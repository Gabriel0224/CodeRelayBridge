/* ONLYOFFICE adapter. A confirmed client receipt is the only saved signal. */
(() => {
  'use strict';
  const sessions = new Set();
  const pending = new Set(['assembling', 'pending', 'saving']);
  const failed = new Set(['failed', 'conflict']);
  const translate = text => window.CRBI18n?.t?.(text) || text;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const random = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
  let viewer;
  try { viewer = sessionStorage.getItem('crbOfficeViewer'); } catch (_) {}
  if (!/^[0-9a-f]{32}$/.test(viewer || '')) {
    viewer = random();
    try { sessionStorage.setItem('crbOfficeViewer', viewer); } catch (_) {}
  }
  async function request(path, value) {
    const response = await fetch(path, {method: value ? 'POST' : 'GET', credentials: 'same-origin',
      headers: value ? {'Content-Type': 'application/json'} : {}, body: value ? JSON.stringify(value) : undefined});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }
  function label(session, state) {
    if (state.error) return state.error;
    if (pending.has(state.state)) return '正在保存到目标机…';
    if (state.state === 'saved') return '已保存到目标机';
    if (!session.editable) return state.readOnlyReason || '只读文档；现代格式 docx、xlsx、pptx 支持原格式编辑';
    return session.dirty ? '有修改尚未保存到目标机' : '文档已打开';
  }
  function update(session, state) {
    session.state = {...session.state, ...state};
    if (state.state === 'saved' && state.savedSequence >= session.savingSequence) {
      // A new change may have arrived while an earlier snapshot was in flight.
      session.dirty = session.revision !== session.savingRevision;
    }
    session.options.status(translate(label(session, session.state)));
    session.options.changed();
  }
  async function status(session) {
    const headers = session.etag ? {'If-None-Match': session.etag} : {};
    const response = await fetch('/api/office/status?id=' + session.id, {credentials: 'same-origin', headers});
    if (response.status === 304) return session.state;
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    session.etag = response.headers.get('ETag'); update(session, data); return data;
  }
  async function save(session) {
    if (session.saving) return session.saving;
    session.saving = (async () => {
      const syncUntil = Date.now() + 20000;
      while (session.enginePending && Date.now() < syncUntil) await pause(100);
      if (session.enginePending) throw new Error('修改仍在同步到文档引擎，请保留窗口后重试');
      const revision = session.revision;
      let state = await request('/api/office/save', {id: session.id,
        fresh: session.state?.state === 'conflict' && revision !== session.savingRevision});
      if (!session.saveRevisions.has(state.sequence)) session.saveRevisions.set(state.sequence, revision);
      session.savingRevision = session.saveRevisions.get(state.sequence);
      session.savingSequence = state.sequence; update(session, state);
      const until = Date.now() + 100000;
      while (pending.has(state.state) && Date.now() < until && !session.destroyed) {
        await pause(1500); state = await status(session);
      }
      if (failed.has(state.state)) throw new Error(state.error || '保存失败，编辑副本已保留');
      if (pending.has(state.state)) throw new Error('保存尚未确认，请保留窗口后重试');
      if (state.state === 'editing') {
        // CommandService error 4 means the engine has no changes to assemble.
        if (session.dirty && !state.savedSha256) throw new Error('引擎尚未确认可保存的修改，请保留窗口后重试');
        session.dirty = session.revision !== session.savingRevision;
      }
      return !session.dirty;
    })();
    session.options.changed();
    try { return await session.saving; }
    catch (error) { session.options.status(error.message); throw error; }
    finally { session.saving = null; session.options.changed(); }
  }
  async function open(tab, options) {
    const result = await request('/api/office/open', {host: tab.hostId, path: tab.sourcePath, name: tab.name, viewOnly: tab.officeViewOnly === true, viewer,
      language: document.documentElement.lang, theme: document.documentElement.dataset.theme || 'light'});
    if (tab.closed) { await request('/api/office/close', {id: result.id}); return; }
    const session = {id: result.id, editable: result.editable, options, dirty: false, revision: 0,
      savingRevision: 0, savingSequence: 0, saveRevisions: new Map(), state: result.status, channel: random(), destroyed: false,
      ready: Boolean(result.recoveryOnly), enginePending: false, closeApproved: false};
    tab.office = session; sessions.add(session); tab.pane.replaceChildren();
    if (result.recoveryOnly) {
      const text = document.createElement('p');
      text.textContent = translate('上次文档回存尚未完成。可重试保存，或下载编辑副本后处理。');
      tab.pane.append(text); update(session, result.status); return;
    }
    const api = new URL(result.apiUrl);
    if (!['http:', 'https:'].includes(api.protocol)) throw new Error('文档引擎地址无效');
    const managed = result.opaqueGateway === true && api.origin === location.origin && api.pathname === '/office-engine/web-apps/apps/api/documents/api.js';
    if (api.origin === location.origin && !managed) throw new Error('文档引擎须使用与面板不同的域名或端口');
    if (location.protocol === 'https:' && api.protocol !== 'https:') throw new Error('HTTPS 面板需要 HTTPS 文档引擎');
    const frame = document.createElement('iframe');
    frame.className = 'office-editor-frame'; frame.title = tab.name;
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-downloads' + (managed ? '' : ' allow-same-origin'));
    const wrapper = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<link rel="stylesheet" href="' + location.origin + '/lite/style.css"></head><body class="office-frame-body"><div id="editor"></div>' +
      '<script src="' + location.origin + '/lite/office-frame.js" data-api="' + api.href.replaceAll('&', '&amp;').replaceAll('"', '&quot;') +
      '" data-opaque="' + (managed ? 'true' : 'false') + '" data-parent="' + location.origin + '" data-channel="' + session.channel + '"></script></body></html>';
    // data: has a unique opaque origin even with allow-same-origin. The remote
    // engine cannot acquire the panel origin or inspect its cookies/DOM.
    frame.src = 'data:text/html;base64,' + btoa(unescape(encodeURIComponent(wrapper)));
    session.frame = frame;
    session.listener = event => {
      if (event.source !== frame.contentWindow || event.origin !== 'null' || event.data?.crbOffice !== session.channel) return;
      const {type, value} = event.data;
      if (type === 'config-ready') frame.contentWindow.postMessage({crbOffice: session.channel, type: 'config', value: result.config}, '*');
      else if (type === 'dirty') { session.closeApproved = false; session.enginePending = true; session.dirty = true; session.revision++; update(session, {state: 'editing'}); }
      else if (type === 'synced') session.enginePending = false;
      else if (type === 'ready') { session.ready = true; update(session, result.status); }
      else if (type === 'error') options.status(String(value).slice(0, 300));
      else if (type === 'close') { session.closeApproved = true; options.close(session.retainOnClose === true); }
    };
    addEventListener('message', session.listener); tab.pane.append(frame); update(session, result.status);
  }
  async function close(session, retain = false) {
    if (!session) return true;
    if (session.dirty || pending.has(session.state?.state) || failed.has(session.state?.state)) {
      try { if (!await save(session)) return false; }
      catch (_) {
        if (!retain || session.revision !== session.savingRevision || !failed.has(session.state?.state)) return false;
      }
    }
    if (session.frame && session.ready && !session.closeApproved) {
      session.retainOnClose = retain;
      session.frame.contentWindow.postMessage({crbOffice: session.channel, type: 'check-close'}, '*');
      return false; // onRequestClose continues only after the engine's own check.
    }
    await request('/api/office/close', {id: session.id, retain});
    session.frame?.contentWindow.postMessage({crbOffice: session.channel, type: 'destroy'}, '*');
    session.destroyed = true; removeEventListener('message', session.listener); sessions.delete(session);
    return true;
  }
  addEventListener('beforeunload', event => {
    if ([...sessions].some(s => s.dirty || s.saving || pending.has(s.state?.state) || failed.has(s.state?.state))) {
      event.preventDefault(); event.returnValue = '';
    }
  });
  window.CRBOffice = {open, save, close, download(session) {
    const link = document.createElement('a'); link.href = '/api/office/download?id=' + session.id; link.click();
  }};
})();
