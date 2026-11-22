"""Explicit, owner-scoped repair of the native Windows server task's DACL."""
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from desktop_client.backend import ClientError, normalized_path
from desktop_client.connections import atomic_json
KIND = 'server-task-permissions'
CONTROL = 1180095
_LOCK = threading.Lock()

def current_sid():
    import win32api
    import win32security as security
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
    try:
        return security.ConvertSidToStringSid(security.GetTokenInformation(token, security.TokenUser)[0])
    finally:
        token.Close()

def plan():
    return dict(schema=1, kind=KIND, role='server', executable=str(Path(sys.executable).resolve()), userSid=current_sid())

def validate_plan(value):
    from desktop_client.system_setup import packaged_role
    if not isinstance(value, dict) or set(value) != {'schema', 'kind', 'role', 'executable', 'userSid'} or value.get('schema') != 1 or (value.get('kind') != KIND) or (value.get('role') != 'server') or (sys.platform != 'win32') or (not getattr(sys, 'frozen', False)) or (packaged_role() != 'server') or (Path(value.get('executable', '')).resolve() != Path(sys.executable).resolve()) or (value.get('userSid') != current_sid()):
        raise ClientError('启动权限修复仅适用于当前用户的已安装服务端；拒绝跨用户或其他程序。')

def expanded_mask(mask):
    for generic, specific in ((268435456, 2032127), (2147483648, 1179785), (1073741824, 1179926), (536870912, 1179808)):
        if mask & generic:
            mask |= specific
    return mask

def grant_rows(rows, sid):
    """Preserve every other ACE, including denies; do not grant ACL/owner editing."""
    if not re.fullmatch('S-1-5-21-(?:[0-9]+-){3}[0-9]+', sid):
        raise ClientError('启动任务用户身份无效。')
    result, selected = ([], False)
    for kind, flags, mask, owner in rows:
        if kind not in (0, 1):
            raise ClientError('启动任务包含特殊权限策略，请由管理员检查；未自动改写。')
        if kind == 1 and owner == sid and expanded_mask(mask) & CONTROL:
            raise ClientError('启动任务明确拒绝当前用户访问，请由管理员检查；未移除拒绝规则。')
        if kind == 0 and owner == sid and (not flags & 24) and (not selected):
            result.append((kind, flags, mask | CONTROL, owner))
            selected = True
        else:
            result.append((kind, flags, mask, owner))
    if not selected:
        index = next((i for i, row in enumerate(result) if row[1] & 16), len(result))
        result.insert(index, (0, 0, CONTROL, sid))
    return result

def descriptor(sddl):
    import win32security as security
    sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    acl = sd.GetSecurityDescriptorDacl()
    if acl is None:
        raise ClientError('启动任务权限未明确限定，拒绝自动修复。')
    rows = []
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if len(ace) != 3 or ace[0][0] not in (0, 1):
            raise ClientError('启动任务包含特殊权限策略，请由管理员检查。')
        rows.append((ace[0][0], ace[0][1], ace[1], security.ConvertSidToStringSid(ace[2])))
    return (sd, rows)

