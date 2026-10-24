/* On-demand native sidebar. Every mutation is confirmed by the original App. */
(() => {
  'use strict';
  const cache = new Map(), freshness = new Map(), loads = new Map(), attempted = new Set(), connected = new Map(), scopes = new Map(), epochs = new Map();
  let generation = relayGeneration;
  let changing = false;
  const node = (tag, text, cls = '') => {
    const n = document.createElement(tag); if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls; return n;
  };
  const button = (label, fn, id = '') => {
    const n = node('button', label); n.type = 'button'; if (id) n.id = id; n.onclick = fn; return n;
  };
  const supported = host => applicationId(host) === 'codex' && host?.capabilities?.nativeSidebar === true;
  const state = host => cache.get(host?.id);
  const caller = (host, thread = '') => thread || host.threads?.find(row => !row.unavailable && !row.isSubagent)?.id || host.threads?.find(row => !row.unavailable)?.id || '';
  const report = value => { $('sendResult').textContent = value; };
  function remember(host, value, info = null) {
    if (!value?.sections || !value.revision) throw new Error('原 App 分区结果无效');
    cache.set(host.id, {...value, threadEmojis: {...state(host)?.threadEmojis, ...value.threadEmojis}}); lastCatalog = ''; renderCatalog({preload: false});
    freshness.delete(host.id);
    if (info?.revision && Number.isFinite(info.maxAgeMs)) freshness.set(host.id, {
      revision: info.revision, expires: Date.now() + Math.min(60000, Math.max(0, info.maxAgeMs))});
    if (cache.size > 24) { const oldest = cache.keys().next().value; cache.delete(oldest); freshness.delete(oldest); attempted.delete(oldest); }
  }
  async function load(host, force = false, thread = '', refreshApp = false) {
    if (!supported(host) || !host.online || !host.desktopConnected) return null;
    if (loads.has(host.id)) {
      if (!refreshApp) return loads.get(host.id);
      await loads.get(host.id).catch(() => {});
      return load(host, true, thread, true);
    }
    const remembered = freshness.get(host.id);
    if (!refreshApp && state(host) && remembered?.expires > Date.now() &&
        (!thread || Object.hasOwn(state(host).threadEmojis || {}, thread))) return state(host);
    if (!force && (cache.has(host.id) || attempted.has(host.id))) return state(host);
    const context = caller(host, thread);
    if (!context) { if (force) throw new Error(uiText('暂无可读取的对话上下文，请先新建对话后刷新分区')); return null; }
    attempted.add(host.id);
    const epoch = epochs.get(host.id) || 0;
    const conditional = host.capabilities?.nativeReadCache === true;
    const promise = issueCommand('native_read', {host: host.id, thread: context, payload: {operation: 'sidebar', emojiThreadId: thread || null,
      ...(conditional ? {cache: true, refresh: refreshApp, ...(remembered && state(host) ? {ifRevision: remembered.revision} : {})} : {})}})
      .then(result => {
        if ((epochs.get(host.id) || 0) !== epoch) return null;
        if (result.notModified && !state(host)) throw new Error('原 App 分区结果无效');
        remember(host, result.notModified ? state(host) : result.sidebar, result.readCache); return state(host);
      })
      .catch(error => { if ((epochs.get(host.id) || 0) === epoch) freshness.delete(host.id); if (force) throw error; return null; })
      .finally(() => { if (loads.get(host.id) === promise) loads.delete(host.id); });
    loads.set(host.id, promise); return promise;
  }
  async function mutate(host, operation, payload = {}, thread = '') {
    if (changing || busy) return null;
    changing = true;
    closeThreadContextMenu(); closeProjectContextMenu();
    try {
      const snapshot = state(host) || await load(host, true);
      if (!snapshot) throw new Error('请先连接目标 Codex Desktop');
      epochs.set(host.id, (epochs.get(host.id) || 0) + 1);
      const result = await issueCommand('native_action', {host: host.id, thread: caller(host, thread),
        payload: {operation, revision: snapshot.revision, ...payload}});
      if (result.sidebar) remember(host, result.sidebar);
      report(result.warning || '✓ 已由原 App 保存');
      await refresh(); return result;
    } catch (error) { report(error.message); await load(host, true, '', true).catch(() => {}); return null; }
    finally { changing = false; }
  }
  const settings = node('dialog', undefined, 'native-settings-dialog');
  settings.id = 'sidebarSettingsDialog';
  const header = node('div', undefined, 'native-header');
  header.append(node('strong', '分区与排序'), button('关闭', () => settings.close()));
  const body = node('div', undefined, 'sidebar-settings-body'); settings.append(header, body); document.body.append(settings);
  let settingsHost = '';
  const field = (label, rows, value, action) => {
    const wrap = node('label', label, 'native-field'), select = node('select');
    for (const [id, title] of rows) { const o = node('option', title); o.value = id; select.append(o); }
    select.value = value; select.onchange = () => action(select.value); wrap.append(select); return wrap;
  };
  async function createSection(host, item = null) {
    const value = window.prompt(uiText('分区名称'));
    if (!value?.trim()) return;
    const before = new Set((state(host)?.sections || []).map(row => row.id));
    const result = await mutate(host, 'sidebar_section_create', {name: value.trim()});
    const added = result?.sidebar?.sections.filter(row => !before.has(row.id));
    if (item && added?.length === 1) await mutate(host, item.type === 'thread' ? 'sidebar_move_thread' : 'sidebar_move_project',
      {sectionId: added[0].id, ...(item.type === 'project' ? {projectId: item.id} : {})}, item.type === 'thread' ? item.id : '');
    if (settings.open) drawSettings(host);
  }
  async function shiftSection(host, id, delta) {
    const ids = state(host).sections.map(s => s.id), index = ids.indexOf(id), next = index + delta;
    if (index < 0 || next < 0 || next >= ids.length) return;
    [ids[index], ids[next]] = [ids[next], ids[index]];
    await mutate(host, 'sidebar_reorder_sections', {sectionIds: ids}); drawSettings(host);
  }
  function drawSettings(host) {
    const snapshot = state(host); body.replaceChildren();
    if (!snapshot) { body.append(node('p', '原 App 分区暂不可用')); return; }
    body.append(button('新建分区', () => createSection(host)), button('从原 App 刷新', async () => {
      body.setAttribute('aria-busy', 'true');
      try { await load(host, true, '', true); if (settings.open && settingsHost === host.id) drawSettings(host); } catch (error) { report(error.message); }
      finally { if (settingsHost === host.id) body.removeAttribute('aria-busy'); }
    }));
    body.append(field('分组方式', [['project', '按项目'], ['connection', '按连接'], ['list', '平铺']], snapshot.preferences.grouping?.mode || 'project',
      async grouping => { await mutate(host, 'sidebar_preferences', {grouping}); drawSettings(host); }));
    for (const [key, label] of [['chats', '对话排序'], ['pinned', '置顶排序']]) {
      body.append(field(label, [['updated_at', '最近更新'], ['manual', '手动']], snapshot.preferences.sorting?.[key] || 'updated_at',
        async value => { await mutate(host, 'sidebar_preferences', {sorting: {[key]: value}}); drawSettings(host); }));
    }
    snapshot.sections.forEach((section, index) => {
      const row = node('div', undefined, 'native-card sidebar-section-editor');
      const sectionName = node('span', title(section), 'sidebar-section-name');
      if (section.custom) sectionName.setAttribute('data-i18n-ignore', '');
      row.append(sectionName);
      const up = button('上移', () => shiftSection(host, section.id, -1)); up.disabled = index === 0;
      const down = button('下移', () => shiftSection(host, section.id, 1)); down.disabled = index === snapshot.sections.length - 1;
      row.append(up, down);
      if (section.custom) {
        row.append(button('重命名分区', async () => {
          const value = window.prompt(uiText('分区名称'), section.name); if (!value?.trim()) return;
          await mutate(host, 'sidebar_section_rename', {sectionId: section.id, name: value.trim()}); drawSettings(host);
        }), button('删除分区', async () => {
          if (!window.confirm(uiText('删除这个分区？其中的项目和对话会保留。'))) return;
          await mutate(host, 'sidebar_section_delete', {sectionId: section.id}); drawSettings(host);
        }));
      }
      body.append(row);
    });
  }
  async function openSettings(host) {
    if (!supported(host)) return;
    settingsHost = host.id; settings.showModal();
    if (state(host)) drawSettings(host); else body.replaceChildren(node('p', '正在读取原 App 分区…'));
    body.setAttribute('aria-busy', 'true');
    try { await load(host, true); if (settings.open && settingsHost === host.id) drawSettings(host); }
    catch (error) { if (settings.open && settingsHost === host.id) { if (state(host)) report(error.message); else body.replaceChildren(node('p', error.message)); } }
    finally { if (settingsHost === host.id) body.removeAttribute('aria-busy'); }
  }
  const manage = button('分区与排序', () => openSettings(currentHost()), 'sidebarManage');
  manage.className = 'sidebar-action-button sidebar-manage'; manage.hidden = true;
  manage.replaceChildren(node('span', manage.textContent));
  $('sidebar').querySelector('.sidebar-primary-actions').prepend(manage);
  const title = section => section.custom ? section.name :
    ({pinned: '置顶', threads: '项目', projects: '项目', chats: '对话', orbit: 'Your dot', agents: 'Agents'}[section.id] || section.name);
  function itemSection(host, type, id) {
    return state(host)?.sections.find(s => (type === 'thread' ? s.threadIds : s.projectIds).includes(id));
  }
  function moveControl(host, type, id) {
    const section = itemSection(host, type, id), wrap = node('label', '移至分区'), select = node('select');
    select.setAttribute('aria-label', uiText('移至分区'));
    const rows = [['', '默认位置'], ...(state(host)?.sections || []).filter(s => s.custom || s.id === 'pinned').map(s => [s.id, title(s)])];
    for (const [value, text] of rows) { const option = node('option', text); option.value = value;
      if (state(host)?.sections.some(s => s.id === value && s.custom)) option.setAttribute('data-i18n-ignore', '');
      select.append(option); }
    select.value = section?.custom || section?.id === 'pinned' ? section.id : '';
    select.onchange = () => mutate(host, type === 'thread' ? 'sidebar_move_thread' : 'sidebar_move_project',
      {sectionId: select.value || null, ...(type === 'project' ? {projectId: id} : {})}, type === 'thread' ? id : '');
    wrap.append(select); return wrap;
  }
  async function shiftItem(host, type, id, delta) {
    const section = itemSection(host, type, id); if (!section) return;
    const order = [...(type === 'thread' ? section.threadIds : section.projectIds)];
    const index = order.indexOf(id), next = index + delta; if (index < 0 || next < 0 || next >= order.length) return;
    [order[index], order[next]] = [order[next], order[index]];
    const operation = type === 'thread' ? 'sidebar_reorder_threads' : section.custom ? 'sidebar_reorder_items' : 'sidebar_reorder_projects';
    await mutate(host, operation, type === 'thread' ? {sectionId: section.id, threadIds: order} : {sectionId: section.id, projectIds: order});
  }
  const threadExtras = node('div', undefined, 'sidebar-menu-extra');
  $('threadContextTarget').after(threadExtras);
  const projectExtras = node('div', undefined, 'sidebar-menu-extra');
  $('projectContextTarget').after(projectExtras);
  const pendingForks = new Map();
  try { for (const row of JSON.parse(localStorage.getItem('crbPendingNativeForks') || '[]')) if (row?.commandId && row?.host && row?.thread) pendingForks.set(`${row.host}/${row.thread}`, row); } catch (_) {}
  function saveForks() { try { localStorage.setItem('crbPendingNativeForks', JSON.stringify([...pendingForks.values()].slice(-50))); } catch (_) {} }
  async function finishFork(target, result, commandId, workspace) {
    if (!result.threadId) {
      pendingForks.set(`${target.host}/${target.thread}`, {...target, commandId, clientThreadId: result.clientThreadId, workspace}); saveForks();
      report('原 App 正在创建工作树；请用“检查分支结果”继续，不要重复创建。'); return;
    }
    pendingForks.set(`${target.host}/${target.thread}`, {...target, commandId, threadId: result.threadId, workspace}); saveForks();
    if (workspace) await updateWorkspaceMembership('add', target.host, result.threadId, {forkCommandId: commandId});
    await refresh(); const host = catalog.find(h => h.id === target.host), thread = host?.threads.find(t => t.id === result.threadId);
    if (thread) await selectThread(host, thread);
    pendingForks.delete(`${target.host}/${target.thread}`); saveForks();
    report('✓ 已创建分支对话');
  }
  async function fork(host, thread, environment) {
    if (changing || busy || pendingForks.has(`${host.id}/${thread.id}`)) return;
    const commandId = uuid(), target = {host: host.id, thread: thread.id}, workspace = isWorkspaceThread(host.id, thread.id);
    pendingForks.set(`${host.id}/${thread.id}`, {...target, commandId, workspace}); saveForks();
    changing = true; closeThreadContextMenu(); report('正在由原 App 创建分支…');
    try { const result = await issueCommand('fork_thread', {...target, commandId, payload: {environment}}); await finishFork(target, result, commandId, workspace); }
    catch (error) { if (error.commandRejected) { pendingForks.delete(`${host.id}/${thread.id}`); saveForks(); } report(error.message); } finally { changing = false; }
  }
  function threadMenu(host, thread) {
    threadExtras.replaceChildren(); threadExtras.hidden = !supported(host); if (!supported(host)) return;
    const section = itemSection(host, 'thread', thread.id), pinned = section?.id === 'pinned';
    threadExtras.append(button(pinned ? '取消置顶' : '置顶', () => mutate(host, 'sidebar_move_thread', {sectionId: pinned ? null : 'pinned'}, thread.id)),
      button(thread.unread ? '标记已读' : '标记未读', () => mutate(host, 'sidebar_read_state', {read: Boolean(thread.unread)}, thread.id)),
      button('更改图标…', async () => { const emoji = window.prompt(uiText('输入图标；留空清除')); if (emoji !== null) await mutate(host, 'sidebar_emoji', {emoji: emoji.trim() || null}, thread.id); }),
      moveControl(host, 'thread', thread.id), button('新建分区并移入…', () => createSection(host, {type: 'thread', id: thread.id})));
    if (section && (section.custom || pinned)) threadExtras.append(button('在分区中上移', () => shiftItem(host, 'thread', thread.id, -1)), button('在分区中下移', () => shiftItem(host, 'thread', thread.id, 1)));
    const pending = pendingForks.get(`${host.id}/${thread.id}`);
    if (pending) threadExtras.append(button('检查分支结果', async () => {
      if (changing) return; changing = true;
      try {
        let result = pending.threadId ? {ok: true, threadId: pending.threadId} :
          (await api('/api/command?id=' + encodeURIComponent(pending.commandId))).result;
        if (!result?.ok) {
          if (result && result.unknown !== true) { pendingForks.delete(`${host.id}/${thread.id}`); saveForks(); }
          throw new Error(result?.error || uiText('分支结果尚未确认，请在原 App 核对；不会重复创建。'));
        }
        if (!result.threadId && result.clientThreadId) result = await issueCommand('native_read', {host: host.id, thread: thread.id,
          payload: {operation: 'fork_status', commandId: pending.commandId, clientThreadId: result.clientThreadId}});
        await finishFork({host: host.id, thread: thread.id}, result, pending.commandId, pending.workspace);
      } catch (error) { report(error.message); } finally { changing = false; }
    }), button('移除分支跟踪…', () => {
      if (!window.confirm(uiText('只移除面板跟踪，不会取消原 App 的分支请求。请先在原 App 核对结果。'))) return;
      pendingForks.delete(`${host.id}/${thread.id}`); saveForks(); threadMenuCached(host, thread);
    }));
    else threadExtras.append(button('创建分支 · 当前目录', () => fork(host, thread, 'same-directory')),
      button('创建分支 · 新工作树', () => fork(host, thread, 'worktree')));
    threadExtras.append(button('分享对话…', async () => {
      if (!window.confirm(uiText('创建当前对话的不可变分享链接？'))) return;
      const result = await mutate(host, 'sidebar_share', {}, thread.id);
      const share = result?.share;
      const url = share?.url || share?.shareUrl || share?.share?.url;
      if (url && /^https:\/\//.test(url)) { await navigator.clipboard.writeText(url).catch(() => {}); report(url); }
    }));
    for (const control of threadExtras.querySelectorAll('button,select')) control.disabled = !host.online || !host.desktopConnected || changing || busy || thread.unavailable;
    if (!suppressRead) load(host, true, thread.id).then(() => { if (threadContext?.hostId === host.id && threadContext?.threadId === thread.id) { threadMenuCached(host, thread); } }).catch(error => report(error.message));
  }
  // A menu's explicit refresh does not recursively issue another request.
  function clampContextMenu(menu) {
    if (menu.hidden) return;
    const rect = menu.getBoundingClientRect(), viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    menu.style.left = `${Math.max(left + 8, Math.min(rect.left, left + (viewport?.width || innerWidth) - rect.width - 8))}px`;
    menu.style.top = `${Math.max(top + 8, Math.min(rect.top, top + (viewport?.height || innerHeight) - rect.height - 8))}px`;
  }
  function threadMenuCached(host, thread) { suppressRead = true; try { threadMenu(host, thread); clampContextMenu($('threadContextMenu')); } finally { suppressRead = false; } }
  let suppressRead = false;
  function projectMenu(host, project) {
    projectExtras.replaceChildren(); projectExtras.hidden = !supported(host) || !project; if (projectExtras.hidden) return;
    const section = itemSection(host, 'project', project.id), pinned = section?.id === 'pinned';
    projectExtras.append(button(pinned ? '取消置顶' : '置顶', () => mutate(host, 'sidebar_move_project', {projectId: project.id, sectionId: pinned ? null : 'pinned'})),
      moveControl(host, 'project', project.id), button('新建分区并移入…', () => createSection(host, {type: 'project', id: project.id})));
    if (section && (['threads', 'projects'].includes(section.id) || section.custom && state(host)?.capabilities?.mixedOrdering)) projectExtras.append(button('项目上移', () => shiftItem(host, 'project', project.id, -1)), button('项目下移', () => shiftItem(host, 'project', project.id, 1)));
    projectExtras.append(button('归档项目对话…', async () => {
      const preview = await mutate(host, 'sidebar_archive_preview', {projectId: project.id}); if (!preview || !preview.count) return;
      if (!window.confirm(`${uiText('归档项目中的空闲对话？')} ${preview.count} · ${uiText('保留对话')} ${preview.preservedCount ?? preview.activeCount}`)) return;
      const result = await mutate(host, 'sidebar_archive_project', {projectId: project.id, archiveRevision: preview.archiveRevision});
      if (result) report(result.warning || `${uiText('已归档')} ${result.archivedCount}`);
    }));
    for (const control of projectExtras.querySelectorAll('button,select')) control.disabled = !host.online || !host.desktopConnected || changing || busy || !state(host)?.projectNativeIds?.[project.id];
    if (!suppressRead) load(host, true).then(() => {
      if (projectContext?.hostId === host.id && projectContext?.projectId === project.id) { suppressRead = true; try { projectMenu(host, project); clampContextMenu($('projectContextMenu')); } finally { suppressRead = false; } }
    }).catch(error => report(error.message));
  }
  const sectionMenu = node('div', undefined, 'thread-context-menu native-section-menu');
  sectionMenu.id = 'nativeSectionContextMenu'; sectionMenu.hidden = true;
  sectionMenu.setAttribute('role', 'menu'); sectionMenu.setAttribute('aria-label', uiText('分区操作'));
  document.body.append(sectionMenu);
  document.addEventListener('pointerdown', event => { if (!sectionMenu.contains(event.target)) sectionMenu.hidden = true; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') sectionMenu.hidden = true; });
  function showSectionMenu(host, section, x, y) {
    closeThreadContextMenu(); closeProjectContextMenu(); closeFileContextMenu(); closeTerminalContextMenu();
    const act = (label, fn) => button(label, () => { sectionMenu.hidden = true; fn(); });
    const sectionName = node('div', title(section), 'file-context-target');
    if (section.custom) sectionName.setAttribute('data-i18n-ignore', '');
    sectionMenu.replaceChildren(sectionName,
      act('新建分区', () => createSection(host)),
      act('上移', () => shiftSection(host, section.id, -1)), act('下移', () => shiftSection(host, section.id, 1)));
    if (section.custom) sectionMenu.append(act('重命名分区', async () => {
      const value = window.prompt(uiText('分区名称'), section.name); if (value?.trim()) await mutate(host, 'sidebar_section_rename', {sectionId: section.id, name: value.trim()});
    }), act('删除分区', async () => {
      if (window.confirm(uiText('删除这个分区？其中的项目和对话会保留。'))) await mutate(host, 'sidebar_section_delete', {sectionId: section.id});
    }));
    sectionMenu.append(act('分区与排序', () => openSettings(host)));
    for (const action of sectionMenu.querySelectorAll('button')) { action.setAttribute('role', 'menuitem'); action.disabled = !host.online || !host.desktopConnected || changing || busy; }
    sectionMenu.hidden = false;
    const viewport = window.visualViewport, left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    sectionMenu.style.left = `${Math.max(left + 8, Math.min(x, left + (viewport?.width || innerWidth) - sectionMenu.offsetWidth - 8))}px`;
    sectionMenu.style.top = `${Math.max(top + 8, Math.min(y, top + (viewport?.height || innerHeight) - sectionMenu.offsetHeight - 8))}px`;
  }
  function render(host, visible, groups, filter) {
    const snapshot = state(host); if (!supported(host) || !snapshot || projectFilter !== 'all') return false;
    const displayedThreads = new Set(), displayedProjects = new Set();
    const explicit = new Set(snapshot.sections.filter(s => s.custom || s.id === 'pinned').flatMap(s => s.threadIds));
    const byId = new Map(visible.map(t => [t.id, t]));
    const groupById = new Map(groups.map(g => [g.id, g]));
    const grouping = snapshot.preferences.grouping?.mode || 'project';
    function addThreads(parent, ids, manual) {
      let rows = ids.map(id => byId.get(id)).filter(t => t && !displayedThreads.has(t.id));
      if (!manual) rows = rows.sort((a, b) => (b.updatedAt || b.updated || 0) - (a.updatedAt || a.updated || 0));
      for (const thread of rows) { appendThread(parent, host, thread); displayedThreads.add(thread.id); }
    }
    function addProject(parent, pid) {
      if (displayedProjects.has(pid) || !groupById.has(pid)) return;
      const rows = visible.filter(t => (t.projectId || 'unassigned') === pid && !explicit.has(t.id) && !displayedThreads.has(t.id));
      if (filter && !rows.length) return;
      appendProjectGroup(host, groupById.get(pid), rows, Boolean(filter), parent);
      rows.forEach(t => displayedThreads.add(t.id)); displayedProjects.add(pid);
    }
    for (const section of snapshot.sections) {
      if (!section.custom && !['pinned', 'threads', 'projects', 'chats'].includes(section.id)) continue;
      const wrap = node('section', undefined, 'native-sidebar-section'); wrap.dataset.sectionId = section.id;
      const heading = node('div', undefined, 'native-sidebar-heading');
      const toggle = button(title(section), () => { contents.hidden = !contents.hidden; toggle.setAttribute('aria-expanded', String(!contents.hidden));
        try { localStorage.setItem(`crbSectionCollapsed:${host.id}:${section.id}`, contents.hidden ? '1' : '0'); } catch (_) {} });
      toggle.className = 'native-section-toggle'; toggle.title = title(section);
      if (section.custom) toggle.setAttribute('data-i18n-ignore', '');
      const more = button('⋯', () => { const rect = more.getBoundingClientRect(); showSectionMenu(host, section, rect.left, rect.bottom); }); more.setAttribute('aria-label', uiText('分区操作'));
      more.className = 'native-section-more';
      heading.append(toggle, more);
      heading.oncontextmenu = event => { event.preventDefault(); showSectionMenu(host, section, event.clientX, event.clientY); };
      heading.onkeydown = event => { if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') { event.preventDefault(); more.click(); } };
      const contents = node('div', undefined, 'native-sidebar-content');
      try { contents.hidden = !filter && localStorage.getItem(`crbSectionCollapsed:${host.id}:${section.id}`) === '1'; } catch (_) {}
      toggle.setAttribute('aria-expanded', String(!contents.hidden));
      let items = section.items || [...section.threadIds.map(id => ({type: 'thread', id})), ...section.projectIds.map(id => ({type: 'project', id}))];
      if (section.id === 'pinned' && snapshot.preferences.sorting?.pinned === 'updated_at') {
        const sorted = items.filter(item => item.type === 'thread').sort((a, b) => (byId.get(b.id)?.updatedAt || byId.get(b.id)?.updated || 0) - (byId.get(a.id)?.updatedAt || byId.get(a.id)?.updated || 0));
        let at = 0; items = items.map(item => item.type === 'thread' ? sorted[at++] : item);
      }
      for (const item of items) {
        if (item.type === 'thread') addThreads(contents, [item.id], true);
        else if (section.custom || section.id === 'pinned' || grouping === 'project') addProject(contents, item.id);
      }
      if (['threads', 'projects'].includes(section.id) && grouping === 'project') {
        for (const group of groups) if (group.id !== 'unassigned' && !snapshot.sections.some(s => (s.custom || s.id === 'pinned') && s.projectIds.includes(group.id))) addProject(contents, group.id);
      }
      if (section.id === 'chats') addThreads(contents, visible.filter(t => !explicit.has(t.id) &&
        (grouping !== 'project' || (t.projectId || 'unassigned') === 'unassigned')).map(t => t.id), true);
      if (!contents.children.length) {
        if (filter || !section.custom) continue;
        contents.append(node('p', '暂无对话', 'project-empty'));
      }
      wrap.append(heading, contents); $('catalog').append(wrap);
    }
    // Preserve visibility for older native versions that omit a built-in section.
    for (const thread of visible) if (!displayedThreads.has(thread.id)) appendThread($('catalog'), host, thread);
    if (!$('catalog').children.length) $('catalog').append(node('p', filter ? '没有找到匹配的对话' :
      archiveFilter === 'archived' ? '这个范围内没有已归档对话' : '这个范围内没有活跃对话', 'history-note'));
    return true;
  }
  window.SidebarPanel = {
    sync() { const host = currentHost();
      if (generation !== relayGeneration) { generation = relayGeneration; cache.clear(); freshness.clear(); attempted.clear();
        for (const id of loads.keys()) epochs.set(id, (epochs.get(id) || 0) + 1); loads.clear(); }
      if (host) { const online = Boolean(host.online && host.desktopConnected);
        if (!online || connected.get(host.id) === false || scopes.has(host.id) && scopes.get(host.id) !== host.capabilities?.nativeReadCacheScope) { cache.delete(host.id); freshness.delete(host.id); attempted.delete(host.id);
          epochs.set(host.id, (epochs.get(host.id) || 0) + 1); loads.delete(host.id); }
        scopes.set(host.id, host.capabilities?.nativeReadCacheScope);
        connected.set(host.id, online); }
      manage.hidden = workspaceMode || !supported(host); manage.disabled = !host?.online || !host?.desktopConnected;
      if (!workspaceMode) load(host); if (settings.open && settingsHost !== host?.id) settings.close(); },
    revision: host => state(host)?.revision || '', render,
    threadMenu, projectMenu,
    pinned: (host, id) => supported(host) && state(host) ? itemSection(host, 'thread', id)?.id === 'pinned' : undefined,
    emoji: (host, id) => state(host)?.threadEmojis?.[id] || '',
    moveProject(host, source, target, after) {
      if (!supported(host) || !state(host)) return false;
      const section = itemSection(host, 'project', source), destination = itemSection(host, 'project', target);
      if (!section || !destination || section.id !== destination.id || !['threads', 'projects'].includes(section.id)) { report('请用项目菜单调整分区与顺序'); return true; }
      const order = section.projectIds.filter(id => id !== source), at = order.indexOf(target); order.splice(at + Number(after), 0, source);
      mutate(host, 'sidebar_reorder_projects', {projectIds: order}); return true;
    }
  };
  SidebarPanel.sync();
})();
