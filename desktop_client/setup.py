"""First enrollment: no legacy config, Python installation, or existing task required."""
import http.cookiejar
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from desktop_client.backend import ClientError, valid_telemetry, matching_processes
from desktop_client.connections import ConnectionStore, atomic_json, validate_relay_url

class RevocationUnconfirmed(ClientError):
    """A revoke request may have committed; never resume its old credential."""

def safe_setup_url(value):
    value = validate_relay_url(value)
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    if parsed.path not in ('', '/'):
        raise ClientError('请填写服务器首页地址，不包含接口路径。')
    if parsed.scheme == 'http':
        try:
            address = ipaddress.ip_address(parsed.hostname)
            private = address.is_private or address in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            private = parsed.hostname == 'localhost'
        if not private:
            raise ClientError('公网配对必须使用 HTTPS；HTTP 仅用于可信局域网或 Tailscale IP。')
    return value

class EnrollmentClient:

    def __init__(self, base):
        self.base = safe_setup_url(base)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, path, payload=None, origin=None, limit=128 * 1024, token=None):
        headers = {'Accept': 'application/json'}
        if token is not None:
            if not isinstance(token, str) or not token or len(token) > 4096 or any((c.isspace() for c in token)):
                raise ClientError('本机配对凭证无效。')
            headers['Authorization'] = 'Bearer ' + token
        if origin is not None:
            headers['Origin'] = origin
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self.opener.open(urllib.request.Request(self.base + path, data=data, headers=headers), timeout=12) as reply:
                raw = reply.read(limit + 1)
                content_type = reply.headers.get('Content-Type', '')
            if len(raw) > limit or 'application/json' not in content_type:
                raise ClientError('服务器未返回客户端配对接口；请检查地址或更新服务器。')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except urllib.error.HTTPError as exc:
            messages = {401: '网页登录账号或密码不正确，或该地址前面还有额外登录验证。', 403: '服务器拒绝配对请求；请确认服务器地址正确。', 404: '服务器尚未支持首次配对，请先更新面板服务器。', 429: '登录尝试过于频繁，请稍后再试。'}
            if token is not None:
                messages.update({401: '服务器未确认这台设备的连接凭证，不能宣称远端授权已撤销。', 404: '服务器尚未支持解除配对，请先更新服务器。', 409: '旧凭证被多个设备共用，不能单独撤销。可先仅断开本机，再由管理员拆分授权。'})
            error = ClientError(messages.get(exc.code, f'配对接口返回 HTTP {exc.code}，未保存密码。'))
            error.http_status = exc.code
            raise error from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ClientError('无法连接服务器。请检查地址、网络以及 Tailscale 连接；HTTPS 证书必须受系统信任。') from None
        except (ValueError, TypeError):
            raise ClientError('服务器配对响应格式错误，未创建本机配置。') from None

    def info(self):
        info = self.request('/client/setup')
        if info.get('service') != 'codex-desktop-relay' or info.get('protocol') != 1 or (not isinstance(info.get('origin'), str)):
            raise ClientError('这不是支持首次接入的面板服务器。')
        return info

    def enroll(self, installation, label, apps, password, username='relay'):
        try:
            info = self.info()
            if not password:
                raise ClientError('请输入面板访问密码，不是 SSH 密码或 API Key。')
            self.request('/client/login', {'username': username, 'password': password}, info['origin'])
            return self.request('/api/client/enroll', {'installationId': installation, 'label': label, 'applications': apps}, info['origin'])
        finally:
            self.cookies.clear()

    def forget(self, config, application):
        info = self.info()
        try:
            result = self.request('/client/forget', {'hostId': config['host_id']}, info['origin'], token=config['agent_token'])
        except ClientError as exc:
            if getattr(exc, 'http_status', None) in {401, 403, 404, 409, 429}:
                raise
            raise RevocationUnconfirmed('远端授权撤销结果未确认，请继续解除；不会恢复旧凭证。') from None
        if result.get('ok') is not True or result.get('revoked') is not True or result.get('hostId') != config['host_id'] or (result.get('applicationId') != application):
            raise RevocationUnconfirmed('服务器未确认撤销本应用授权，请继续解除；不会恢复旧凭证。')
        return result

