/* Isolated local-web adapter. This file executes only inside the preview. */
(() => {
  'use strict';
  const config = JSON.parse(atob(document.currentScript.dataset.preview));
  const prefix = config.prefix, panel = location.origin, target = new URL(config.target);
  const nativeFetch = window.fetch.bind(window);
  const originalBase = () => {
    const base = new URL(document.baseURI);
    return config.target + (base.pathname.startsWith(prefix + '/') ? base.pathname.slice(prefix.length) : '/') + base.search;
  };
  const map = value => {
    if (value == null || String(value).startsWith('#')) return value;
    if (String(value).startsWith(prefix + '/')) return panel + value;
    let url;
    try { url = new URL(String(value), originalBase()); } catch (_) { return value; }
    if (!['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)) return value;
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) &&
      ({'ws:':'http:','wss:':'https:'}[url.protocol] || url.protocol) === target.protocol &&
      (url.port || (['https:', 'wss:'].includes(url.protocol) ? '443' : '80')) === (target.port || (target.protocol === 'https:' ? '443' : '80'));
    const own = url.host === location.host;
    if (own && url.pathname.startsWith(prefix + '/')) return panel + url.pathname + url.search + url.hash;
    if (local || own) return panel + prefix + url.pathname + url.search + url.hash;
    return url.href;
  };
  let pending = Promise.resolve();
  const update = (kind, data) => {
    pending = pending.catch(() => {}).then(() => nativeFetch(panel + prefix + '/__crb/' + kind, {
      method: 'POST', credentials: 'omit', keepalive: true, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    }));
  };
  window.fetch = async (input, options) => {
    await pending.catch(() => {});
    const request = input instanceof Request ? new Request(map(input.url), input) : map(input);
    return nativeFetch(request, {...options, credentials: 'omit'});
  };
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, async = true, ...rest) {
    this.__crbAsync = async !== false;
    return open.call(this, method, map(url), async, ...rest);
  };
  XMLHttpRequest.prototype.send = function(body) {
    if (this.__crbAsync) { pending.catch(() => {}).then(() => send.call(this, body)); return; }
    return send.call(this, body);
  };
  const Socket = window.WebSocket;
  function WebSocket(url, protocols) {
    const mapped = new URL(map(url));
    if (mapped.protocol === 'https:') mapped.protocol = 'wss:';
    else if (mapped.protocol === 'http:') mapped.protocol = 'ws:';
    return protocols === undefined ? new Socket(mapped.href) : new Socket(mapped.href, protocols);
  }
  Object.setPrototypeOf(WebSocket, Socket); WebSocket.prototype = Socket.prototype; window.WebSocket = WebSocket;
  const Events = window.EventSource;
  if (Events) {
    function EventSource(url) { return new Events(map(url), {withCredentials: false}); }
    Object.setPrototypeOf(EventSource, Events); EventSource.prototype = Events.prototype; window.EventSource = EventSource;
  }
  const attribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {
    return attribute.call(this, name, ['src', 'href', 'action', 'poster', 'formaction'].includes(name.toLowerCase()) ? map(value) : value);
  };
  for (const [type, names] of [[HTMLImageElement, ['src']], [HTMLScriptElement, ['src']], [HTMLLinkElement, ['href']],
    [HTMLAnchorElement, ['href']], [HTMLFormElement, ['action']], [HTMLIFrameElement, ['src']], [HTMLMediaElement, ['src']]]) {
    for (const name of names) {
      const property = Object.getOwnPropertyDescriptor(type.prototype, name);
      if (property?.set) Object.defineProperty(type.prototype, name, {...property, set(value) { property.set.call(this, map(value)); }});
    }
  }
  const opened = window.open;
  const targetUrl = value => {
    try {
      if (String(value).startsWith(prefix + '/')) return new URL(String(value).slice(prefix.length), config.target).href;
      const url = new URL(String(value), originalBase());
      if (url.origin === panel && url.pathname.startsWith(prefix + '/')) {
        return new URL(url.pathname.slice(prefix.length) + url.search + url.hash, config.target).href;
      }
      return url.href;
    } catch (_) { return ''; }
  };
  const localUrl = value => {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) && !url.username && !url.password; }
    catch (_) { return false; }
  };
  const rawLocal = value => {
    if (!localUrl(value)) return false;
    const url = new URL(value);
    return !(url.origin === panel && url.pathname.startsWith(prefix + '/'));
  };
  const inPanel = value => {
    const url = targetUrl(value);
    if (parent === window || !localUrl(url)) return false;
    parent.postMessage({crbLocalPreview: config.id, type: 'open', url}, panel);
    return true;
  };
  window.open = function(url, ...rest) {
    if (inPanel(url)) return null;
    const mapped = map(url);
    // A standalone view cannot grant a capability for a different local port.
    if (rawLocal(mapped)) return null;
    return opened.call(this, mapped, ...rest);
  };
  // innerHTML bypasses href setters. Remap at activation as well, and keep
  // target=_blank / modifier / middle clicks in the panel's shared tab strip.
  const navigate = event => {
    if (event.type === 'auxclick' && event.button !== 1) return;
    const link = event.composedPath().find(item => item instanceof HTMLAnchorElement || item instanceof HTMLAreaElement);
    if (!link) return;
    const raw = link.getAttribute('href');
    if (!raw) return;
    const mapped = map(raw);
    const popup = (link.target && !['_self', '_parent', '_top'].includes(link.target)) || event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1;
    if ((popup || rawLocal(mapped)) && inPanel(raw)) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (rawLocal(mapped)) { event.preventDefault(); return; }
    attribute.call(link, 'href', mapped);
    if (['_parent', '_top'].includes(link.target)) attribute.call(link, 'target', '_self');
  };
  document.addEventListener('click', navigate, true);
  document.addEventListener('auxclick', navigate, true);
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name].bind(history);
    history[name] = (data, title, url) => {
      try { return original(data, title, url == null ? url : map(url)); }
      catch (error) { if (error.name !== 'SecurityError') throw error; }
    };
  }
  const storage = name => {
    const values = {...(config.storage?.[name] || {})};
    return {get length() { return Object.keys(values).length; }, key(index) { return Object.keys(values)[index] ?? null; },
      getItem(key) { return Object.hasOwn(values, key) ? String(values[key]) : null; },
      setItem(key, value) { values[String(key)] = String(value); update('storage', {name, values}); },
      removeItem(key) { delete values[String(key)]; update('storage', {name, values}); },
      clear() { for (const key of Object.keys(values)) delete values[key]; update('storage', {name, values}); }};
  };
  for (const name of ['localStorage', 'sessionStorage']) {
    try { void window[name].length; } catch (_) { Object.defineProperty(window, name, {value: storage(name)}); }
  }
  try {
    let cookie = config.cookie || '';
    Object.defineProperty(document, 'cookie', {configurable: true, get() { return cookie; }, set(value) {
      const row = String(value).split(';')[0], name = row.split('=')[0].trim();
      cookie = cookie.split(';').map(s => s.trim()).filter(s => s && !s.startsWith(name + '=')).concat(row).join('; ');
      update('cookie', {cookie: String(value)});
    }});
  } catch (_) {}
  const WorkerOriginal = window.Worker;
  if (WorkerOriginal) {
    function Worker(url, options) {
      const source = new URL(map(url), location.href).href;
      const bootstrap = `const base=${JSON.stringify(source)},prefix=${JSON.stringify(prefix)},panel=${JSON.stringify(panel)};
        const resolve=url=>{const u=new URL(String(url),base);if(u.origin===panel&&!u.pathname.startsWith(prefix+'/'))u.pathname=prefix+u.pathname;return u.href};
        const fetchOriginal=self.fetch.bind(self);self.fetch=(url,options)=>fetchOriginal(typeof url==='string'?resolve(url):url,{...options,credentials:'omit'});
        const scripts=self.importScripts.bind(self);self.importScripts=(...urls)=>scripts(...urls.map(resolve));
        if(typeof XMLHttpRequest!=='undefined'){const open=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(method,url,...rest){return open.call(this,method,resolve(url),...rest)}}
        self.Module=self.Module||{locateFile:name=>resolve(name)};
        ${options?.type === 'module' ? 'await import(base)' : 'importScripts(base)'};`;
      const blob = URL.createObjectURL(new Blob([bootstrap], {type: 'application/javascript'}));
      try { const worker = new WorkerOriginal(blob, options); setTimeout(() => URL.revokeObjectURL(blob), 60000); return worker; }
      catch (error) { URL.revokeObjectURL(blob); throw error; }
    }
    Worker.prototype = WorkerOriginal.prototype; Object.setPrototypeOf(Worker, WorkerOriginal); window.Worker = Worker;
  }
  if (parent === window) {
    const lease = new URL(panel + prefix + '/__crb/lease'); lease.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const connection = new Socket(lease.href);
    addEventListener('pagehide', () => connection.close(), {once: true});
  } else {
    addEventListener('DOMContentLoaded', () => parent.postMessage({crbLocalPreview: config.id, type: 'ready', url: targetUrl(location.href)}, panel), {once: true});
  }
})();
