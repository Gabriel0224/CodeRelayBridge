/* Remote engine code runs inside an opaque wrapper, outside the panel origin. */
(() => {
  'use strict';
  const script = document.currentScript;
  const channel = script.dataset.channel;
  const panel = script.dataset.parent;
  const apiUrl = script.dataset.api;
  let editor;
  const opaque = script.dataset.opaque === 'true';
  addEventListener('message', event => {
    if (event.source === parent) return;
    const frame = document.querySelector('#editor iframe, iframe');
    if (!frame || event.source !== frame.contentWindow) event.stopImmediatePropagation();
  });
  const send = (type, value) => parent.postMessage({crbOffice: channel, type, value}, panel);
  addEventListener('message', event => {
    if (event.source !== parent || event.origin !== panel || event.data?.crbOffice !== channel) return;
    if (event.data.type === 'destroy') { editor?.destroyEditor(); editor = null; return; }
    if (event.data.type === 'check-close') { editor?.requestClose(); return; }
    if (event.data.type !== 'config' || editor) return;
    const config = event.data.value;
    config.events = {
      onDocumentReady: () => send('ready'),
      onDocumentStateChange: event => send(event.data === true ? 'dirty' : 'synced'),
      onError: event => send('error', String(event.data?.errorDescription || '文档引擎错误').slice(0, 300)),
      onRequestClose: () => send('close')
    };
    try {
      if (opaque) {
        // The pinned engine accepts only its actual child through this owned
        // dispatcher scope. No third-party source is rewritten.
        const scope = {};
        editor = DocsAPI.DocEditor.call(scope, 'editor', config);
        if (typeof scope.frameOrigin !== 'string') throw new Error('unsupported engine interface');
        scope.frameOrigin = 'null';
      } else editor = new DocsAPI.DocEditor('editor', config);
    }
    catch (_) { send('error', '文档编辑器初始化失败'); }
  });
  const loader = document.createElement('script');
  loader.src = apiUrl;
  loader.onload = () => send('config-ready');
  loader.onerror = () => send('error', '无法加载文档引擎，请检查地址、HTTPS 和浏览器连接');
  document.head.append(loader);
})();
