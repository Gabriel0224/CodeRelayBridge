"""Recognize fixed, system-owned Debian installations; keep user data separate."""
import json
import os
from pathlib import Path
import sys
BASE = Path('/opt/coderelaybridge')

def deb_layout(executable=None, role=None):
    if not sys.platform.startswith('linux'):
        return None
    executable = Path(executable or sys.executable).absolute()
    for candidate_role, product in [('client', 'RelayClient'), ('server', 'RelayServer')]:
        if role and role != candidate_role:
            continue
        directory = BASE / candidate_role
        if executable != directory / product or directory.is_symlink():
            continue
        try:
            if any((p.is_symlink() or p.stat().st_uid != 0 or p.stat().st_mode & 18 for p in (BASE.parent, BASE, directory, executable))):
                return None
            marker = directory / 'CRB-INSTALL.json'
            if marker.is_symlink() or marker.stat().st_uid != 0 or marker.stat().st_mode & 18:
                return None
            value = json.loads(marker.read_text(encoding='utf-8'))
            identity = json.loads((directory / '_internal/relay-edition.json').read_text(encoding='utf-8'))
            if value.get('manager') != 'deb' or value.get('role') != candidate_role or value.get('schema') != 1 or (value.get('edition') != identity.get('edition')) or (identity.get('role') != candidate_role) or (directory.stat().st_uid != 0) or (directory.stat().st_mode & 18):
                return None
            return dict(value, directory=str(directory), executable=str(executable))
        except (OSError, ValueError):
            return None
    return None

def deb_package_name(role):
    if role not in {'client', 'server'}:
        raise ValueError('Invalid native package identity')
    return 'coderelaybridge-' + role
