"""Per-user systemd/launchd services. Only exact Relay executables are owned."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import time
import sqlite3
from desktop_client.backend import ClientError, matching_processes, process_rows, native_app_running, request_bridge_stop
from desktop_client.connections import atomic_json, client_root

def service_name(spec):
    if spec.key == 'server':
        profile = spec.runtime_file.parent.parent
        if profile.name.startswith('CodexRelayServer-SetupTest-'):
            import hashlib
            suffix = hashlib.sha256(str(profile).encode()).hexdigest()[:8]
            return 'com.codexrelay.server.test.' + suffix if sys.platform == 'darwin' else 'relay-server-test-' + suffix + '.service'
        return 'com.codexrelay.server.core' if sys.platform == 'darwin' else 'relay-server.service'
    return 'com.codexrelay.client.' + spec.key if sys.platform == 'darwin' else 'relay-client-' + spec.key + '.service'

def unit_text(spec):

    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    return '[Unit]\nDescription=Relay Client ' + spec.title + '\nAfter=network-online.target\n[Service]\nType=simple\nExecStart=' + quote(spec.managed_executable) + ' --bridge ' + spec.key + '\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=12\nUMask=0077\n[Install]\nWantedBy=default.target\n'

def plist_data(spec):
    return {'Label': service_name(spec), 'ProgramArguments': [str(spec.managed_executable), '--bridge', spec.key], 'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Background', 'StandardOutPath': str(spec.runtime_file.with_suffix('.service.log')), 'StandardErrorPath': str(spec.runtime_file.with_suffix('.service.log')), 'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'}, 'Umask': 63}

class PosixTasks:

    def __init__(self, home=None, runner=subprocess.run):
        self.home = Path(home or Path.home())
        self.runner = runner

    def path(self, spec):
        return self.home / 'Library/LaunchAgents' / (service_name(spec) + '.plist') if sys.platform == 'darwin' else self.home / '.config/systemd/user' / service_name(spec)

    def legacy(self, spec):
        labels = {'codex': f'com.{self.home.name}.codex-desktop-relay-agent', 'deepseek': f'com.{self.home.name}.deepseek-harness-relay-agent'} if sys.platform == 'darwin' else {'codex': 'codex-desktop-agent.service', 'deepseek': 'deepseek-harness-relay.service'}
        label = labels.get(spec.key)
        if not label or not spec.config.exists():
            return None
        path = self.home / ('Library/LaunchAgents/' + label + '.plist' if sys.platform == 'darwin' else '.config/systemd/user/' + label)
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise ClientError('原启动入口不属于本用户，未接管。')
        if sys.platform == 'darwin':
            values = plistlib.loads(path.read_bytes()).get('ProgramArguments', [])
        else:
            lines = [line[len('ExecStart='):] for line in path.read_text().splitlines() if line.startswith('ExecStart=')]
            values = shlex.split(lines[0]) if len(lines) == 1 else []
        args = values[1:]
        if args[:1] == ['-u']:
            args = args[1:]
        if len(args) != 3 or args != [str(spec.launcher), '--config', str(spec.config)] or (not values) or (not Path(values[0]).name.startswith('python')):
            raise ClientError('原桥接入口不匹配，未接管其他程序。')
        return (label, path)

    def legacy_control(self, spec, enabled):
        label, path = self.legacy(spec)
        if sys.platform == 'darwin':
            target = f'gui/{os.getuid()}/' + label
            self.command(['launchctl', 'enable' if enabled else 'disable', target])
            loaded = self.command(['launchctl', 'print', target], False).returncode == 0
            if enabled and (not loaded):
                self.command(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)])
            elif not enabled and loaded:
                self.command(['launchctl', 'bootout', target])
            if enabled:
                self.command(['launchctl', 'kickstart', target])
        else:
            self.command(['systemctl', '--user', 'enable' if enabled else 'disable', label])
            self.command(['systemctl', '--user', 'start' if enabled else 'stop', label])

    def migrate(self, spec, store):
        old = self.legacy(spec)
        if not old:
            self.create(spec)
            return
        if not spec.managed_executable.is_file():
            raise ClientError('请先安装独立客户端。')
        label, path = old
        before = self.inspect(spec)
        app_was_running = native_app_running(spec.key, process_rows())
        cfg = store.load(spec.key)
        journal = Path(cfg['state_dir']).expanduser() / ('commands.sqlite' if spec.key == 'codex' else 'harness.sqlite')
        if journal.exists():
            with sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                if db.execute('SELECT COUNT(*) FROM commands WHERE result IS NULL').fetchone()[0]:
                    raise ClientError('桥接仍有请求在处理，请稍后接管；原 App 任务无需停止。')
        backup = client_root() / 'backups' / (spec.key + '-legacy-' + str(time.time_ns()) + path.suffix)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
        backup.chmod(384)
        store.import_existing(spec.key)
        self.legacy_control(spec, False)
        try:
            self.create(spec)
            if before['state'] != 4:
                self.enable(spec, before['enabled'])
                return
            self.start(spec)
            started = time.time()
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                try:
                    state = json.loads(spec.runtime_file.read_text())
                    procs, _ = matching_processes(spec, process_rows())
                    owned = any((p['pid'] == state.get('pid') and p['created'] == state.get('created') for p in procs))
                    synced = state.get('last_sync_at') and (not state.get('last_error'))
                    waiting_for_app = not app_was_running and time.time() - started >= 3 and (state.get('phase') in {'starting', 'running'}) and (time.time() - state.get('updated_at', 0) < 5)
                    if owned and (synced or waiting_for_app):
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.5)
            raise ClientError('新客户端未确认同步，恢复原桥接。')
        except Exception:
            if self.path(spec).exists():
                self.pause(spec)
                self.remove(spec)
            if before['state'] == 4:
                self.legacy_control(spec, True)
            elif sys.platform != 'darwin':
                self.command(['systemctl', '--user', 'enable' if before['enabled'] else 'disable', label])
            raise

    def command(self, args, check=True):
        r = self.runner(args, capture_output=True, text=True, timeout=15)
        if check and r.returncode:
            raise ClientError('后台服务操作失败：' + r.stderr.strip()[-600:])
        return r

    def exists(self, spec):
        return self.path(spec).exists() or bool(self.legacy(spec))

    def validate(self, spec):
        path = self.path(spec)
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise ClientError('启动入口不属于本用户，未控制。')
        if sys.platform == 'darwin':
            if plistlib.loads(path.read_bytes()).get('ProgramArguments') != [str(spec.managed_executable), '--bridge', spec.key]:
                raise ClientError('LaunchAgent 入口不匹配，未控制其他程序。')
        elif path.read_text() != unit_text(spec):
            raise ClientError('systemd 入口不匹配，未控制其他程序。')

    def inspect(self, spec):
        if not self.path(spec).exists():
            old = self.legacy(spec)
            if old:
                label, _ = old
                if sys.platform == 'darwin':
                    loaded = self.command(['launchctl', 'print', f'gui/{os.getuid()}/' + label], False)
                    return {'state': 4 if 'state = running' in loaded.stdout else 3, 'enabled': loaded.returncode == 0, 'last_result': 0, 'mode': 'legacy'}
                active = self.command(['systemctl', '--user', 'is-active', label], False).stdout.strip() == 'active'
                enabled = self.command(['systemctl', '--user', 'is-enabled', label], False).stdout.strip() == 'enabled'
                return {'state': 4 if active else 3, 'enabled': enabled, 'last_result': 0, 'mode': 'legacy'}
        self.validate(spec)
        if sys.platform == 'darwin':
            marker = spec.runtime_file.with_suffix('.enabled.json')
            enabled = json.loads(marker.read_text()).get('enabled', True) if marker.exists() else True
            r = self.command(['launchctl', 'print', f'gui/{os.getuid()}/' + service_name(spec)], False)
            return {'state': 4 if 'state = running' in r.stdout else 3, 'enabled': enabled, 'last_result': 0, 'mode': 'packaged'}
        active = self.command(['systemctl', '--user', 'is-active', service_name(spec)], False).stdout.strip() == 'active'
        enabled = self.command(['systemctl', '--user', 'is-enabled', service_name(spec)], False).stdout.strip() == 'enabled'
        return {'state': 4 if active else 3, 'enabled': enabled, 'last_result': 0, 'mode': 'packaged'}

    def create(self, spec):
        if not spec.managed_executable.is_file():
            raise ClientError('请先安装完整客户端。')
        if self.path(spec).exists():
            self.validate(spec)
            return
        path = self.path(spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        spec.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        data = plistlib.dumps(plist_data(spec)) if sys.platform == 'darwin' else unit_text(spec).encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        if sys.platform != 'darwin':
            self.command(['systemctl', '--user', 'daemon-reload'])

    def enable(self, spec, enabled):
        if not self.path(spec).exists() and self.legacy(spec):
            self.legacy_control(spec, enabled)
            return
        self.validate(spec)
        if sys.platform == 'darwin':
            self.command(['launchctl', 'enable' if enabled else 'disable', f'gui/{os.getuid()}/' + service_name(spec)])
            atomic_json(spec.runtime_file.with_suffix('.enabled.json'), {'enabled': bool(enabled)})
        else:
            self.command(['systemctl', '--user', 'enable' if enabled else 'disable', service_name(spec)])

    def start(self, spec):
        if not self.path(spec).exists() and self.legacy(spec):
            self.legacy_control(spec, True)
            return
        self.enable(spec, True)
        if sys.platform == 'darwin':
            target = f'gui/{os.getuid()}/' + service_name(spec)
            if self.command(['launchctl', 'print', target], False).returncode:
                self.command(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(self.path(spec))])
            self.command(['launchctl', 'kickstart', target])
        else:
            self.command(['systemctl', '--user', 'start', service_name(spec)])

    def pause(self, spec):
        if not self.path(spec).exists() and self.legacy(spec):
            self.legacy_control(spec, False)
            return
        self.enable(spec, False)
        try:
            request_bridge_stop(spec)
        except (OSError, ValueError):
            pass
        if sys.platform == 'darwin':
            target = f'gui/{os.getuid()}/' + service_name(spec)
            if not self.command(['launchctl', 'print', target], False).returncode:
                self.command(['launchctl', 'bootout', target])
        else:
            self.command(['systemctl', '--user', 'stop', service_name(spec)])

    def remove(self, spec):
        if not self.path(spec).exists():
            old = self.legacy(spec)
            if not old:
                return
            if self.inspect(spec)['enabled'] or matching_processes(spec, process_rows())[0]:
                raise ClientError('原后台仍运行或启用，未移除。')
            _, path = old
            backup = client_root() / 'backups' / (spec.key + '-unpaired-service-' + str(time.time_ns()) + path.suffix)
            backup.parent.mkdir(parents=True, exist_ok=True)
            path.rename(backup)
            if sys.platform != 'darwin':
                self.command(['systemctl', '--user', 'daemon-reload'])
            return
        self.validate(spec)
        if self.inspect(spec)['enabled'] or matching_processes(spec, process_rows())[0]:
            raise ClientError('后台仍运行或启用，未移除。')
        self.path(spec).unlink()
        if sys.platform != 'darwin':
            self.command(['systemctl', '--user', 'daemon-reload'])

class PosixStartup:

    def __init__(self, product='client'):
        self.product = product
        self.path = Path.home() / ('Library/LaunchAgents/com.codexrelay.' + product + '.ui.plist') if sys.platform == 'darwin' else Path.home() / ('.config/autostart/relay-' + product + '.desktop')

    def enabled(self):
        return self.path.exists()

    def configure(self, enabled, executable, previous_executable=None):
        executable = Path(executable).resolve()
        if not executable.is_file():
            raise ClientError('请使用打包后的客户端。')
        allowed = {executable}
        if previous_executable is not None:
            previous = Path(previous_executable).resolve()
            if not getattr(sys, 'frozen', False) or previous != Path(sys.executable).resolve() or previous.name != executable.name:
                raise ClientError('原客户端启动入口身份不匹配，未修改。')
            allowed.add(previous)
        if self.path.exists():
            if self.path.is_symlink():
                raise ClientError('同名启动入口是链接，未覆盖。')
            data = self.path.read_bytes()
            if sys.platform == 'darwin':
                args = plistlib.loads(data).get('ProgramArguments')
                owned = isinstance(args, list) and len(args) == 2 and (args[1] == '--tray') and isinstance(args[0], str) and (Path(args[0]).resolve() in allowed)
            else:
                owners = [line.split('=', 1)[1] for line in data.decode().splitlines() if line.startswith('X-Relay-Executable=')]
                owned = len(owners) == 1 and Path(owners[0]).resolve() in allowed
            if not owned:
                raise ClientError('同名启动入口属于其他程序，未覆盖。')
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'darwin':
            data = plistlib.dumps({'Label': 'com.codexrelay.' + self.product + '.ui', 'ProgramArguments': [str(executable), '--tray'], 'RunAtLoad': True, 'ProcessType': 'Interactive'})
        else:
            q = str(executable).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
            data = ('[Desktop Entry]\nType=Application\nName=Relay ' + self.product.title() + '\nExec="' + q + '" --tray\nTerminal=false\nX-Relay-Executable=' + str(executable) + '\n').encode()
        from desktop_client.connections import atomic_bytes
        atomic_bytes(self.path, data)
