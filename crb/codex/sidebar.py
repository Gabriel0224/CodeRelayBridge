"""Read native sidebar metadata without guessing from a model/name or writing it."""
import json

def native_thread_metadata(row, sections=None):
    try:
        source = json.loads(row['native_source'] or 'null')
    except (ValueError, TypeError):
        source = row['native_source']
    child = source.get('subagent', source.get('subAgent')) if isinstance(source, dict) else {} if source in ('subagent', 'subAgent') else None
    spawned = child.get('thread_spawn', child.get('threadSpawn', {})) if isinstance(child, dict) else {}
    spawned = spawned if isinstance(spawned, dict) else {}
    is_child = 'subagent' in source or 'subAgent' in source if isinstance(source, dict) else source in ('subagent', 'subAgent')
    fields = {'isSubagent': is_child, 'isPinned': bool(row['native_pinned'])}
    for name, keys in (('parentThreadId', ('parent_thread_id', 'parentThreadId')), ('agentPath', ('agent_path', 'agentPath')), ('agentNickname', ('agent_nickname', 'agentNickname')), ('agentRole', ('agent_role', 'agentRole'))):
        value = next((spawned.get(key) for key in keys if spawned.get(key)), '')
        if not value and name != 'parentThreadId':
            value = row['native_' + keys[0]] or ''
        if isinstance(value, str) and value.isprintable() and (len(value) <= 500):
            fields[name] = value
    section = row['native_section']
    if section:
        fields.update(sectionId=section, sectionName=(sections or {}).get(section, ''))
    return fields

def native_subagent_predicate(columns):
    if 'source' not in columns:
        return '0'
    return "COALESCE(CASE WHEN json_valid(source) THEN json_type(source,'$.subagent') IS NOT NULL OR json_type(source,'$.subAgent') IS NOT NULL ELSE source IN ('subagent','subAgent') END,0)"