class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, *args, **kwargs):
        return None

def ensure_client_installed(executable=None, target=None):
    source = Path(executable or sys.executable).resolve()
    from desktop_client.platform_support import installed_executable
    target = Path(target or installed_executable(ConnectionStore().root))
    if source == target.resolve():
        if getattr(sys, 'frozen', False) and (sys.platform != 'win32' or target.parent.parent.name == 'CodexRelayClient'):
            desktop_shortcut(target)
        return target
    if not getattr(sys, 'frozen', False) and executable is None:
        raise ClientError('请使用独立客户端 EXE 完成首次接入。')
    if target.exists():
        raise ClientError('本机已有另一份客户端，请从已安装的 Relay Client 打开，避免覆盖运行中的程序。')
    destination = target.parents[3] if sys.platform == 'darwin' else target.parent
    stage = destination.with_name('app-install-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    try:
        if sys.platform == 'darwin':
            bundle = source.parents[2]
            if bundle.suffix != '.app' or not (bundle / 'Contents/Info.plist').exists():
                raise ClientError('请使用完整 macOS App 安装包。')
            shutil.copytree(bundle, stage / target.parents[2].name, symlinks=True)
            stage.rename(target.parents[3])
            desktop_shortcut(target)
            return target
        shutil.copy2(source, stage / target.name)
        internal = source.parent / '_internal'
        if not internal.is_dir():
            raise ClientError('安装包缺少 _internal 运行时，请解压完整安装包后启动。')
        shutil.copytree(internal, stage / '_internal')
        readme = source.parent / 'README.md'
        if readme.exists():
            shutil.copy2(readme, stage / 'README.md')
        stage.rename(target.parent)
        if getattr(sys, 'frozen', False) and target.parent.parent.name == 'CodexRelayClient':
            desktop_shortcut(target)
    except Exception:
        raise
    return target

def persistent_client_executable(install=True):
    """First-run UI can still be running from Downloads after installation."""
    from desktop_client.platform_support import installed_executable
    target = installed_executable(ConnectionStore().root)
    if target.is_file():
        return target.resolve()
    return ensure_client_installed() if install else Path(sys.executable).resolve()

def desktop_shortcut(executable):
    if sys.platform != 'win32':
        executable = Path(executable).resolve()
        if sys.platform == 'darwin':
            applications = Path.home() / 'Applications'
            applications.mkdir(exist_ok=True)
            link = applications / 'Relay Client.app'
            bundle = executable.parents[2]
            if link.exists() or link.is_symlink():
                if link.resolve() != bundle:
                    raise ClientError('Applications 中存在同名其他程序，未覆盖。')
            else:
                link.symlink_to(bundle, target_is_directory=True)
        else:
            launcher = Path.home() / '.local/share/applications/relay-client.desktop'
            launcher.parent.mkdir(parents=True, exist_ok=True)
            if launcher.exists() and 'X-Relay-Executable=' + str(executable) + '\n' not in launcher.read_text():
                raise ClientError('应用菜单中存在同名其他程序，未覆盖。')
            q = str(executable).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`').replace('%', '%%')
            launcher.write_text('[Desktop Entry]\nType=Application\nName=Relay Client\nExec="' + q + '"\nTerminal=false\nCategories=Network;Utility;\nX-Relay-Executable=' + str(executable) + '\n', encoding='utf-8')
        return
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    shell = link = None
    try:
        shell = win32com.client.Dispatch('WScript.Shell')
        path = Path(shell.SpecialFolders('Desktop')) / 'Relay Client.lnk'
        link = shell.CreateShortcut(str(path))
        if path.exists() and Path(link.TargetPath).resolve() != Path(executable).resolve():
            raise ClientError('桌面同名快捷方式属于其他程序，未覆盖。')
        link.TargetPath = str(executable)
        link.WorkingDirectory = str(Path(executable).parent)
        link.IconLocation = str(executable)
        link.Description = 'Relay Client · 本机桥接客户端'
        link.Save()
    finally:
        link = shell = None
        pythoncom.CoUninitialize()

class SetupService:

    def __init__(self, manager, store=None, remote_factory=EnrollmentClient, installer=ensure_client_installed):
        self.manager = manager
        self.store = store or ConnectionStore()
        self.remote_factory, self.installer = (remote_factory, installer)

    def missing(self):
        return [k for k in self.manager.specs if not self.store.available(k) and (not self.store.disconnect_pending(k))]

    def disconnect(self, key, local_only=False):
        spec = self.manager.specs[key]
        continuing = self.store.disconnect_pending(key)
        if not continuing and self.store.unpaired(key):
            marker = self.store.pairing_marker(key)
            if marker.get('remoteRevoked'):
                return spec.title + ' 配对已经解除。'
            if local_only:
                return spec.title + ' 本机已经断开；远端授权尚未撤销。'
            data = self.store.disconnected_config(key)
            self.remote_factory(data['relay_url']).forget(data, key)
            self.store.finish_revoke(key)
            return spec.title + ' 远端授权已补撤销；原 App 的聊天和项目保持不变。'
        data = self.store.disconnect_config(key) if continuing else self.store.load(key)
        self.store.validate(data)
        exists = self.manager.tasks.exists(spec)
        original = self.manager.tasks.inspect(spec) if exists else {}
        procs, _ = matching_processes(spec, self.manager.processes())
        if procs and (not exists):
            raise ClientError('有桥接进程但没有匹配的启动任务，未擅自终止；请先修复后台。')
        running = bool(procs)
        if not continuing:
            self.store.begin_disconnect(key, data)
        revoked = self.store.disconnect_record(key)['remoteRevoked']
        try:
            if exists:
                self.manager.control(key, 'pause')
            if not local_only and (not revoked):
                self.remote_factory(data['relay_url']).forget(data, key)
                revoked = True
        except RevocationUnconfirmed:
            raise ClientError('本机桥接已暂停；远端撤销结果待确认，请点击“继续解除”。不会恢复旧凭证，原 App 未被停止。') from None
        except Exception:
            if continuing:
                raise ClientError('解除配对仍待完成，请检查连接后继续解除；不会恢复旧授权。') from None
            try:
                self.store.abort_disconnect(key)
            except (OSError, ValueError):
                raise ClientError('解除操作未完成，恢复记录仍待处理；请继续解除，未恢复旧授权。') from None
            if exists:
                if running:
                    self.manager.control(key, 'start')
                else:
                    self.manager.tasks.enable(spec, original.get('enabled', False))
            raise
        if revoked and (not self.store.disconnect_record(key)['remoteRevoked']):
            try:
                self.store.record_disconnect_revoked(key)
            except (OSError, ValueError):
                raise ClientError('远端授权已撤销，本地操作记录待完成；请继续解除，不会恢复旧凭证。') from None
        try:
            if exists:
                self.manager.tasks.remove(spec)
        except Exception:
            raise ClientError('后台已暂停，远端授权' + ('已撤销' if revoked else '未撤销') + '；启动任务清理失败，请重试解除配对。原 App 未被停止。') from None
        try:
            self.store.complete_disconnect(key)
        except (OSError, ValueError):
            raise ClientError('远端授权' + ('已撤销' if revoked else '尚未确认撤销') + '；本地清理待完成，请点击“继续解除”。不会恢复旧凭证。') from None
        return spec.title + (' 配对已解除：本机后台与远端授权均已移除。' if revoked else ' 本机已断开：后台已移除，但远端授权尚未撤销，可在配对管理中补撤销。') + '\n原 App 的聊天、项目和文件保持不变。'

    def installation_id(self):
        path = self.store.root / 'installation.json'
        if path.exists():
            return str(uuid.UUID(json.loads(path.read_text(encoding='utf-8'))['id']))
        identity = str(uuid.uuid4())
        atomic_json(path, {'id': identity})
        return identity

    def connect(self, base, label, apps, password, username='relay'):
        label = label.strip()
        if not 1 <= len(label) <= 80 or any((ord(c) < 32 for c in label)):
            raise ClientError('请填写 1–80 个字符的电脑名称。')
        if not apps or len(set(apps)) != len(apps) or any((k not in self.manager.specs for k in apps)):
            raise ClientError('请至少选择一个应用。')
        for key in apps:
            if self.store.disconnect_pending(key):
                raise ClientError('本应用解除配对尚未完成，请先继续解除；不会覆盖旧操作。')
            if key not in self.missing():
                raise ClientError(self.manager.specs[key].title + ' 已有配置，请使用连接设置；首次接入不会覆盖它。')
            if self.store.unpaired(key):
                marker = self.store.pairing_marker(key)
                if not marker.get('remoteRevoked'):
                    raise ClientError('本应用还有未撤销的远端授权，请先在配对管理中“补撤销授权”，再重新配对。')
            if self.manager.tasks.exists(self.manager.specs[key]):
                raise ClientError('发现同名后台任务，未覆盖。请先检查或使用“接管 / 修复”。')
        base = safe_setup_url(base)
        installed = self.installer()
        if any((Path(self.manager.specs[k].managed_executable).resolve() != Path(installed).resolve() for k in apps)):
            raise ClientError('客户端安装路径与后台任务路径不一致。')
        installation = self.installation_id()
        reply = self.remote_factory(base).enroll(installation, label, apps, password, username)
        connections = reply.get('connections')
        physical = 'pc-' + installation.replace('-', '')
        if reply.get('protocol') != 1 or not isinstance(connections, dict) or set(connections) != set(apps):
            raise ClientError('服务器返回的应用配对不完整，未建立本机后台任务。')
        candidates = {}
        for key in apps:
            config = dict(connections[key])
            expected = physical if key == 'codex' else physical + '.' + key
            if config.get('host_id') != expected or config.get('physical_host_id') != physical:
                raise ClientError('服务器返回的设备身份不匹配，未创建配置。')
            config.update(relay_url=base, state_dir=str(self.store.root / 'bridge-data' / key), local_access=True)
            if key == 'codex':
                config.update(codex_home=str(self.store.home / '.codex'))
            elif key == 'deepseek':
                config.update(harness_home=str(self.store.home / '.dsh'), harness_url='http://127.0.0.1:' + ('3080' if sys.platform.startswith('linux') else '19387'))
                from desktop_client.platform_support import harness_payload
                payload = harness_payload(self.store.home)
                if payload:
                    config['native_desktop_payload'] = payload
            elif key == 'claude':
                config.update(claude_debug_url='http://127.0.0.1:19488')
            self.store.validate(config)
            candidates[key] = config
        for key, config in candidates.items():
            atomic_json(self.store.path(key), config)
            self.store.marker_path(key).unlink(missing_ok=True)
        outcomes = []
        for key in apps:
            spec = self.manager.specs[key]
            try:
                self.manager.tasks.create(spec)
                self.manager.control(key, 'start')
                outcomes.append(spec.title + '：已配对，后台已启动')
            except Exception:
                raise ClientError(spec.title + ' 已配对并保存；后台任务建立失败。请点击“接管 / 修复内置桥接”重试，不需要再次输入密码。') from None
        deadline = self.manager.clock() + 65
        pending = set(apps)
        desktop = {}
        while pending and self.manager.clock() < deadline:
            for item in self.manager.status():
                if item['key'] in pending and item.get('relay_online'):
                    pending.remove(item['key'])
                    desktop[item['key']] = item.get('telemetry', {}).get('desktop_connected', False)
            if pending:
                self.manager.sleep(0.5)
        detail = '\n'.join(outcomes)
        if pending:
            detail += '\n配对完成，但部分后台尚未确认同步。请在概览查看网络和诊断日志；不用重复配对。'
        elif all(desktop.values()):
            detail += '\n服务器与桌面 App 均已连接，可以打开网页面板。'
        else:
            detail += '\n服务器已连接；请打开对应桌面 App，客户端会自动继续连接。'
        return detail
