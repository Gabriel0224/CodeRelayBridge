// Version-reviewed native selector and mixed-section order callback.
(async request => {
  if (location.protocol !== 'app:' || location.host !== '-' || location.pathname !== '/index.html') throw Error('Original App required');
  const contracts = {
    linux: ['app-shared-f2257985f1b6.js', 'be5fbb46d2d4b3fea9e7a4fc7385032e1e3f26d32d4e5cf9ce3fd51617baed5d', 'Zyt', 'LP', 'UP']
  };
  const c = contracts[request.platform]; if (!c) throw Error('此原 App 版本尚未提供可核验的分支映射通道，请在原 App 查看结果');
  const url = '/assets/' + c[0];
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 1500);
  let bytes;
  try {
    const response = await fetch(url, {cache: 'no-store', signal: controller.signal}); if (!response.ok) throw Error('Native asset unavailable');
    bytes = await response.arrayBuffer();
  } finally { clearTimeout(timer); } if (bytes.byteLength > 16000000) throw Error('Native asset too large');
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v => v.toString(16).padStart(2, '0')).join('');
  if (hash !== c[1]) throw Error('原 App 版本已变化，请先更新适配');
  const native = await import(url), mapping = native[c[2]], reorder = native[c[3]], model = native[c[4]];
  if (!mapping?.scope || !model?.scope || typeof reorder !== 'function') throw Error('Native sidebar contract changed');
  let scope = null, visits = 0;
  function find(fiber) {
    if (!fiber || scope || visits++ > 30000) return;
    let hook = fiber.memoizedState, count = 0;
    while (hook && count++ < 300) {
      const value = hook.memoizedState?.current;
      if (value?.scope === mapping.scope && value.node && value.chain && typeof value.get === 'function' && typeof value.watch === 'function') { scope = value; break; }
      hook = hook.next;
    }
    find(fiber.child); find(fiber.sibling);
  }
  find(window.__codexRoot?._internalRoot?.current); if (!scope) throw Error('Native sidebar scope unavailable');
  if (request.operation === 'capabilities') {
    let manager = null;
    try { if (native.vA?.scope) manager = scope.get(native.vA, 'local'); } catch (_) {}
    return {status: 'ok', mixedOrdering: true, forkMapping: true,
      historyEdit: Boolean(manager && ['getConversation', 'getCompleteConversationTurns', 'sendRequest',
        'applyRevertResponseToConversation', 'startTurn'].every(key => typeof manager[key] === 'function'))};
  }
  if (request.operation === 'edit-history') {
    if (typeof request.threadId !== 'string' || !request.threadId ||
        ['turnId', 'itemId', 'expectedLastTurnId'].some(k => typeof request[k] !== 'string' || !request[k] || request[k].length > 200) ||
        typeof request.text !== 'string' || !request.text.trim() || request.text.trim().length > 32000 ||
        typeof request.originalText !== 'string') throw Error('Invalid native edit identity');
    const manager = scope.get(native.vA, 'local');
    if (!manager || ['getConversation', 'getCompleteConversationTurns', 'getStreamRole',
      'waitForPendingThreadSettingsUpdate', 'sendRequest', 'applyRevertResponseToConversation', 'startTurn']
      .some(k => typeof manager[k] !== 'function')) throw Error('Native history contract changed');
    if (manager.getStreamRole(request.threadId)?.role !== 'owner') throw Error('请在原 App 打开此对话后重试');
    await manager.getCompleteConversationTurns(request.threadId);
    await manager.waitForPendingThreadSettingsUpdate(request.threadId);
    const state = manager.getConversation(request.threadId);
    const history = state?.turnHistory?.history;
    const turns = history ? history.islands.flatMap(island => island.entries.map(entry =>
      history.entitiesByKey[entry.value ?? entry.key])).filter(Boolean) : state?.turns || [];
    const index = turns.findIndex(turn => turn.turnId === request.turnId), turn = turns[index];
    if (!turn || state.threadRuntimeStatus?.type === 'active' ||
        turns.some(t => t.status === 'inProgress') || turns.at(-1)?.turnId !== request.expectedLastTurnId)
      throw Error('对话内容已变化，请刷新后重试');
    const item = turn.items.find(item => item.id === request.itemId && ['userMessage', 'UserMessage'].includes(item.type));
    const input = turn.params?.input;
    if (!item || !Array.isArray(input)) throw Error('原 App 未提供可编辑的消息内容');
    const pos = input.findIndex(part => part.type === 'text');
    const raw = pos < 0 ? '' : input[pos].text;
    const matches = [...raw.matchAll(/## My request(?: for Codex)?:/g)], marker = matches.at(-1);
    const text = marker ? raw.slice(marker.index + marker[0].length).trim() : raw.trim();
    if (text !== request.originalText.trim()) throw Error('这条消息已变化，请刷新后重试');
    const edited = structuredClone(input), message = request.text.trim();
    if (pos < 0) edited.unshift({type: 'text', text: message, text_elements: []});
    else edited[pos] = {...edited[pos], text: marker ? raw.slice(0, marker.index + marker[0].length) + '\n' + message : message,
      text_elements: []};
    // Let the original manager replace its canonical history, invalidate its
    // cursors and broadcast the same reset used by Desktop's own edit flow.
    const response = await manager.sendRequest('thread/revert', {threadId: request.threadId, beforeTurnId: turn.turnId});
    manager.applyRevertResponseToConversation({conversationId: request.threadId, response, revertedTurns: turns.slice(index)});
    try {
      await manager.startTurn(request.threadId, {request: {threadId: request.threadId,
        input: edited, turnTrigger: 'edit_user_message', serviceTier: state.latestThreadSettings?.serviceTier},
        context: {inheritThreadSettings: true, attachments: turn.params.attachments,
          commentAttachments: turn.params.commentAttachments,
          mcpAppModelContextAttachments: turn.mcpAppModelContextAttachments}});
      return {status: 'ok', historyReverted: true, messageSent: true};
    } catch (error) {
      // Revert is already accepted. Returning that fact prevents a second
      // destructive edit and lets the panel preserve the unsent draft.
      return {status: 'ok', historyReverted: true, messageSent: false, outcomeUnknown: true,
        warning: String(error.message || error)};
    }
  }
  if (request.operation === 'fork-status') {
    if (typeof request.clientThreadId !== 'string' || !request.clientThreadId.startsWith('client-new-thread:') || request.clientThreadId.length > 200) throw Error('Invalid native creation identity');
    const read = get => {
      const id = get(mapping, request.clientThreadId);
      return typeof id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id) ? id : null;
    };
    const immediate = read((atom, arg) => scope.get(atom, arg)); if (immediate) return {status: 'ok', threadId: immediate};
    return await new Promise((resolve, reject) => {
      let settled = false, dispose = null;
      const finish = (value, error) => { if (settled) return; settled = true; clearTimeout(timer); dispose?.(); error ? reject(error) : resolve({status: 'ok', threadId: value, pending: !value}); };
      const timer = setTimeout(() => finish(null), 3000);
      try { dispose = scope.watch(({get}) => { const value = read(get); if (value) queueMicrotask(() => finish(value)); }); if (settled) dispose(); }
      catch (error) { finish(null, error); }
    });
  }
  if (request.operation === 'reorder-items') {
    if (typeof request.sectionId !== 'string' || !Array.isArray(request.before) || !Array.isArray(request.after) || request.before.length > 5000 ||
      request.before.some(v => typeof v !== 'string') || request.after.some(v => typeof v !== 'string') ||
      new Set(request.before).size !== request.before.length || new Set(request.after).size !== request.after.length ||
      JSON.stringify([...request.before].sort()) !== JSON.stringify([...request.after].sort())) throw Error('Invalid native section order');
    const id = request.sectionId.replace(/^custom:/, '');
    const section = scope.get(model).sections.find(row => row.id === id);
    if (!section || JSON.stringify(section.itemKeys) !== JSON.stringify(request.before)) throw Error('分区已变化，请刷新');
    reorder(scope, id, request.after);
    const actual = scope.get(model).sections.find(row => row.id === id)?.itemKeys;
    if (JSON.stringify(actual) !== JSON.stringify(request.after)) throw Error('原 App 未确认新顺序');
    return {status: 'ok'};
  }
  throw Error('Unsupported native sidebar operation');
})
