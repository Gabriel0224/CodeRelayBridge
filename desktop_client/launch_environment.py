"""Keep the real desktop's input method; never lend packaged Qt paths to Apps."""
import os
import subprocess
import sys
import psutil
SESSION_KEYS = frozenset({'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP', 'GTK_IM_MODULE', 'QT_IM_MODULE', 'SDL_IM_MODULE', 'XMODIFIERS', 'LANG', 'LC_CTYPE'})
DESKTOPS = {'cinnamon', 'gnome-shell', 'plasmashell', 'xfce4-session', 'mate-session', 'lxqt-session', 'sway', 'weston'}

def choose_session(base, candidates):
    """Refuse a different display or ambiguous multi-session desktop."""
    matching = []
    for env in candidates:
        if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
            continue
        if any((base.get(key) and env.get(key) != base[key] for key in ('DISPLAY', 'WAYLAND_DISPLAY'))):
            continue
        matching.append({key: value for key, value in env.items() if key in SESSION_KEYS})
    identities = {(e.get('DISPLAY'), e.get('WAYLAND_DISPLAY'), e.get('XDG_RUNTIME_DIR')) for e in matching}
    if len(identities) != 1:
        return {}
    result = {}
    for key in SESSION_KEYS:
        values = {e[key] for e in matching if e.get(key)}
        if len(values) == 1:
            result[key] = values.pop()
    return result

def desktop_session_environment(base):
    if not sys.platform.startswith('linux'):
        return {}
    current = psutil.Process().username()
    candidates = []
    for process in psutil.process_iter(['name', 'username']):
        try:
            if process.info['username'] == current and process.info['name'] in DESKTOPS:
                candidates.append(process.environ())
        except (psutil.Error, OSError):
            continue
    if any((env.get('DISPLAY') or env.get('WAYLAND_DISPLAY') for env in candidates)):
        return choose_session(base, candidates)
    try:
        output = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True, text=True, timeout=3, check=True).stdout
        env = dict((line.split('=', 1) for line in output.splitlines() if '=' in line))
        return choose_session(base, [env])
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}

def gui_environment(base=None):
    env = dict(os.environ if base is None else base)
    env.update(desktop_session_environment(env))
    return env

def prepare_gui_environment():
    os.environ.update(gui_environment())

def _private_path(value):
    normalized = value.replace('\\', '/').casefold()
    root = getattr(sys, '_MEIPASS', None)
    if root:
        prefix = str(root).replace('\\', '/').casefold().rstrip('/')
        if normalized == prefix or normalized.startswith(prefix + '/'):
            return True
    return '/codexrelayclient/' in normalized or '/relayclient/_internal/' in normalized or '/relay client.app/contents/frameworks/' in normalized

def external_app_environment(base=None):
    env = gui_environment(base)
    if sys.platform == 'win32':
        return env
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
        if key in env:
            paths = [p for p in env[key].split(os.pathsep) if p and (not _private_path(p))]
            if paths:
                env[key] = os.pathsep.join(paths)
            else:
                env.pop(key, None)
    for key in list(env):
        if key.startswith('_PYI_'):
            env.pop(key, None)
    return env
