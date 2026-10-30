"""Read-only mapping between Desktop project IDs and app-server project IDs."""
import json
import os
from pathlib import Path

def path_key(value):
    value = str(value or '')
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))

def _desktop_state(home):
    try:
        state = json.loads((Path(home) / '.codex-global-state.json').read_text(encoding='utf-8-sig'))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}

def _project_id_map(home, state):
    """Only the current local Codex home; never another connected host's IDs."""
    try:
        scopes = state.get('app-server-project-id-by-legacy-project-id-by-host', {})
        if not isinstance(scopes, dict):
            return {}
        matches = [value for key, value in scopes.items() if isinstance(key, str) and key.startswith('local:') and (path_key(key[6:]) == path_key(home)) and isinstance(value, dict)]
        if len(matches) != 1:
            return {}
        return {key: value for key, value in matches[0].items() if isinstance(key, str) and key and isinstance(value, str) and value}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}

def project_id_map(home):
    return _project_id_map(home, _desktop_state(home))

def thread_project_memberships(home, project_ids):
    """Explicit Desktop memberships, including removal from a project.

    Missing keys allow the older database/folder fallback. Present None values
    must not fall back: deleted, cloud and explicitly projectless memberships
    cannot borrow a different project's shared directory.
    """
    state = _desktop_state(home)
    mapping = _project_id_map(home, state)
    assignments = state.get('thread-project-assignments', {})
    hosts = state.get('thread-project-membership-host-ids', {})
    hosts = hosts if isinstance(hosts, dict) else {}
    result = {}
    if isinstance(assignments, dict):
        for thread, value in assignments.items():
            if not isinstance(thread, str) or not isinstance(value, dict) or (not isinstance(value.get('projectId'), str)) or (hosts.get(thread, 'local') != 'local'):
                continue
            project = mapping.get(value['projectId'], value['projectId'])
            result[thread] = project if value.get('projectKind') == 'local' and project in project_ids else None
    projectless = state.get('projectless-thread-ids', [])
    for thread in projectless if isinstance(projectless, list) else []:
        if isinstance(thread, str) and hosts.get(thread, 'local') == 'local':
            result[thread] = None
    return result

def local_projects(value):
    rows = value.get('projects', []) if isinstance(value, dict) else []
    return [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict) and item.get('projectKind') == 'local' and isinstance(item.get('projectId'), str) and item['projectId']]

def project_paths(project):
    roots = project.get('roots', project.get('rootPaths', []))
    paths = {path_key(root) for root in (roots if isinstance(roots, list) else []) if isinstance(root, str) and root}
    if isinstance(project.get('path'), str) and project['path']:
        paths.add(path_key(project['path']))
    return paths

def desktop_project_id(home, project, desktop_projects, backend_projects=()):
    """Resolve an exact native registration; a matching display name is not identity."""
    mapping = project_id_map(home)
    backend_id = project.get('id')
    roots = project_paths(project)
    mapped = [row for row in desktop_projects if mapping.get(row['projectId']) == backend_id]
    if mapped:
        matches = [row for row in mapped if project_paths(row) & roots]
    else:
        matches = [row for row in desktop_projects if mapping.get(row['projectId'], backend_id) == backend_id and project_paths(row) & roots]
        exact = [row for row in matches if row['projectId'] == backend_id]
        if exact:
            matches = exact
        elif any((row.get('id') != backend_id and project_paths(row) & roots for row in backend_projects)):
            matches = []
    if len(matches) != 1:
        raise RuntimeError('无法确认项目在目标 Codex Desktop 中的登记，请刷新项目或先在原 App 中添加该项目')
    return matches[0]['projectId']

def registered_backend_project(home, desktop_id, roots, backend_projects):
    mapping = project_id_map(home)
    backend_id = mapping.get(desktop_id, desktop_id)
    expected = {path_key(root) for root in roots}
    matches = [row for row in backend_projects if row.get('id') == backend_id and project_paths(row) == expected]
    return matches[0] if len(matches) == 1 else None
