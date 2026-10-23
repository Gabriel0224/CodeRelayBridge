"""Explicit conversation edits and first-turn settings, using native APIs."""
import time
import uuid

def create_configured(agent, payload, project, model, command_id=None):
    """Create without inference, attach Desktop, then submit the first turn once.

    Native create_thread has no permission/tier arguments. Applying settings
    after it returns would let the first turn run with the wrong settings.
    """
    if not agent.appserver:
        raise RuntimeError('首轮设置需要目标 Codex app-server')
    full = payload.get('fullAccess')
    if not isinstance(full, bool):
        raise ValueError('完全访问设置无效')
    tier = payload.get('serviceTier')
    if tier is not None and (not isinstance(tier, str) or not model or tier not in {row['id'] for row in model.get('serviceTiers', [])}):
        raise ValueError('该模型不支持所选加速模式')
    cwd = project.get('path') if project else str(agent.home.parent)
    profiles = agent.appserver.request('permissionProfile/list', {'cwd': cwd, 'limit': 100})
    profile = ':danger-full-access' if full else ':workspace'
    if not any((row.get('id') == profile and row.get('allowed') for row in profiles.get('data', []))):
        raise ValueError('原 App 不允许选择这个权限配置')
    settings = {'permissions': profile, 'approvalPolicy': 'never' if full else 'on-request', 'approvalsReviewer': 'user'}
    if payload.get('model'):
        settings['model'] = payload['model']
    if tier is not None:
        settings['serviceTier'] = tier
    result = None
    send_attempted = False
    send_rejected = False
    try:
        with agent.appserver.session(timeout=60) as request:
            started = request('thread/start', {'cwd': cwd, 'ephemeral': False, **settings, **({'projectId': project['id']} if project else {})})
            tid = str(started.get('thread', {}).get('id') or '')
            if not tid:
                raise RuntimeError('原 App 未返回新对话标识；请核对后再操作')
            result = {'ok': True, 'threadId': tid, 'transport': 'desktop-native-first-turn', 'initialMessagePending': True, 'fullAccess': full, 'serviceTier': tier}
            request('thread/inject_items', {'threadId': tid, 'items': [{'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'Conversation initialized. Await the user task.'}]}]})
        agent.read_catalog()
        with agent.lock:
            agent.wanted.add(tid)
            agent.followed.discard(tid)
        agent.attach_desktop_thread(tid)
        owner = agent.desktop_thread_owner(tid)
        response = agent.ipc.request('thread-follower-update-thread-settings', {'conversationId': tid, 'threadSettings': {**settings, **({'effort': payload['effort']} if payload.get('effort') else {})}, 'activeTurnId': None}, version=2, target=owner, timeout=30)
        if response.get('result', {}).get('applied') is not True:
            raise RuntimeError('原 App 未接受首轮设置，任务尚未发送')
        native_id = command_id or str(uuid.uuid4())
        request = {'threadId': tid, 'clientUserMessageId': native_id, 'input': [{'type': 'text', 'text': payload['prompt'].strip(), 'text_elements': []}], 'turnTrigger': 'submit', **settings}
        if payload.get('effort'):
            request['effort'] = payload['effort']
        send_attempted = True
        response = agent.deliver_native_message(tid, native_id, owner, 'thread-follower-start-turn', {'conversationId': tid, 'turnStart': {'request': request, 'context': {'inheritThreadSettings': True}}}, version=2)
        if response.get('result', {}).get('ok') is False:
            send_rejected = True
            raise RuntimeError('原 App 未确认首条消息，请先核对原对话')
        result['initialMessagePending'] = False
    except Exception as exc:
        if result is None:
            raise
        result.update(initialMessageWarning=str(exc), initialMessageOutcomeUnknown=send_attempted and (not send_rejected))
    result['confirmedAt'] = time.time()
    return result
