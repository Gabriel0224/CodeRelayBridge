/* CRB-owned compatibility layer for the pinned engine's opaque sandbox. */
(() => {
  'use strict';
  const Original = window.Worker;
  if (!Original) return;
  function Worker(url, options) {
    const source = new URL(String(url), location.href);
    if (source.origin !== location.origin || !source.pathname.startsWith('/office-engine/'))
      throw new DOMException('Unsupported document worker', 'SecurityError');
    const bootstrap = `const base=${JSON.stringify(source.href)};
      const originalFetch=self.fetch.bind(self);
      self.fetch=(url,options)=>originalFetch(typeof url==='string'?new URL(url,base).href:url,options);
      const originalOpen=XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open=function(method,url,...rest){return originalOpen.call(this,method,new URL(String(url),base).href,...rest)};
      const scripts=self.importScripts.bind(self);
      self.importScripts=(...urls)=>scripts(...urls.map(url=>new URL(String(url),base).href));
      self.Module={locateFile:name=>new URL(name,base).href};
      ${options?.type === 'module' ? 'await import(base);' : 'importScripts(base);'}`;
    const blob = URL.createObjectURL(new Blob([bootstrap], {type: 'application/javascript'}));
    try {
      const worker = new Original(blob, options);
      setTimeout(() => URL.revokeObjectURL(blob), 60000);
      return worker;
    } catch (error) { URL.revokeObjectURL(blob); throw error; }
  }
  Worker.prototype = Original.prototype;
  Object.setPrototypeOf(Worker, Original);
  window.Worker = Worker;
})();