def effective_control(sd):
    """Check the actual caller token, including filtered administrator/deny groups."""
    import ctypes
    from ctypes import wintypes
    import win32api
    import win32security as security

    class Mapping(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in ('read', 'write', 'execute', 'all')]
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    check = api.AccessCheck
    check.argtypes = [wintypes.LPVOID, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(Mapping), wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.BOOL)]
    check.restype = wintypes.BOOL
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), 10)
    duplicate = None
    try:
        duplicate = security.DuplicateToken(token, security.SecurityImpersonation)
        raw = ctypes.create_string_buffer(bytes(sd))
        mapping = Mapping(1179785, 1179926, 1179808, 2032127)
        size, granted, allowed = (wintypes.DWORD(1024), wintypes.DWORD(), wintypes.BOOL())
        privileges = ctypes.create_string_buffer(size.value)
        if not check(raw, int(duplicate), CONTROL, ctypes.byref(mapping), privileges, ctypes.byref(size), ctypes.byref(granted), ctypes.byref(allowed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(allowed.value) and granted.value & CONTROL == CONTROL
    finally:
        if duplicate is not None:
            duplicate.Close()
        token.Close()

def validate_task(task, spec, sid):
    import win32security as security
    definition = task.Definition
    principal = definition.Principal
    actual = str(principal.UserId)
    if not actual.startswith('S-1-'):
        actual = security.ConvertSidToStringSid(security.LookupAccountName(None, actual)[0])
    action = definition.Actions.Item(1) if definition.Actions.Count == 1 else None
    if spec.key != 'server' or spec.task_name != 'Codex Relay Server' or actual != sid or (int(principal.LogonType) != 3) or (int(principal.RunLevel) != 0) or (action is None) or (int(action.Type) != 0) or (str(action.Arguments).strip() != '--bridge server') or (normalized_path(action.Path) != normalized_path(spec.managed_executable)) or (normalized_path(action.WorkingDirectory) != normalized_path(spec.managed_executable.parent)):
        raise ClientError('启动任务的程序、用户或权限级别不匹配，拒绝自动修复。')

def inspect(manager):
    tasks, spec, sid = (manager.tasks(), manager.spec(), current_sid())
    if not tasks.exists(spec):
        return {'present': False, 'needsRepair': False}

    def read(task):
        validate_task(task, spec, sid)
        sd, rows = descriptor(task.GetSecurityDescriptor(7))
        grant = 0
        for kind, flags, mask, owner in rows:
            if kind == 0 and owner == sid and (not flags & 8):
                grant |= expanded_mask(mask)
        owner_granted = grant & CONTROL == CONTROL
        effective = effective_control(sd)
        return dict(present=True, needsRepair=not (owner_granted and effective), ownerGranted=owner_granted, effectiveControl=effective)
    return tasks.invoke(spec, read)

def apply(value):
    validate_plan(value)
    from desktop_server.backend import ServerManager
    import win32security as security
    manager = ServerManager()
    tasks, spec, sid = (manager.tasks(), manager.spec(), current_sid())
    if Path(spec.managed_executable).resolve() != Path(sys.executable).resolve():
        raise ClientError('启动权限修复入口不是当前安装的服务端。')

    def change(task):
        validate_task(task, spec, sid)
        old, xml = (str(task.GetSecurityDescriptor(7)), str(task.Xml))
        sd, rows = descriptor(old)
        proposed = grant_rows(rows, sid)
        if proposed == rows:
            return {'ok': True, 'changed': False, 'message': '当前用户已具备明确的服务启停权限。'}
        acl = security.ACL()
        for kind, flags, mask, owner in proposed:
            add = acl.AddAccessAllowedAceEx if kind == 0 else acl.AddAccessDeniedAceEx
            add(2, flags, mask, security.ConvertStringSidToSid(owner))
        sd.SetSecurityDescriptorDacl(1, acl, 0)
        candidate = security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd, 1, 4)
        try:
            task.SetSecurityDescriptor(candidate, 16)
            actual, after = descriptor(task.GetSecurityDescriptor(7))
            if after != proposed or actual.GetSecurityDescriptorOwner() != sd.GetSecurityDescriptorOwner() or actual.GetSecurityDescriptorGroup() != sd.GetSecurityDescriptorGroup() or (str(task.Xml) != xml):
                raise ClientError('启动任务权限复验不一致，正在恢复原权限。')
        except Exception:
            original, _ = descriptor(old)
            task.SetSecurityDescriptor(security.ConvertSecurityDescriptorToStringSecurityDescriptor(original, 1, 4), 16)
            raise
        return {'ok': True, 'changed': True, 'message': '已修复当前用户的服务启停权限；服务进程保持运行。'}
    return tasks.invoke(spec, change)

def repair():
    from desktop_client.system_setup import is_admin, request_authorization
    value = plan()
    validate_plan(value)
    return apply(value) if is_admin() else request_authorization(value, helper='--server-task-authorized')

def authorized_main(source, result):
    """Only the same user's pre-created result is opened, never an arbitrary admin output."""
    import stat
    import msvcrt
    import win32security as security
    source, result = (Path(source), Path(result))
    if source.name != 'plan.json' or result.name != 'result.json' or source.parent != result.parent or (not source.parent.name.startswith('relay-system-authorize-')):
        return 1
    for path in (source, result, *source.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            return 1
    fd = os.open(result, os.O_WRONLY)
    try:
        sd = security.GetSecurityInfo(msvcrt.get_osfhandle(fd), security.SE_FILE_OBJECT, security.OWNER_SECURITY_INFORMATION)
        if security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()) != current_sid():
            return 1
        try:
            value = json.loads(source.read_text(encoding='utf-8-sig'))
            data = apply(value)
        except Exception as exc:
            data = {'ok': False, 'error': str(exc) if isinstance(exc, ClientError) else '启动任务权限修复未完成，请重新检查任务状态。'}
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        os.write(fd, raw)
        os.ftruncate(fd, len(raw))
        return 0 if data.get('ok') else 1
    finally:
        os.close(fd)

def record(root, event, stage='', codes=()):
    """Bounded diagnostics without account names, SDDL, paths or exception payloads."""
    path = Path(root) / 'task-permissions.json'
    item = {'time': time.time(), 'event': event, 'stage': str(stage)[:40], 'codes': [code for code in codes if re.fullmatch('0x[0-9A-F]{8}', code)]}
    try:
        with _LOCK:
            try:
                rows = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(rows, list) or not all((isinstance(row, dict) for row in rows)):
                    rows = []
            except (OSError, ValueError):
                rows = []
            if rows and all((rows[-1].get(k) == item[k] for k in ('event', 'stage', 'codes'))):
                return
            atomic_json(path, (rows + [item])[-20:])
    except (OSError, ValueError, TypeError):
        pass

def history(root):
    try:
        rows = json.loads((Path(root) / 'task-permissions.json').read_text(encoding='utf-8'))
        return '\n'.join((json.dumps(row, ensure_ascii=False) for row in rows[-20:])) if isinstance(rows, list) else ''
    except (OSError, ValueError):
        return ''
