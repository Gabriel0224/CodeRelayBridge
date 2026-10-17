/* UI-only in-place localization. Never translate user payloads or call a model. */
(() => {
  'use strict';
  const catalog = window.CRB_UI_CATALOG || {messages: {}, templates: []};
  const key = 'codexRelayLanguage';
  const supported = new Set(['zh', 'en']);
  const systemLanguage = navigator.languages?.[0] || navigator.language || '';
  let selected;
  try { selected = localStorage.getItem(key); } catch (_) {}
  let language = supported.has(selected) ? selected : /^zh(?:$|[-_])/i.test(systemLanguage) ? 'zh' : 'en';
  let templates;
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function patterns() {
    if (templates) return templates;
    templates = catalog.templates.map(([source, target]) => {
      const seen = new Set();
      const pattern = source.split(/(\{\d+\})/).map(part => {
        if (!/^\{\d+\}$/.test(part)) return escape(part);
        const name = 'v' + part.slice(1, -1);
        if (seen.has(name)) return `\\k<${name}>`;
        seen.add(name); return `(?<${name}>[\\s\\S]*?)`;
      }).join('');
      return [new RegExp('^' + pattern + '$'), target];
    });
    return templates;
  }
  function t(value) {
    if (typeof value !== 'string' || language === 'zh') return value;
    if (Object.hasOwn(catalog.messages, value)) return catalog.messages[value];
    const trimmed = value.trim(), prefix = value.slice(0, value.length - value.trimStart().length), suffix = value.slice(value.trimEnd().length);
    if (Object.hasOwn(catalog.messages, trimmed)) return prefix + catalog.messages[trimmed] + suffix;
    for (const [pattern, target] of patterns()) {
      const match = pattern.exec(trimmed);
      if (match) return prefix + target.replace(/\{(\d+)\}/g, (_, id) => match.groups['v' + id]) + suffix;
    }
    const parts = value.split(/(\n| · |：|；)/);
    if (parts.length > 1) return parts.map(part => part === '：' ? ': ' : part === '；' ? '; ' : part === '\n' || part === ' · ' ? part : t(part)).join('');
    return value;
  }
  const protectedSelector = '[data-i18n-ignore],pre,code,textarea,.xterm,.thread-name,.project-title,' +
    '.workspace-thread-computer,.workspace-thread-project,#title,#subtitle,#threadContextTarget,#projectContextTarget,' +
    '#brandEditTrigger,.brand-label,#filePreviewTitle,#filePreviewMeta,.file-preview-tab-label,.file-source-text,' +
    '.file-markdown,.preview-table,.file-row-details strong,.file-tree-label,.attachment-info strong,' +
    '.pending-upload-info strong,.queue-text,.async-choice-option,.tool-primary,.tool-detail,' +
    '.native-card>strong,.native-tool-row>strong,.native-tool-row details p,.native-tool-row label,' +
    '.native-field input,.native-metadata dd';
  const uiWithinContent = 'button,.text-box-actions,.attachment-actions,.attachment-loading,.attachment-download,' +
    '.attachment-info small,.attachment-info button,.scheduled-instructions>summary';
  function protectedNode(node) {
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!element || element.closest('script,style,svg')) return true;
    if (element.closest(protectedSelector)) return true;
    return Boolean(element.closest('.content') && !element.closest(uiWithinContent));
  }
  const records = new WeakMap();
  const tracked = new Set();
  const translated = (node, source) => (node.nodeType === Node.TEXT_NODE ? node.parentElement : node)
    ?.matches('label[for="application"]') && source === '应用' && language === 'en' ? 'Application' : t(source);
  function bind(node, property, source, setter) {
    let fields = records.get(node);
    if (!fields) { fields = new Map(); records.set(node, fields); tracked.add(node); }
    const prior = fields.get(property);
    if (prior && source === prior.output) return;
    const output = translated(node, source);
    fields.set(property, {source, output, setter});
    if (source !== output) setter(output);
  }
  function visit(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (!protectedNode(root) && (records.has(root) || /[\u3400-\u9fff]/.test(root.data))) bind(root, 'text', root.data, value => {root.data = value;});
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE && root.matches('.content,.file-markdown')) {
      // Do not traverse every token of large histories/documents on streaming
      // updates. Only the panel-owned controls inside this opaque data surface.
      for (const control of root.querySelectorAll(uiWithinContent)) visit(control);
      return;
    }
    if (root.nodeType === Node.ELEMENT_NODE && (!protectedNode(root) ||
        root.matches('input,textarea') && !root.closest('[data-i18n-ignore]'))) {
      for (const property of ['title', 'aria-label', 'placeholder']) {
        const value = root.getAttribute(property);
        if (value && /[\u3400-\u9fff]/.test(value)) bind(root, property, value, text => root.setAttribute(property, text));
      }
    }
    if (root.nodeType === Node.ELEMENT_NODE && root.matches('textarea,script,style,pre,code,.xterm,[data-i18n-ignore]')) return;
    for (const child of root.childNodes) visit(child);
  }
  let observer;
  function startObserver() {
    observer = new MutationObserver(changes => {
      const roots = new Set();
      for (const change of changes) {
        if (change.type === 'childList') for (const child of change.addedNodes) roots.add(child);
        else roots.add(change.target);
      }
      for (const root of roots) visit(root);
      for (const node of tracked) if (!node.isConnected) tracked.delete(node);
    });
    observer.observe(document.body, {subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ['title', 'aria-label', 'placeholder']});
  }
  function setLanguage(value, remember = true) {
    if (!supported.has(value)) return false;
    if (remember) {
      try { localStorage.setItem(key, value); }
      catch (_) { return false; }
    }
    observer?.disconnect();
    language = value;
    document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en';
    for (const node of tracked) {
      if (!node.isConnected || protectedNode(node) && !(node instanceof Element && node.matches('input,textarea') && !node.closest('[data-i18n-ignore]'))) {tracked.delete(node); continue;}
      for (const field of records.get(node).values()) {
        field.output = translated(node, field.source);
        field.setter(field.output);
      }
    }
    document.querySelectorAll('[data-language-option]').forEach(option => {
      option.setAttribute('aria-checked', String(option.dataset.languageOption === value));
    });
    document.querySelectorAll('[data-language-name]').forEach(label => {
      label.textContent = value === 'zh' ? '中文' : 'English';
    });
    visit(document.body);
    startObserver();
    window.dispatchEvent(new CustomEvent('crb-language-change', {detail: {language: value}}));
    return true;
  }
  for (const name of ['alert', 'confirm', 'prompt']) {
    const original = window[name].bind(window);
    window[name] = (message, ...args) => original(t(message), ...args);
  }
  window.CRBI18n = {t, setLanguage, get language() {return language;},
    originalText(node) {return records.get(node.firstChild)?.get('text')?.source || node.textContent;},
    register(messages, rows = []) {Object.assign(catalog.messages, messages); catalog.templates.push(...rows); templates = null;},
    refresh: () => visit(document.body)};
  function ready() {
    setLanguage(language, false);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, {once: true});
  else ready();
})();
