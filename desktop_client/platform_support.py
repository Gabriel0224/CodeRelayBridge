"""Native paths and launch descriptions, shared by client and server UI."""
import os
from pathlib import Path
import sys

def platform_name():
    return 'Windows' if sys.platform == 'win32' else 'macOS' if sys.platform == 'darwin' else 'Linux'

def data_root(product='CodexRelayClient', home=None):
    home = Path(home or Path.home())
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / product
    if sys.platform == 'darwin':
        return home / 'Library/Application Support' / product
    return Path(os.environ.get('XDG_DATA_HOME', home / '.local/share')) / product

def installed_executable(root, product='RelayClient'):
    root = Path(root)
    if sys.platform == 'win32':
        return root / 'app' / (product + '.exe')
    if sys.platform == 'darwin':
        title = 'Relay Client' if product == 'RelayClient' else 'Relay Server'
        return root / 'app' / (title + '.app') / 'Contents/MacOS' / product
    return root / 'app' / product

def packaged_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))

def harness_payload(home=None, harness_home=None, preferred=None):
    """Discover an installed Linux RC2 payload, never start another engine."""
    if not sys.platform.startswith('linux'):
        return None
    home = Path(home or Path.home())
    candidates = ([Path(preferred)] if preferred else []) + [home / '.local/share/deepseek-harness/resources/dsh', Path('/opt/deepseek-harness/resources/dsh'), Path('/usr/lib/deepseek-harness/resources/dsh')]
    cache = Path(harness_home or home / '.dsh') / 'desktop/payloads'
    if cache.is_dir():
        candidates += sorted(cache.iterdir(), key=lambda root: root.stat().st_mtime, reverse=True)
    roots = [root for root in candidates if root.is_dir() and (root / 'node/bin/node').is_file() and (root / 'runtime/node_modules/@deepseek-ai/dsh/lib/bin.js').is_file()]
    nodes = {str((root / 'node/bin/node').resolve()): root for root in roots}
    proc = Path('/proc')
    if proc.is_dir():
        for process in proc.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                root = nodes.get(str((process / 'exe').resolve()))
                if root is None:
                    continue
                args = (process / 'cmdline').read_bytes().decode().split('\x00')
                if args[1:3] == [str(root.resolve() / 'runtime/node_modules/@deepseek-ai/dsh/lib/bin.js'), 'web']:
                    env = dict((v.split('=', 1) for v in (process / 'environ').read_bytes().decode().split('\x00') if '=' in v))
                    data_home = env.get('DSH_HOME', '').strip() or str(Path(env.get('HOME', '/')) / '.dsh')
                    if Path(data_home).expanduser().resolve() != Path(harness_home or home / '.dsh').resolve():
                        continue
                    return str(root.resolve())
            except (OSError, UnicodeError):
                continue
    if roots:
        return str(roots[0].resolve())
    return None
