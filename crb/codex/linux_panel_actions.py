"""Conversation actions against the app-server already serving the Linux desktop."""
import sqlite3
import time
import uuid

def latest(owner, tid):
    rows = owner.request('thread/turns/list', {'threadId': tid, 'itemsView': 'summary', 'sortDirection': 'desc', 'limit': 1})['data']
    return rows[0] if rows else {}

def retry_state(turn):
    from crb.codex.agent import native_capacity_retry
    return native_capacity_retry({'threadRuntimeStatus': {'type': 'idle'}}, [turn])

def continue_turn(desktop, tid, payload):
    turn = latest(desktop.owner, tid)
    capacity = retry_state(turn)
    if not turn or payload.get('turnId') not in (None, turn['id']) or (not (turn.get('status') == 'interrupted' or (capacity and capacity['canContinue']))):
        raise ValueError('当前轮次已变化，请刷新后再继续')
    trigger = 'capacity_retry_manual' if capacity else 'resume_interrupted_task'
    started = desktop.owner.request('turn/start', {'threadId': tid, 'input': [], 'turnTrigger': trigger})
    return {'ok': True, 'turnId': str(uuid.UUID(started['turn']['id']))}

def question_turns(desktop, tid, *, item_id=None, turn_id=None):
    turns = []
    cursor = None
    for _ in range(8):
        page = desktop.owner.request('thread/turns/list', {'threadId': tid, 'itemsView': 'full', 'sortDirection': 'desc', 'limit': 4, 'cursor': cursor})
        if cursor is None:
            page = desktop.live.merge(tid, page)
        turns.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor or not page['data'] or any((t['id'] == turn_id or (item_id is not None and any((i.get('id') == item_id for i in t['items']))) for t in page['data'])):
            break
    return [{**t, 'turnId': t['id']} for t in reversed(turns)]

def question_control(desktop, tid, payload):
    from crb.codex.agent import pending_async_questions
    turn_id = payload.get('turnId')
    ids = payload.get('questionIds')
    operation = payload.get('operation')
    if not isinstance(turn_id, str) or not turn_id or operation not in ('skip', 'close', 'reopen') or (not isinstance(ids, list)) or (not 1 <= len(ids) <= 8) or (not all((isinstance(i, str) for i in ids))) or (payload.get('questionId') not in ids):
        raise ValueError('问题操作无效')
    turns = question_turns(desktop, tid, turn_id=turn_id)
    item_ids = {i['id'] for t in turns if t['id'] == turn_id for i in t['items'] if i.get('id')}
    groups = pending_async_questions({'turns': turns})
    questions = next((qs for i, qs in groups if i.get('id') in item_ids and ids == [q['questionItemId'] for q in qs]), None)
    if not questions:
        raise ValueError('这组选项已变化，请刷新对话')
    control = getattr(desktop, 'control_question', None)
    if control:
        question = next((q for q in questions if q['questionItemId'] == payload['questionId']))
        turn = next((t for t in turns if t['id'] == turn_id))
        outcome = control(tid, turn, question, operation)
        if outcome == 'applied':
            return {'ok': True, 'transport': 'desktop-question-ui', 'questionUi': {'status': 'applied'}}
        if outcome == 'requested':
            return {'ok': True, 'transport': 'desktop-question-ui', 'questionUi': {'status': 'unavailable', 'requested': True, 'detail': '已请求 Codex 桌面处理，窗口恢复显示后可核对结果。'}}
    detail = '提问已在网页重新展开，可以在这里回答。' if operation == 'reopen' else '本次操作仅在网页生效。'
    return {'ok': True, 'transport': 'panel-only', 'questionUi': {'status': 'unavailable', 'detail': detail + '未能确认 Codex 桌面提问框的状态。'}}

