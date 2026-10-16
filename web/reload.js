/* Keep recovery independent of chat initialization, watches and API requests. */
(() => {
  'use strict';
  const parameter = '_crb_reload';
  const current = new URL(window.location.href);
  if (current.searchParams.has(parameter)) {
    current.searchParams.delete(parameter);
    // The document already contains the one-shot asset URLs. Do not make
    // ordinary navigation or the next browser refresh bypass caches forever.
    try { window.history.replaceState(window.history.state, '', current.href); } catch (_) {}
  }
  const recovery = document.getElementById('pageReloadRecovery');
  const workspace = document.getElementById('workspace');
  const login = document.getElementById('login');
  const hideRecovery = () => {
    if (recovery && (!workspace?.hidden || login && !login.hidden)) recovery.hidden = true;
  };
  if (recovery && workspace) {
    new MutationObserver(hideRecovery).observe(workspace, {attributes: true, attributeFilter: ['hidden']});
    if (login) new MutationObserver(hideRecovery).observe(login, {attributes: true, attributeFilter: ['hidden']});
    setTimeout(() => {
      recovery.hidden = !workspace.hidden || Boolean(login && !login.hidden);
    }, 12000);
  }
  let navigating = false;
  document.addEventListener('click', event => {
    if (!event.target.closest?.('#reloadPage,#reloadPageFallback') || navigating) return;
    const hasDraft = document.querySelector('#message')?.value.trim();
    const hasAttachments = document.querySelector('#uploadTray:not([hidden]) .pending-upload');
    const hasEditor = document.querySelector('.file-editor textarea,.queue-editor,.sent-edit-panel textarea');
    if ((hasDraft || hasAttachments || hasEditor) && !window.confirm(
      '重载前请保存未发送内容和文件修改。继续重新加载整个网页？')) return;
    navigating = true;
    const target = new URL(window.location.href);
    target.searchParams.set(parameter, Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
    // A new navigation, not an API refresh or a nonstandard reload(true).
    // Preserve the route, other query parameters, login and local data.
    window.location.replace(target.href);
  });
})();
