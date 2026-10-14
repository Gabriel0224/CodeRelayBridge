"""One parent map per Windows snapshot; no WMI, shell, cache or daemon."""
import sys
import time
import psutil

def windows_creation_time(process):
    """Protected processes are unknown, not a reason for a full-system fallback."""
    value = getattr(process, '_create_time', None)
    if value is not None:
        return value
    try:
        return process._proc.create_time(fast_only=True)
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, AttributeError, TypeError):
        return None

def windows_parent_snapshot():
    if sys.platform != 'win32':
        return None
    try:
        from desktop_client.startup_diagnostics import boot_mark
        boot_mark('process.parents.begin')
        from psutil._pswindows import ppid_map
        captured_at = time.time()
        parents = ppid_map()
        boot_mark('process.parents.end.count-' + str(len(parents)))
        if isinstance(parents, dict) and parents:
            return (parents, captured_at)
    except (ImportError, OSError, AttributeError) as error:
        from desktop_client.startup_diagnostics import boot_mark
        boot_mark('process.parents.fallback-' + type(error).__name__)
    return None

def iter_process_info(attrs):
    from desktop_client.startup_diagnostics import profile_process_scan
    with profile_process_scan():
        yield from _iter_process_info(attrs)

def _iter_process_info(attrs):
    attrs = list(attrs)
    snapshot = windows_parent_snapshot() if 'ppid' in attrs else None
    fast_creation = sys.platform == 'win32' and 'create_time' in attrs
    queried = [key for key in attrs if not (key == 'ppid' and snapshot is not None) and (not (key == 'create_time' and fast_creation))]
    from desktop_client.startup_diagnostics import boot_mark
    boot_mark('process.attributes-' + ','.join(queried))
    for process in psutil.process_iter(queried):
        row = dict(process.info)
        if fast_creation:
            row['create_time'] = windows_creation_time(process)
        if snapshot is not None:
            parents, captured_at = snapshot
            if row['pid'] not in parents:
                continue
            created = row.get('create_time')
            if isinstance(created, (int, float)) and created > captured_at:
                continue
            row['ppid'] = parents[row['pid']]
        yield (process, row)
