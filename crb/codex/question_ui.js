// Local, version-reviewed question cleanup and explicit native queue resume.
// No clicks, focus, synthesized sends, auth reads or installed-file changes.
(async (request) => {
  if (location.protocol !== 'app:' || location.host !== '-' || location.pathname !== '/index.html')
    throw new Error('Not the original Codex surface');
  if (request.operation === 'capacity-retry-state') {
    if (request.hostId !== 'local' || typeof request.threadId !== 'string' || !request.threadId ||
        typeof request.turnId !== 'string' || !request.turnId) throw new Error('Invalid retry scope');
    // Read the ORIGINAL retry component's deadline and rendered CTA props.
    // No scope.set, callbacks, clicks, focus, model calls or automatic retry.
    const inspect = () => {
      let result = null, visits = 0;
      const walk = (fiber, parentTurn = null) => {
        if (!fiber || result || visits++ > 30000) return;
        const props = fiber.memoizedProps;
        const turn = props?.conversationId === request.threadId && props.hostId === 'local' &&
          typeof props.turnId === 'string' ? props.turnId : parentTurn;
        if (turn === request.turnId && props?.conversationId === request.threadId &&
            props.hostId === 'local' && Object.hasOwn(props, 'retryDelaySeconds') &&
            typeof fiber.type === 'function') {
          const source = Function.prototype.toString.call(fiber.type);
          if (source.includes('capacity_retry_automatic') && source.includes('capacity_retry_manual')) {
            let deadline = null, hook = fiber.memoizedState, limit = 0;
            while (hook && limit++ < 300) {
              const value = hook.memoizedState;
              if (typeof value === 'number' && Number.isFinite(value) &&
                  value > Date.now() - 86400000 && value < Date.now() + 86400000) deadline ??= value;
              hook = hook.next;
            }
            const findCta = node => {
              if (!node) return null;
              const p = node.memoizedProps;
              if (p && Object.hasOwn(p, 'secondsRemaining') && Object.hasOwn(p, 'totalSeconds') &&
                  typeof p.onClick === 'function') return p;
              return findCta(node.child) || findCta(node.sibling);
            };
            const cta = findCta(fiber.child);
            if (cta && (cta.secondsRemaining == null || Number.isFinite(cta.secondsRemaining))) {
              const automatic = cta.secondsRemaining != null && cta.disabled !== true;
              result = {status: 'capacity-observed', turnId: request.turnId,
                automatic, pending: cta.disabled === true,
                retryAtMs: automatic ? deadline : null,
                retryDelaySeconds: cta.totalSeconds ?? null};
            }
          }
        }
        walk(fiber.child, turn); walk(fiber.sibling, parentTurn);
      };
      walk(window.__codexRoot?._internalRoot?.current);
      return result;
    };
    const immediate = inspect();
    if (immediate) return immediate;
    // IPC can report failure before React commits the banner. Wait only for
    // that bounded local render event; disconnect the observer before return.
    return await new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true; clearTimeout(timer); observer.disconnect(); resolve(value);
      };
      const observer = new MutationObserver(() => queueMicrotask(() => {
        const value = inspect(); if (value) finish(value);
      }));
      const timer = setTimeout(() => finish({status: 'capacity-not-rendered', turnId: request.turnId}), 1200);
      observer.observe(document.documentElement, {childList: true, characterData: true, subtree: true});
    });
  }
  if (request.operation === 'resume-interrupted-queue') {
    if (request.hostId !== 'local' || typeof request.threadId !== 'string' || !request.threadId ||
        !Array.isArray(request.messageIds) || !request.messageIds.length || request.messageIds.length > 1000 ||
        request.messageIds.some(id => typeof id !== 'string' || !id || id.length > 200) ||
        new Set(request.messageIds).size !== request.messageIds.length) throw new Error('Invalid queue scope');
    // The original queue's Continue callback calls this exact coordinator API.
    // It handles both local and server-backed queues, unlike clearing pause
    // flags or starting an empty turn through the follower IPC.
    const queueContracts = {
      win32: [
        ['app-initial-5851df3f6cb1.js', 'c70d95da34ba7e5962a559dbf68d0102d10b4b47b877f5d9933629bb5a1f3a10', null, null, 'YPr', 'utn'],
        ['app-initial-df84a35d942d.js', 'd7659a96f7d0cfe2249e089ef19675d09d04d6dd9569b550ddaa64722d4f13f3', null, null, 'YPr', 'utn'],
      ],
      darwin: [['app-initial-90eb42395b7d.js', '2687d59e8c67ccf81c7efb3820211070df303c6a200c9e0e8d604f26253f2f5e', null, null, 'YPr', 'utn']],
      linux: [['app-initial-17317d71fd80.js', '3cec672b3ffa028c5e7d47eab4198ef0102bd04955e45282154de8aa3f840ce1',
        'app-shared-6fc3d63f0486.js', 'e24dc1a9e516fb12b36edfe2ae984185ca1238787fcc19938501a097b1acd06c', 'v$t', 'U2t']],
    };
    const hash = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
      .map(value => value.toString(16).padStart(2, '0')).join('');
    async function verifiedAsset(name, expected) {
      const controller = new AbortController(); let timer;
      try {
        const bytes = await Promise.race([(async () => {
          const response = await fetch('/assets/' + name, {signal: controller.signal});
          if (response.ok === false) throw new Error('Unavailable native queue asset');
          return await response.arrayBuffer();
        })(), new Promise((_, reject) => {timer = setTimeout(() => {
          controller.abort(); reject(new Error('Native queue asset timed out'));
        }, 1200);})]);
        return await hash(bytes) === expected;
      } finally {clearTimeout(timer);}
    }
    // Background windows can throttle the timeout for a missing app:// asset.
    // Prefer real loaded scripts, not failed probes recorded as fetch resources.
    const loaded = typeof performance !== 'undefined' && typeof performance.getEntriesByType === 'function'
      ? new Set(performance.getEntriesByType('resource').filter(entry => entry.initiatorType !== 'fetch')
        .map(entry => entry.name.split(/[?#]/)[0])) : new Set();
    const candidates = [...(queueContracts[request.platform] || [])].sort((a, b) =>
      Number([...loaded].some(name => name.endsWith('/assets/' + b[0]))) -
      Number([...loaded].some(name => name.endsWith('/assets/' + a[0]))));
    let contract = null;
    for (const candidate of candidates) {
      try {if (await verifiedAsset(candidate[0], candidate[1])) {contract = candidate; break;}} catch (_) {}
    }
    if (!contract) throw new Error('Unreviewed native queue version');
    if (contract[2] && !await verifiedAsset(contract[2], contract[3])) throw new Error('Unreviewed native queue transport');
    const native = await import('/assets/' + contract[0]);
    const shared = contract[2] ? await import('/assets/' + contract[2]) : native;
    const queueAtom = native[contract[5]], getRpc = shared[contract[4]];
    if (!queueAtom?.scope || typeof getRpc !== 'function') throw new Error('Native queue contract changed');
    let scope = null, visits = 0;
    function findScope(fiber) {
      if (!fiber || scope || visits++ > 30000) return;
      if ([0, 11, 15].includes(fiber.tag)) {
        let hook = fiber.memoizedState, count = 0;
        while (hook && count++ < 300) {
          const value = hook.memoizedState?.current;
          if (value?.scope === queueAtom.scope && value.node && value.chain && typeof value.get === 'function') {scope = value; break;}
          hook = hook.next;
        }
      }
      findScope(fiber.child); findScope(fiber.sibling);
    }
    findScope(window.__codexRoot?._internalRoot?.current);
    if (!scope) throw new Error('Native queue scope unavailable');
    const key = {hostId: 'local', threadId: request.threadId};
    const read = () => scope.get(queueAtom, key);
    const fingerprint = values => Array.isArray(values) ? JSON.stringify(values.map(message =>
      [message.id, message.pausedReason ?? null, message.submission?.status ?? null])) : null;
    const messages = read(), interrupted = 'Interrupted before the steer was accepted.';
    if (!Array.isArray(messages) || JSON.stringify(messages.map(message => message.id)) !== JSON.stringify(request.messageIds))
      throw new Error('Native queue changed; refresh before continuing');
    if (!messages.some(message => message.pausedReason === interrupted)) return {status: 'queue-not-paused'};
    if (messages.some(message => ['sending', 'outcome-unknown'].includes(message.submission?.status)) ||
        messages[0].pausedReason && messages[0].pausedReason !== interrupted) throw new Error('Native queue is not ready to continue');
    const expected = fingerprint(messages);
    const coordinator = await getRpc(scope, 'local').getTurnCoordinator();
    // Re-read immediately before dispatch; never revive deleted or reordered items.
    // Unobserved native selectors may produce a fresh but equivalent array.
    if (fingerprint(read()) !== expected) throw new Error('Native queue changed; refresh before continuing');
    await coordinator.resumeInterruptedQueuedMessages(request.threadId);
    const after = read();
    return {status: Array.isArray(after) && !after.some(message => message.pausedReason === interrupted)
      ? 'queue-resumed' : 'queue-resume-pending'};
  }
  // Each tuple was reviewed against that platform's installed App, including
  // its original submit callback and accepted-answer parser. Unknown builds
  // fail closed; similar version numbers do not establish compatibility.
  const contracts = {
    win32: ['app-initial-b8e1c8014bb4.js', '799331dde54905c4df5e360f23a9621141759e2c0acb230aeab909f9904b646f',
      'app-shared-737655e1fb23.js', 'a1daa891e89a395b8db399ff6eaa365f12a86880c3d8cf891caf4ccbcb9eebf5', 'gKn', 'hKn', 'yKn', 'L8'],
    darwin: ['app-initial-c468e3fd7f91.js', 'efd785d44bc6f07771f3d5a21cf255112d0f17969b398889e6a8a4868338152f',
      'app-shared-2f2228189a8d.js', '47b7173d0bbecc5f6f32157134add7be65c0a22c2555c5bbeb9665b1bcded7b0', 'gKn', 'hKn', 'yKn', 'L8'],
    linux: ['app-initial-929a3344d5d0.js', '5d51a4b80d01286b08e0491818733bb80998a3165c380e045a00b8c075a49a43',
      'app-shared-cc1fe93eb683.js', 'fd664125b278cc1e16bb4cad9cc4f44392af0426404e92a8ed2514d53f8e3be4', 'SBn', 'xBn', 'EBn', 'C3'],
  };
  const candidates = contracts[request.platform] ? [contracts[request.platform]] : [];
  if (request.platform === 'win32') candidates.push([
    'app-initial-df84a35d942d.js', 'd7659a96f7d0cfe2249e089ef19675d09d04d6dd9569b550ddaa64722d4f13f3',
    'app-initial-df84a35d942d.js', 'd7659a96f7d0cfe2249e089ef19675d09d04d6dd9569b550ddaa64722d4f13f3',
    'P6n', 'N6n', 'L6n', 'Vxr', 'single-replies']);
  if (request.platform === 'win32') candidates.push([
    'app-initial-5851df3f6cb1.js', 'c70d95da34ba7e5962a559dbf68d0102d10b4b47b877f5d9933629bb5a1f3a10',
    'app-initial-5851df3f6cb1.js', 'c70d95da34ba7e5962a559dbf68d0102d10b4b47b877f5d9933629bb5a1f3a10',
    'P6n', 'N6n', 'L6n', 'Vxr', 'single-replies']);
  if (request.platform === 'darwin') candidates.push([
    'app-initial-90eb42395b7d.js', '2687d59e8c67ccf81c7efb3820211070df303c6a200c9e0e8d604f26253f2f5e',
    'app-initial-90eb42395b7d.js', '2687d59e8c67ccf81c7efb3820211070df303c6a200c9e0e8d604f26253f2f5e',
    'P6n', 'N6n', 'L6n', 'Vxr', 'single-replies']);
  if (request.platform === 'linux') candidates.push([
    'app-initial-17317d71fd80.js', '3cec672b3ffa028c5e7d47eab4198ef0102bd04955e45282154de8aa3f840ce1',
    'app-shared-6fc3d63f0486.js', 'e24dc1a9e516fb12b36edfe2ae984185ca1238787fcc19938501a097b1acd06c',
    'dKn', 'uKn', 'mKn', 'L8']);
  if (!candidates.length) throw new Error('Unreviewed native question UI platform');
  // An absent app:// asset is not guaranteed to finish with HTTP 404. Prefer
  // the already loaded bundle, then bound each local read including its body.
  // Otherwise a changed App build can leave an acknowledgement pending forever.
  const loaded = typeof performance !== 'undefined' && typeof performance.getEntriesByType === 'function'
    ? new Set(performance.getEntriesByType('resource').map(entry => entry.name.split(/[?#]/)[0])) : new Set();
  candidates.reverse().sort((a, b) => Number([...loaded].some(name => name.endsWith('/assets/' + b[0]))) -
    Number([...loaded].some(name => name.endsWith('/assets/' + a[0]))));
  const verifiedBytes = new Map();
  async function localAsset(name) {
    if (verifiedBytes.has(name)) return verifiedBytes.get(name);
    const controller = new AbortController();
    let timer;
    try {
      const bytes = await Promise.race([
        (async () => {
          const response = await fetch('/assets/' + name, {signal: controller.signal});
          if (response.ok === false) throw new Error('Native asset unavailable');
          return await response.arrayBuffer();
        })(),
        new Promise((_, reject) => {timer = setTimeout(() => {
          controller.abort(); reject(new Error('Native asset read timed out'));
        }, 1200);}),
      ]);
      verifiedBytes.set(name, bytes);
      return bytes;
    } finally {clearTimeout(timer);}
  }
  let contract = null;
  for (const candidate of candidates) {
    let bytes;
    try {bytes = await localAsset(candidate[0]);} catch (_) {continue;}
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
      .map(b => b.toString(16).padStart(2, '0')).join('');
    if (hash === candidate[1]) {contract = candidate; break;}
  }
  if (!contract) throw new Error('Unreviewed native question UI version');
  const asset = '/assets/' + contract[0];
  const native = await import(asset);
  const sharedAsset = '/assets/' + contract[2];
  const sharedBytes = await localAsset(contract[2]);
  const sharedHash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', sharedBytes))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  if (sharedHash !== contract[3])
    throw new Error('Unreviewed native answer parser version');
  const shared = await import(sharedAsset);
  const parseAccepted = shared[contract[7]];
  const selection = native[contract[4]], question = native[contract[5]], select = native[contract[6]];
  if (!selection?.scope || !question?.resolve || typeof select !== 'function' || typeof parseAccepted !== 'function')
    throw new Error('Native question selection contract changed');
  if (request.hostId !== 'local' || typeof request.threadId !== 'string' || !request.threadId ||
      typeof request.turnId !== 'string' || !request.turnId ||
      !Array.isArray(request.replies) || request.replies.length < 1 || request.replies.length > 8)
    throw new Error('Invalid question acknowledgement');
  const turnId = request.turnId;
  const answers = new Map();
  for (const reply of request.replies) {
    const id = JSON.parse(reply.questionItemId);
    if (!Array.isArray(id) || id.length !== 3 || id[0] !== 'request_user_input_async' ||
        typeof id[1] !== 'string' || !id[1] || !Number.isInteger(id[2]) || id[2] < 0 ||
        typeof reply.answer !== 'string' || !reply.answer.trim() || reply.answer.length > 4000 ||
        answers.has(reply.questionItemId)) throw new Error('Invalid native question identity');
    answers.set(reply.questionItemId, reply.answer.trim());
  }
  // React's retained scope is used without changing React or its component
  // functions. Accept only the original question atoms' exact owning scope.
  let scope = null, manager = null, count = 0;
  function visit(f) {
    if (!f || (scope && manager) || count++ > 30000) return;
    const props = f.memoizedProps;
    if (props?.questionKey?.hostId === request.hostId && props.questionKey.threadId === request.threadId &&
        typeof props.manager?.getConversation === 'function' &&
        typeof props.manager?.subscribe === 'function') manager = props.manager;
    if ([0, 11, 15].includes(f.tag)) {
      let hook = f.memoizedState, limit = 0;
      while (hook && limit++ < 300) {
        const value = hook.memoizedState?.current;
        if (value?.scope === selection.scope && value.node && value.chain &&
            typeof value.get === 'function' && typeof value.set === 'function' && typeof value.watch === 'function') {
          scope = value; break;
        }
        hook = hook.next;
      }
    }
    visit(f.child); visit(f.sibling);
  }
  visit(window.__codexRoot?._internalRoot?.current);
  if (!scope) throw new Error('Native question scope unavailable');
  const hostThread = {hostId: request.hostId, threadId: request.threadId};
  // Idle replies start a NEW turn. The App's old-question atom watches only
  // its own turn, so also read accepted replies from the App's cached history
  // using the App's exact parser. No history fetch, extra model call, or poll.
  function acceptedAnswers(entityKey) {
    if (!manager) return new Map();
    const conversation = manager.getConversation(request.threadId);
    if (!conversation || conversation.hostId !== request.hostId) return new Map();
    const history = conversation.turnHistory;
    const original = history?.kind === 'canonical' ? history.history.entitiesByKey[entityKey]
      : (conversation.turns ?? []).find(turn => turn.turnId === turnId);
    if (original?.turnId !== turnId) return new Map();
    const turns = history?.kind === 'canonical'
      ? history.history.islands.flatMap(island => island.entries)
        .slice(-12).map(entry => history.history.entitiesByKey[entry.value]).filter(Boolean)
      : (conversation.turns ?? []).slice(-12);
    const items = [original, ...turns.filter(turn => turn !== original)].flatMap(turn => turn.items ?? []);
    if (contract[8] !== 'single-replies') return parseAccepted(items).answersByQuestionItemId;
    // This reviewed App build exposes its exact envelope parser separately.
    // Only original accepted messages, never local drafts or queued steers,
    // establish acceptance. Preserve native order and exact question identity.
    const accepted = new Map();
    for (const item of items) {
      if (item.type !== 'userMessage' && !(item.type === 'steeringUserMessage' && item.status === 'accepted')) continue;
      const input = item.type === 'userMessage' ? item.content : item.input;
      if (!Array.isArray(input) || input.length !== 1 || input[0]?.type !== 'text') continue;
      for (const reply of parseAccepted(input[0].text) || []) {
        if (answers.has(reply.questionItemId)) accepted.set(reply.questionItemId, reply.answer);
      }
    }
    return accepted;
  }
  // Read App-confirmed acceptance, not the relay's optimistic state.
  // Do not clear another turn, an unanswered question, or a later edited draft.
  function decide(get) {
    const selected = get(selection, hostThread);
    if (!selected) return {status: 'not-open'};
    if (selected.selectedQuestionKey?.hostId !== request.hostId ||
        selected.selectedQuestionKey?.threadId !== request.threadId) return {status: 'different-question-preserved'};
    if (typeof selected.selectedQuestionKey?.entityKey !== 'string') throw new Error('Invalid native entity');
    const key = {...hostThread, entityKey: selected.selectedQuestionKey.entityKey};
    const currentState = get(question, selected.selectedQuestionKey);
    if (currentState?.turnId !== turnId || !selected.questionIds?.some(id => answers.has(id)))
      return {status: 'different-question-preserved'};
    const ids = selected.questionIds;
    if (!Array.isArray(ids) || !ids.length) throw new Error('Invalid native selection');
    const accepted = acceptedAnswers(key.entityKey), updates = [];
    let waiting = false, removed = 0;
    const retained = ids.filter(id => {
      const answer = answers.get(id);
      if (answer == null) return true;
      const state = get(question, {...key, itemId: id});
      if (!state || state.turnId !== turnId || (accepted.has(id)
          ? accepted.get(id) !== answer : state.lastSubmission !== answer)) {waiting = true; return true;}
      if (typeof state.draft !== 'string') return true;
      const unchanged = state.draft === state.draftBaseline;
      const matches = state.draft.trim() === answer;
      updates.push({key: {...key, itemId: id}, state: {...state, lastSubmission: answer,
        ...(Object.hasOwn(state, 'deadlineMs') ? {deadlineMs: null} : {}),
        draftBaseline: answer, isSubmitting: false, draft: unchanged ? answer : state.draft}});
      if (!unchanged && !matches) return true;
      removed++; return false;
    });
    if (waiting) return null;
    if (!removed) return {status: 'new-draft-preserved', updates};
    const next = retained.length ? {...selected, questionIds: retained,
      selectedQuestionKey: retained.includes(selected.selectedQuestionKey.itemId)
        ? selected.selectedQuestionKey : {...key, itemId: retained[0]}} : null;
    return {status: retained.length ? 'unanswered-preserved' : 'closed', next, updates};
  }
  let result = decide((atom, argument) => scope.get(atom, argument));
  if (!result) {
    // Event-driven bounded acknowledgement wait; no polling and no persistent
    // hook living in the App after the client has returned or exited.
    result = await new Promise((resolve, reject) => {
      let settled = false;
      const disposers = [];
      const finish = (value, error = null) => {
        if (settled) return;
        settled = true; clearTimeout(timer);
        for (const dispose of disposers) dispose();
        if (error) reject(error); else resolve(value);
      };
      const timer = setTimeout(() => finish({status: 'awaiting-native-acceptance'}), 3000);
      const register = dispose => {if (settled) dispose(); else disposers.push(dispose);};
      const inspect = get => {
        try {
          const value = decide(get);
          if (value) queueMicrotask(() => finish(value));
        } catch (error) {queueMicrotask(() => finish(null, error));}
      };
      try {
        register(scope.watch(({get}) => inspect(get)));
        if (manager) register(manager.subscribe({type: 'conversation', key: hostThread,
          listener: () => inspect((atom, argument) => scope.get(atom, argument))}));
      } catch (error) {finish(null, error);}
    });
  }
  if ('updates' in result) {
    // Re-read immediately before writing: the user may have selected another
    // question or typed a new draft while the acknowledgement was arriving.
    const fresh = decide((atom, argument) => scope.get(atom, argument));
    if (!fresh || !('updates' in fresh)) return fresh ?? {status: 'awaiting-native-acceptance'};
    for (const update of fresh.updates) scope.set(question, update.key, update.state);
    if ('next' in fresh) select(scope, hostThread, fresh.next);
    return {status: fresh.status};
  }
  return {status: result.status};
})
