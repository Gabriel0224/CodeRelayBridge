"""Scoped operations shared by the CRB workbench and the isolated trial."""
from pathlib import Path
from itertools import islice
from uuid import UUID
import hashlib
import json
WRITES = {'create', 'send'}
READS = {'children', 'history', 'chats', 'occurrences', 'names', 'content', 'preview', 'diff'}

class Workbench:

    def __init__(self, rpc, send, search_files, review):
        self.rpc, self.send = (rpc, send)
        self.search_files, self.review = (search_files, review)

    def execute(self, parent, project_threads, cwd, payload, write=False):
        op = payload.get('op')
        if op not in (WRITES if write else READS) or parent not in project_threads:
            raise ValueError('对话或操作不可用')
        target = str(payload.get('target') or parent)
        if target != parent:
            UUID(target)
            row = self.rpc('thread/read', {'threadId': target})['thread']
            if row.get('attachedToThreadId') != parent:
                raise ValueError('侧聊属于另一条主对话')
        cursor = payload.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 8192):
            raise ValueError('分页位置无效')
        if op == 'children':
            page = self.rpc('thread/list', dict(attachedToThreadId=parent, limit=30, cursor=cursor))
            return {'rows': [{'id': r['id'], 'name': r.get('name') or r.get('preview', '')[:60] or '侧聊'} for r in page['data'] if r.get('attachedToThreadId') == parent], 'cursor': page.get('nextCursor')}
        if op == 'create':
            row = self.rpc('thread/fork', dict(threadId=parent, attachToSource=True))['thread']
            if row.get('attachedToThreadId') != parent:
                raise RuntimeError('尚未确认侧聊归属，请刷新列表核对')
            return {'id': row['id']}
        if op == 'send':
            message = payload.get('text')
            if not isinstance(message, str) or not message.strip() or len(message) > 12000:
                raise ValueError('请输入 1 至 12000 字')
            receipt = self.send(target, message)
            if isinstance(receipt, dict) and (receipt.get('ok') is False or receipt.get('unknown')):
                raise RuntimeError(receipt.get('error') or '尚未确认发送结果，请先核对侧聊记录')
            return {'accepted': True}
        if op == 'history':
            page = self.rpc('thread/turns/list', dict(threadId=target, limit=12, itemsView='full', cursor=cursor))
            rows = []
            for turn in reversed(page['data']):
                for item in turn.get('items', []):
                    kind = item.get('type')
                    if kind == 'userMessage':
                        body = '\n'.join((c.get('text', '') for c in item.get('content', []) if c.get('type') == 'text'))
                    elif kind == 'agentMessage':
                        body = item.get('text', '')
                    else:
                        continue
                    rows.append(dict(id=item['id'], role='你' if kind == 'userMessage' else 'Codex', text=body[:10000]))
            result = {'rows': rows[-80:], 'cursor': page.get('nextCursor'), 'active': any((t.get('status') == 'inProgress' for t in page['data']))}
            revision = hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            if not cursor and payload.get('since') == revision:
                return {'unchanged': True, 'revision': revision, 'active': result['active']}
            return dict(result, revision=revision)
        if op == 'diff':
            if payload.get('view') not in ('unstaged', 'staged'):
                raise ValueError('未知的比较范围')
            return self.review(cwd, {'view': payload['view']})
        if not cwd:
            raise ValueError('当前对话没有项目目录')
        root = Path(cwd).resolve(strict=True)
        if op == 'preview':
            relative = Path(str(payload.get('path', '')))
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('文件不在选定项目内')
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError('仅支持项目内 1 MiB 以内的文本文件')
            line = max(1, min(1000000, int(payload.get('line') or 1)))
            start = max(1, line - 8)
            with path.open(encoding='utf-8') as stream:
                lines = [s.rstrip('\r\n')[:600] for s in islice(stream, start - 1, line + 16)]
            return dict(start=start, lines=lines, line=line)
        q = payload.get('query')
        if not isinstance(q, str) or not q.strip() or len(q) > 256 or (not q.isprintable()):
            raise ValueError('请输入最多 256 字的单行查询')
        if op == 'chats':
            page = self.rpc('thread/search', dict(searchTerm=q, limit=30, cursor=cursor, includeAttachedThreads=False))
            return {'rows': [dict(id=h['thread']['id'], name=project_threads[h['thread']['id']], snippet=h['snippet'][:480]) for h in page['data'] if h['thread']['id'] in project_threads], 'cursor': page.get('nextCursor')}
        if op == 'occurrences':
            page = self.rpc('thread/searchOccurrences', dict(threadId=target, searchTerm=q, limit=30, cursor=cursor))
            return dict(rows=[dict(id=h['itemId'], snippet=h['snippet'][:480], turnCursor=h.get('turnCursor')) for h in page['data']], cursor=page.get('nextCursor'))
        if op == 'names':
            page = self.rpc('fuzzyFileSearch', dict(roots=[str(root)], query=q, cancellationToken=None))
            return {'rows': [{'path': r['path'], 'line': 1} for r in page['files'][:100] if r.get('match_type') != 'directory']}
        return self.search_files(dict(path=str(root), query=q, glob=payload.get('glob', '*')))