def answer_choice(desktop, tid, cid, payload):
    from crb.codex.agent import prepare_async_answer
    item_id = payload.get('itemId')
    if not isinstance(item_id, str) or not 0 < len(item_id) <= 200:
        raise ValueError('提问标识无效')
    turns = question_turns(desktop, tid, item_id=item_id)
    text, replies, _ = prepare_async_answer({'turns': turns}, payload)
    tail = latest(desktop.owner, tid)
    if not turns or tail.get('id') != turns[-1]['id'] or tail.get('status') != turns[-1]['status']:
        raise ValueError('对话已更新，请刷新后核对提问')
    params = {'threadId': tid, 'input': [{'type': 'text', 'text': text}], 'clientUserMessageId': cid}
    sock = desktop.owner.open()
    try:
        try:
            with desktop.agent.connect_journal() as db:
                db.execute('CREATE TABLE IF NOT EXISTS native_question_answers (thread TEXT, question TEXT, command TEXT, created REAL, PRIMARY KEY(thread,question))')
                now = time.time()
                db.execute('DELETE FROM native_question_answers WHERE created < ?', (now - 7 * 86400,))
                db.executemany('INSERT INTO native_question_answers VALUES (?,?,?,?)', [(tid, r['questionItemId'], cid, now) for r in replies])
        except sqlite3.IntegrityError:
            raise ValueError('该问题的回答已提交，请等待原 App 同步') from None
        try:
            if tail['status'] == 'inProgress':
                result = desktop.owner.call(sock, 'turn/steer', {**params, 'expectedTurnId': tail['id']})
            else:
                result = desktop.owner.call(sock, 'turn/start', params)
        except RuntimeError:
            with desktop.agent.connect_journal() as db:
                db.execute('DELETE FROM native_question_answers WHERE thread=? AND command=?', (tid, cid))
            raise
        if tail['status'] == 'inProgress' and result.get('turnId') != tail['id']:
            raise ValueError('原 App 的回答回执不匹配，请刷新核对')
        if tail['status'] != 'inProgress':
            uuid.UUID(result['turn']['id'])
    finally:
        sock.close(timeout=0.5)
    return {'ok': True, 'clientUserMessageId': cid, 'questionUi': {'status': 'awaiting-native-acceptance'}}

def manage_thread(desktop, tid, action, payload):
    from crb.codex.agent import app_tool_json
    archive = action in ('archive', 'unarchive')
    if action == 'rename_thread':
        name = payload.get('name')
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 240:
            raise ValueError('对话名称需为 1–240 个字符')
        if 'set_thread_title' in desktop.desktop_tools:
            desktop.desktop_tool('set_thread_title', {'threadId': tid, 'title': name.strip()}, tid)
        else:
            desktop.owner.request('thread/name/set', {'threadId': tid, 'name': name.strip()})
    elif archive:
        if action == 'archive' and latest(desktop.owner, tid).get('status') == 'inProgress':
            raise ValueError('请等本轮结束后再归档')
        if 'set_thread_archived' in desktop.desktop_tools:
            desktop.desktop_tool('set_thread_archived', {'threadId': tid, 'archived': action == 'archive'}, tid)
        else:
            desktop.owner.request('thread/' + action, {'threadId': tid})
    else:
        env = payload.get('environment')
        if env is not None:
            if env not in ('worktree', 'same-directory') or payload.get('lastTurnId'):
                raise ValueError('分支位置无效')
            value = app_tool_json(desktop.desktop_tool('fork_thread', {'threadId': tid, 'environment': {'type': env}}, tid))
            fork = value.get('threadId')
            client_id = value.get('clientThreadId')
            if not fork and (not client_id):
                raise ConnectionError('原 App 未确认分支，请刷新核对')
            return {'ok': True, 'threadId': fork, 'clientThreadId': client_id, 'sourceThreadId': tid, 'pending': not fork}
        params = {'threadId': tid, 'excludeTurns': True}
        if payload.get('lastTurnId'):
            params['lastTurnId'] = str(uuid.UUID(payload['lastTurnId']))
        fork = desktop.owner.request('thread/fork', params)['thread']
        return {'ok': True, 'threadId': str(uuid.UUID(fork['id'])), 'sourceThreadId': tid, 'title': fork.get('name') or '新分支'}
    desktop.agent.read_catalog()
    return {'ok': True, 'threadId': tid}
