/* Panel-owned tabs; target scripts execute only in an opaque sandbox. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const text = value => window.CRBI18n?.t?.(value) || value;
  const local = value => {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) && !url.username && !url.password; }
    catch (_) { return false; }
  };
  function normalize(value) {
    let url = String(value || '').trim();
    if (/^\d{1,5}$/.test(url)) url = 'http://127.0.0.1:' + url + '/';
    else if (/^(localhost|127\.0\.0\.1|\[::1\]):/.test(url)) url = 'http://' + url;
    if (!local(url)) throw new Error(text('请输入目标电脑的本机网页地址'));
    return new URL(url).href;
  }
  async function request(path, value, keepalive = false) {
    const response = await fetch(path, {method: 'POST', credentials: 'same-origin', keepalive,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
    const result = await response.json(); if (!response.ok) throw new Error(result.error || response.statusText); return result;
  }
  function hosts(preferred) {
    const select = byId('localPreviewHost'); select.replaceChildren();
    const source = typeof catalog !== 'undefined' ? catalog : [];
    for (const host of source) {
      const option = document.createElement('option'); option.value = host.id;
      option.textContent = (host.label || host.id) + (host.online ? '' : ' · ' + text('离线'));
      select.append(option);
    }
    if (preferred && ![...select.options].some(option => option.value === preferred)) {
      const option = document.createElement('option'); option.value = preferred; option.textContent = preferred; select.append(option);
    }
    if (preferred) select.value = preferred;
  }
  function activate(tab) {
    const web = tab.kind === 'web';
    byId('localPreviewForm').hidden = !web;
    byId('localPreviewActions').hidden = !web;
    if (!web) return;
    hosts(tab.hostId); byId('localPreviewUrl').value = tab.webUrl || '';
    byId('localPreviewReload').disabled = !tab.webUrl;
    byId('localPreviewWindow').disabled = !tab.web;
  }
  function release(tab) {
    tab.webGeneration = (tab.webGeneration || 0) + 1;
    const previous = tab.web; tab.web = null; tab.pane.replaceChildren();
    if (previous) {
      previous.lease?.close();
      void request('/api/local-preview/close', {id: previous.id}, true).catch(() => {});
    }
  }
  async function launch(tab, host, value) {
    const url = normalize(value);
    if (!host) throw new Error(text('请选择目标电脑'));
    release(tab); const generation = tab.webGeneration;
    tab.hostId = host; tab.webUrl = url; tab.key = `web/${host}/${url}`;
    tab.name = new URL(url).host;
    const hostName = (typeof catalog !== 'undefined' ? catalog : []).find(item => item.id === host)?.label || host;
    tab.meta = hostName + ' · ' + url;
    setPreviewStatus(tab, text('正在连接目标电脑的本地网页…'));
    if (activeFilePreview()?.id === tab.id) activateFilePreview(tab.id);
    else renderFilePreviewTabs();
    const result = await request('/api/local-preview/open', {host, url});
    if (tab.closed || generation !== tab.webGeneration) { await request('/api/local-preview/close', {id: result.id}, true).catch(() => {}); return; }
    const frame = document.createElement('iframe'); frame.title = text('目标电脑的本地网页');
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-downloads');
    frame.referrerPolicy = 'no-referrer';
    tab.web = {...result, frame};
    const leaseUrl = new URL('/local-preview/' + result.id + '/__crb/lease', location.origin);
    leaseUrl.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const lease = new WebSocket(leaseUrl); tab.web.lease = lease;
    lease.onclose = () => { if (tab.web?.id === result.id) setPreviewStatus(tab, text('预览连接已断开，可刷新重连')); };
    frame.src = result.previewUrl; tab.pane.append(frame);
    if (activeFilePreview()?.id === tab.id) activate(tab);
  }
  async function open(host, value = '') {
    let url;
    try { url = value ? normalize(value) : ''; }
    catch (error) { url = ''; }
    const {tab, existing} = beginFilePreview(`web/${host}/${url}`, text('本地网页'), '', null, 'web');
    tab.pane.classList.add('web-preview-pane'); tab.hostId = host;
    activateFilePreview(tab.id);
    if (existing && tab.web) return;
    if (!url) {
      tab.pane.textContent = text('请输入目标电脑的本机网页地址');
      byId('localPreviewUrl').value = value; byId('localPreviewUrl').focus(); return;
    }
    try { await launch(tab, host, url); } catch (error) { setPreviewStatus(tab, error.message); }
  }
  function textLinks(value, host) {
    const urls = [...new Set((value.match(/https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/[^\s<>"'`)]*)?/g) || []).filter(local))].slice(0, 8);
    if (!urls.length) return null;
    const controls = document.createElement('div'); controls.className = 'local-web-links';
    for (const url of urls) {
      const button = document.createElement('button'); button.className = 'secondary'; button.type = 'button';
      button.textContent = text('预览本地网页') + ' · ' + new URL(url).host;
      button.onclick = () => { void open(host, url); }; controls.append(button);
    }
    return controls;
  }
  function bind(target, host, thread) {
    target.dataset.localPreviewHost = host || '';
    for (const pre of target.querySelectorAll('pre')) {
      if (pre.dataset.localPreviewBound) continue;
      pre.dataset.localPreviewBound = 'true';
      const controls = textLinks(pre.textContent, host);
      if (controls) pre.after(controls);
    }
  }
  // Delegation also covers links added after Markdown hydration. A message or
  // file pane's recorded host takes precedence over the currently selected chat.
  function clicked(event) {
    if (event.type === 'auxclick' && event.button !== 1) return;
    const link = event.target.closest?.('a[href]');
    if (!link || !local(link.href)) return;
    const owner = link.closest('[data-local-preview-host]');
    const host = owner?.dataset.localPreviewHost || (typeof currentHost === 'function' ? currentHost()?.id : '');
    event.preventDefault(); event.stopImmediatePropagation(); void open(host, link.href);
  }
  document.addEventListener('click', clicked, true);
  document.addEventListener('auxclick', clicked, true);
  addEventListener('message', event => {
    const tab = typeof filePreviewTabs !== 'undefined' && filePreviewTabs.find(item => item.web?.id === event.data?.crbLocalPreview && event.source === item.web.frame.contentWindow);
    if (!tab || event.origin !== 'null') return;
    if (event.data.type === 'ready') {
      const url = event.data.url;
      if (local(url) && new URL(url).origin === new URL(tab.webUrl).origin) {
        tab.webUrl = url; tab.key = `web/${tab.hostId}/${url}`;
        const parsed = new URL(url);
        tab.web.previewUrl = new URL('/local-preview/' + tab.web.id + parsed.pathname + parsed.search + parsed.hash, location.origin).href;
        tab.meta = tab.meta.split(' · ')[0] + ' · ' + url;
        if (activeFilePreview()?.id === tab.id) { byId('localPreviewUrl').value = url; byId('filePreviewMeta').textContent = tab.meta; }
      }
      setPreviewStatus(tab, text('本地网页已打开'));
    }
    if (event.data.type === 'open' && local(event.data.url) && navigator.userActivation.isActive) void open(tab.hostId, event.data.url);
  });
  addEventListener('pagehide', () => { for (const tab of filePreviewTabs) if (tab.kind === 'web') release(tab); });
  byId('localPreviewToggle').onclick = byId('filePreviewWeb').onclick = () => { void open(typeof currentHost === 'function' ? currentHost()?.id : ''); };
  byId('localPreviewForm').onsubmit = event => {
    event.preventDefault(); const tab = activeFilePreview(); if (tab?.kind !== 'web') return;
    void launch(tab, byId('localPreviewHost').value, byId('localPreviewUrl').value).catch(error => setPreviewStatus(tab, error.message));
  };
  byId('localPreviewReload').onclick = () => {
    const tab = activeFilePreview(); if (tab?.kind !== 'web' || !tab.webUrl) return;
    if (tab.web?.lease?.readyState === WebSocket.OPEN) { setPreviewStatus(tab, text('正在刷新本地网页…')); tab.web.frame.src = tab.web.previewUrl; }
    else void launch(tab, tab.hostId, tab.webUrl).catch(error => setPreviewStatus(tab, error.message));
  };
  byId('localPreviewWindow').onclick = async () => {
    const tab = activeFilePreview(); if (!tab?.web) return;
    const popup = window.open('about:blank', '_blank');
    if (!popup) { setPreviewStatus(tab, text('浏览器阻止了新窗口，请允许弹出窗口后重试')); return; }
    popup.opener = null;
    try {
      const result = await request('/api/local-preview/open', {host: tab.hostId, url: tab.webUrl});
      if (popup.closed) { await request('/api/local-preview/close', {id: result.id}, true); return; }
      popup.location.replace(result.previewUrl);
    } catch (error) { popup.close(); setPreviewStatus(tab, error.message); }
  };
  window.CRBLocalPreview = {open, bind, isLocal: local, activate, release, textLinks};
})();
