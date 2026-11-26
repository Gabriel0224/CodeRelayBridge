// Reviewed 26.1117 native AppServerManager RPC. Runs only in the original App.
// No second app-server, UI clicks, authentication reads or bundle modification.
(async request => {
  if (location.href !== 'app://-/index.html' || codexWindowType !== 'electron')
    throw Error('Original Codex window required');
  const key = request.key;
  if (!/^__crb_rpc_[a-f0-9]{32}$/.test(key)) throw Error('Invalid bridge identity');
  if (request.operation === 'install') {
    if (globalThis[key]) throw Error('Bridge already installed');
    // 26.1117 Windows/macOS share these assets; Linux uses another runtime
    // map. Their reviewed RPC, coordinator and host-scope modules are identical.
    const profiles = [
      [
        ['348410.35ea11b6a0.js', '6ed551093131b3e72a07956981d27253333ffffb1944867d2635eabe98945159'],
        ['async/911644.eaf037cac0.js', '0d3393b090dc0d1b6134be4ec10bafa0c58c5e3986b2f91ffca559579cd56a5d'],
      ],
      [
        ['348410.742384b020.js', '30a4f4021ec2cad583d90408ed03ac0116aa581f2419359150abbeff1fd253fa'],
        ['async/911644.729bde0037.js', '62d3cc39b6afb4679c941271b002ad7adfb9e1ab5624e5bcbbb0e6c4e4b13dee'],
      ],
    ];
    const matched = profiles.filter(p => [...document.scripts]
      .some(s => s.src === 'app://-/assets/' + p[0][0]));
    if (matched.length !== 1) throw Error('Unreviewed or ambiguous native App version');
    const assets = matched[0];
    for (const [name, expected] of assets) {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch('/assets/' + name, {signal: controller.signal});
        if (!response.ok) throw Error('Native asset unavailable');
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength > 12000000) throw Error('Native asset too large');
        const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
          .map(x => x.toString(16).padStart(2, '0')).join('');
        if (hash !== expected) throw Error('Unreviewed native App version');
      } finally { clearTimeout(timer); }
    }
    const native = (await import('/assets/' + assets[0][0])).__webpack_require__;
    const anchor = native('l00C').M4;
    let scope = null, visits = 0;
    function find(f) {
      if (!f || scope || visits++ > 30000) return;
      if ([0, 11, 15].includes(f.tag)) {
        let h = f.memoizedState, count = 0;
        while (h && count++ < 300) {
          const v = h.memoizedState?.current;
          if (v?.scope === anchor.scope && typeof v.get === 'function') { scope = v; break; }
          h = h.next;
        }
      }
      find(f.child); find(f.sibling);
    }
    find(globalThis.__codexRoot?._internalRoot?.current);
    if (!scope) throw Error('Native App scope unavailable');
    const current = () => scope.get(anchor, 'local');
    const entry = current();
    if (entry.status !== 'ready' || !entry.client?.rpc) throw Error('Native App is not ready');
    const rpc = entry.client.rpc.dup(), subscriptions = new Map();
    const dispose = value => { try { value?.[Symbol.dispose]?.(); } catch (_) {} };
    let alive = true, touched = Date.now();
    const assertCurrent = () => {
      if (!alive || current().client !== entry.client || current().status !== 'ready')
        throw Error('Native App connection changed; operation not repeated');
    };
    async function prepareQueue() {
      const factory = native.m?.UaU4;
      if (typeof factory !== 'function') throw Error('Native queue preparation unavailable');
      const bytes = new TextEncoder().encode(Function.prototype.toString.call(factory));
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
        .map(x => x.toString(16).padStart(2, '0')).join('');
      if (hash !== 'c83ca774a2551189c90db8802e556756c7cde62fb4504e705f2a029b8ceb2d73')
        throw Error('Unreviewed native queue preparation');
      return native('UaU4')._(scope, 'local', 'queued');
    }
    const emit = value => {
      if (!alive) return;
      const text = JSON.stringify(value);
      if (text.length > 12000000) throw Error('Native snapshot too large');
      globalThis[key + '_event'](text);
    };
    async function snapshot(id, row) {
      if (!alive || subscriptions.get(id) !== row) return;
      clearTimeout(row.timer); row.timer = null;
      if (row.busy) { row.dirty = true; return; }
      row.busy = true;
      try {
        assertCurrent();
        const state = await rpc.getConversation(id);
        const attached = await rpc.hasLocalHistorySubscription(id);
        assertCurrent();
        if (!alive || subscriptions.get(id) !== row) return;
        if (!state || state.id !== id || state.hostId !== 'local') {
          emit({type: 'unloaded', threadId: id}); return;
        }
        // Serialize only the native conversation, never renderer/account state.
        const text = JSON.stringify([attached, state]);
        if (text !== row.previous) {
          row.previous = text;
          emit({type: 'snapshot', threadId: id, attached, state, revision: ++row.revision});
        }
      } catch (_) { emit({type: 'invalidated', threadId: id}); }
      finally {
        row.busy = false;
        if (row.dirty && alive && subscriptions.get(id) === row) {
          row.dirty = false; row.timer = setTimeout(() => snapshot(id, row), 200);
        }
      }
    }
    function unfollow(id) {
      const row = subscriptions.get(id); if (!row) return;
      subscriptions.delete(id); clearTimeout(row.timer);
      for (const lease of row.leases) dispose(lease);
    }
    function close() {
      if (!alive) return;
      alive = false; clearInterval(watchdog);
      for (const id of [...subscriptions.keys()]) unfollow(id);
      dispose(rpc); delete globalThis[key]; delete globalThis[key + '_event'];
    }
    const watchdog = setInterval(() => {
      if (Date.now() - touched > 45000) close();
    }, 10000);
    globalThis[key] = {
      async call(p) {
        if (p.operation === 'close') { close(); return {ok: true}; }
        assertCurrent(); touched = Date.now();
        if (p.operation === 'ping') return {ok: true};
        const id = p.threadId;
        if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id || ''))
          throw Error('Invalid native thread identity');
        if (p.operation === 'unfollow') { unfollow(id); return {ok: true}; }
        if (p.operation === 'follow') {
          if (!subscriptions.has(id)) {
            if (subscriptions.size >= 40) throw Error('Native subscription limit');
            const row = {leases: [], revision: 0, busy: false, dirty: false, previous: null};
            subscriptions.set(id, row);
            try {
              for (const spec of [
                {type: 'conversation', key: {hostId: 'local', threadId: id}},
                ...['externalRequests', 'mcpServerElicitationRequests', 'historyAttached', 'listState']
                  .map(field => ({type: 'thread', key: {hostId: 'local', threadId: id}, field})),
              ]) {
                const lease = await rpc.subscribe({...spec, listener: () => snapshot(id, row)});
                if (alive && subscriptions.get(id) === row) row.leases.push(lease); else dispose(lease);
              }
              const lease = await rpc.subscribe({type: 'thread', key: {hostId: 'local', threadId: id},
                field: 'queuedMessages', listener: messages => {
                  if (subscriptions.get(id) === row && Array.isArray(messages)) {
                    const signature = JSON.stringify(messages);
                    row.messages = messages;
                    if (signature !== row.queueSignature) {
                      row.queueSignature = signature; emit({type: 'queue', threadId: id, messages});
                    }
                  }
                }});
              if (alive && subscriptions.get(id) === row) row.leases.push(lease); else dispose(lease);
            } catch (e) { unfollow(id); throw e; }
          }
          await snapshot(id, subscriptions.get(id)); return {ok: true};
        }
        if (p.operation === 'owner') {
          const attached = await rpc.hasLocalHistorySubscription(id);
          return {ok: true, attached};
        }
        if (p.operation === 'history') {
          await rpc.ensureConversationHistoryLoaded(id); return {ok: true};
        }
        // Mutations use the running original App's methods and exact scope.
        // Never start/resume a second app-server or retry an uncertain result.
        if (!await rpc.hasLocalHistorySubscription(id)) throw Error('Native thread is not attached');
        assertCurrent();
        const a = p.arguments || {};
        if (p.operation === 'side-create') {
          const source = await rpc.getConversation(id);
          if (!source) throw Error('Parent conversation is not loaded');
          const result = await rpc.forkConversationFromLatest({
            sourceConversationId: id,
            attachToSource: true,
            sideConversation: true,
            addForkedSyntheticItem: false,
            cwd: source.cwd,
            workspaceRoots: source.workspaceRoots,
          });
          if (result?.status !== 'created' || !result.conversationId)
            throw Error('Side chat creation was not confirmed; refresh before trying again');
          const child = await rpc.getConversation(result.conversationId);
          if (child?.attachedToThreadId !== id)
            throw Error('Side chat ownership was not confirmed; refresh before trying again');
          return {ok: true, thread: {id: result.conversationId, attachedToThreadId: id}};
        }

        if (p.operation === 'queue-remove-receipt') {
          const coordinator = await rpc.getTurnCoordinator();
          try {
            const removed = await coordinator.removeQueuedMessage(id, a.messageId);
            return {ok: true, removed};
          } finally { dispose(coordinator); }
        }
        if (['queue', 'queue-resume', 'queue-send-now'].includes(p.operation)) {
          const row = subscriptions.get(id), before = a.before;
          if (!row || !Array.isArray(before) || JSON.stringify(before) !== JSON.stringify(row.messages))
            throw Error('Native queue changed; refresh before retrying');
          if (p.operation === 'queue-resume' && before.some(m =>
              m.preparation || ['sending', 'outcome-unknown'].includes(m.submission?.status)))
            throw Error('Native queue contains an unconfirmed submission');
          const coordinator = await rpc.getTurnCoordinator();
          try {
            assertCurrent();
            if (JSON.stringify(before) !== JSON.stringify(row.messages)) throw Error('Native queue changed');
            if (p.operation === 'queue-resume') {
              await coordinator.resumeInterruptedQueuedMessages(id); return {ok: true};
            }
            if (p.operation === 'queue-send-now') {
              const message = before.find(m => m.id === a.messageId);
              if (!message) throw Error('Native queued message unavailable');
              if (message.preparation || ['sending', 'outcome-unknown'].includes(message.submission?.status))
                throw Error('Native queue submission is not confirmed');
              const prepare = await prepareQueue();
              const result = await coordinator.sendQueuedMessageNow(id, a.messageId,
                message.submissionOptions, prepare, assertCurrent);
              if (!['sent', 'queued'].includes(result?.status)) throw Error('Native queue send result unknown');
              return {ok: true, status: result.status};
            }
            const after = a.after;
            if (!Array.isArray(after) || after.length > 1000 || after.some(m => !m || typeof m.id !== 'string') ||
                new Set(after.map(m => m.id)).size !== after.length) throw Error('Invalid native queue');
            const old = new Map(before.map(m => [m.id, m])), next = new Map(after.map(m => [m.id, m]));
            const added = after.filter(m => !old.has(m.id)), removed = before.filter(m => !next.has(m.id));
            const edited = after.filter(m => old.has(m.id) && JSON.stringify(old.get(m.id)) !== JSON.stringify(m));
            if (!added.length && !removed.length && !edited.length) {
              await coordinator.reorderQueuedMessages(id, after.map(m => m.id));
            } else if (removed.length === 1 && !added.length && !edited.length &&
                JSON.stringify(a.discardedIds) === JSON.stringify([removed[0].id])) {
              await coordinator.removeQueuedMessage(id, removed[0].id);
            } else if (!removed.length && added.length + edited.length === 1) {
              const message = added[0] || edited[0];
              if (message.preparation || ['sending', 'outcome-unknown'].includes(message.submission?.status))
                throw Error('Native queue submission is not confirmed');
              const prepare = await prepareQueue();
              const validate = () => {
                assertCurrent();
                if (JSON.stringify(before) !== JSON.stringify(row.messages)) throw Error('Native queue changed');
              };
              validate();
              // The original coordinator atomically replaces a local queued ID.
              // Never delete it first, manufacture a preparation reservation, or
              // start an empty turn as a substitute for enqueueing.
              const index = after.findIndex(m => m.id === message.id);
              const editPosition = edited.length ? {messageId: message.id, index,
                previousMessageId: after[index - 1]?.id ?? null,
                nextMessageId: after[index + 1]?.id ?? null} : undefined;
              const result = await coordinator.sendMessage({conversationId: id, message, editPosition,
                queueModeOverride: 'queue'}, prepare,
                undefined, validate);
              if (result?.status !== 'queued' || typeof result.messageId !== 'string' || !result.messageId)
                throw Error('Native queue result unknown');
              return {ok: true, messageId: result.messageId};
            } else {
              // There is no equivalent atomic full-snapshot setter in this App.
              // Do not implement edits by deleting first and risking message loss.
              throw Error('Only one native queue mutation is allowed per request');
            }
            return {ok: true};
          } finally { dispose(coordinator); }
        }
        const approvalMethods = {
          'command-approval': ['item/commandExecution/requestApproval', 'replyWithCommandExecutionApprovalDecision', 'decision'],
          'file-approval': ['item/fileChange/requestApproval', 'replyWithFileChangeApprovalDecision', 'decision'],
          'permissions-approval': ['item/permissions/requestApproval', 'replyWithPermissionsRequestApprovalResponse', 'response'],
          'user-input': ['item/tool/requestUserInput', 'replyWithUserInputResponse', 'response'],
          'elicitation': ['mcpServer/elicitation/request', 'replyWithMcpServerElicitationResponse', 'response'],
        };
        if (approvalMethods[p.operation]) {
          const [method, callback, field] = approvalMethods[p.operation];
          const state = await rpc.getConversation(id);
          if (!state?.requests?.some(r => r.id === a.requestId && r.method === method && r.completed !== true))
            throw Error('Native request changed; response not submitted');
          await rpc[callback](id, a.requestId, a[field]); return {ok: true};
        }
        switch (p.operation) {
          case 'start':
            if (a.request?.threadId !== id) throw Error('Native turn identity mismatch');
            await rpc.startTurn(id, a); break;
          case 'steer':
            await rpc.steerTurn(id, a.input, a.restoreMessage, a.serviceTier,
              a.attachments, a.clientUserMessageId, a.additionalContext, a.toolOutput); break;
          case 'interrupt': await rpc.interruptConversation(id, 'user-stop', a.turnId); break;
          case 'edit': await rpc.editLastUserTurn(id, a); break;
          case 'settings': await rpc.updateThreadSettingsForNextTurn(id, a.threadSettings); break;
          default: throw Error('This native RPC operation has not been reviewed');
        }
        return {ok: true, applied: true};
      }, close
    };
    try {
      if (await rpc.getHostId() !== 'local' || await rpc.getCodexHome() !== request.home)
        throw Error('Wrong native host or home');
      return {ok: true, protocol: 1};
    } catch (e) { close(); throw e; }
  }
  const bridge = globalThis[key];
  if (!bridge && request.operation === 'close') {
    delete globalThis[key + '_event']; return {ok: true};
  }
  if (!bridge) throw Error('Native bridge expired');
  return await bridge.call(request);
})
