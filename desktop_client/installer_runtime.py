"""Scoped uninstall cleanup. Never revoke pairing or touch original model Apps."""
from pathlib import Path
import sys
import psutil

def uninstall(role):
    from desktop_client.platform_support import data_root, installed_executable
    root = data_root('CodexRelayClient' if role == 'client' else 'CodexRelayServer')
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    target = installed_executable(root, product).resolve()
    if Path(sys.executable).resolve() != target or not getattr(sys, 'frozen', False):
        raise RuntimeError('Installer cleanup is only valid for the owned installed application')
    for process in psutil.process_iter(['pid', 'exe']):
        if process.pid != psutil.Process().pid and process.info.get('exe') and (Path(process.info['exe']).resolve() == target):
            raise RuntimeError('Quit Relay and stop its Relay service before uninstalling')
    if role == 'server':
        from desktop_server.backend import ServerManager
        from desktop_server.install import ServerStartup
        manager = ServerManager(root=root)
        if manager.config_path().exists():
            tasks, spec = (manager.tasks(), manager.spec())
            if tasks.exists(spec):
                tasks.pause(spec)
                tasks.remove(spec)
        startup = ServerStartup()
    else:
        from desktop_client.backend import BridgeManager, StartupShortcut
        manager = BridgeManager()
        for spec in manager.specs.values():
            if manager.tasks.exists(spec):
                manager.tasks.pause(spec)
                manager.tasks.remove(spec)
        startup = StartupShortcut()
    if startup.enabled():
        startup.configure(False, target)
    return {'passed': True, 'role': role, 'configurationRemoved': False, 'pairingRevoked': False, 'originalAppsStopped': False}
