"""Optional original-App callback, using the existing verified loopback channel."""
from pathlib import Path
import sys
from crb.codex.question_ui import CodexQuestionUI, discover_debugger
PROGRAM = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')

def call(request):
    endpoint = discover_debugger()
    if not endpoint:
        raise RuntimeError('原 App 本机联动通道不可用，请在原 App 查看或调整；不会自动重试')
    return CodexQuestionUI()._call(endpoint, {**request, 'platform': sys.platform}, program=PROGRAM, statuses={'ok'})

def fork_status(agent, tid, payload):
    import json
    cid, client_id = (payload.get('commandId'), payload.get('clientThreadId'))
    if not isinstance(cid, str) or not isinstance(client_id, str):
        raise ValueError('分支确认标识无效')
    with agent.connect_journal() as db:
        row = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
    saved = json.loads(row[0]) if row and row[0] else {}
    if saved.get('ok') is not True or saved.get('sourceThreadId') != tid or saved.get('clientThreadId') != client_id:
        raise ValueError('只能检查本面板已提交的这个分支请求')
    if saved.get('threadId'):
        return saved
    actual = call({'operation': 'fork-status', 'clientThreadId': client_id})
    if not actual.get('threadId'):
        return {**saved, 'pending': True}
    confirmed = {**saved, 'threadId': actual['threadId'], 'pending': False}
    with agent.connect_journal() as db:
        cursor = db.execute('UPDATE commands SET result=? WHERE id=? AND result=?', (json.dumps(confirmed), cid, row[0]))
        if cursor.rowcount != 1:
            raise RuntimeError('分支确认状态已变化，请重新检查')
    with agent.lock:
        agent.results[cid] = confirmed
    agent.read_catalog()
    return confirmed

def capabilities(agent):
    endpoint = discover_debugger()
    if not endpoint:
        return {}
    identity = json_identity(endpoint)
    cached = getattr(agent, '_sidebar_ui_capabilities', None)
    if cached and cached[0] == identity:
        return cached[1]
    try:
        result = call({'operation': 'capabilities'})
    except Exception:
        result = {}
    agent._sidebar_ui_capabilities = (identity, result)
    return result

def json_identity(endpoint):
    import json
    return json.dumps(endpoint, sort_keys=True, default=str)
