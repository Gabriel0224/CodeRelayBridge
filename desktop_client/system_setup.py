"""Owned endpoint firewall/dependency setup; no blanket security disable or NAT."""
import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from desktop_client.backend import ClientError
LIB_PACKAGES = {'libxcb-cursor.so.0': 'libxcb-cursor0', 'libxkbcommon-x11.so.0': 'libxkbcommon-x11-0', 'libGL.so.1': 'libgl1', 'libEGL.so.1': 'libegl1', 'libwayland-client.so.0': 'libwayland-client0', 'libwayland-cursor.so.0': 'libwayland-cursor0', 'libwayland-egl.so.1': 'libwayland-egl1', 'libxcb.so.1': 'libxcb1', 'libX11-xcb.so.1': 'libx11-xcb1', 'libX11.so.6': 'libx11-6', 'libXrender.so.1': 'libxrender1', 'libxkbcommon.so.0': 'libxkbcommon0', 'libfontconfig.so.1': 'libfontconfig1', 'libfreetype.so.6': 'libfreetype6'}

def run(argv, timeout=15, input_text=None):
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True, input=input_text, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, env=dict(os.environ, LC_ALL='C'))

def powershell(code, timeout=15):
    encoded = base64.b64encode(code.encode('utf-16-le')).decode()
    return run([powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], timeout)

def powershell_executable():
    return str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')

def local_scopes(bind):
    address = ipaddress.ip_address(bind)
    tail = ipaddress.ip_network('100.64.0.0/10')
    if address.version == 4 and address in tail:
        return ['100.64.0.0/10']
    if address.is_loopback:
        return []
    if sys.platform == 'win32':
        return ['LocalSubnet']
    import psutil
    scopes = set()
    for rows in psutil.net_if_addrs().values():
        for row in rows:
            if row.family != socket.AF_INET or not row.netmask:
                continue
            addr = ipaddress.ip_address(row.address)
            if not addr.is_private or addr.is_loopback or addr in tail:
                continue
            if not address.is_unspecified and row.address != bind:
                continue
            scopes.add(str(ipaddress.ip_network(row.address + '/' + row.netmask, strict=False)))
    if not scopes:
        raise ClientError('未识别可信局域网网段，请先选择本机有效监听 IP；不自动放行任意来源')
    return sorted(scopes)

def endpoint_probe(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ClientError('请使用有效的 HTTP/HTTPS 面板地址，不包含账号或密码')
    port = p.port or (443 if p.scheme == 'https' else 80)
    value = {'host': p.hostname, 'port': port, 'addresses': [], 'tcpReachable': False, 'tlsVerified': None}
    try:
        value['addresses'] = sorted({entry[4][0] for entry in socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)})
        with socket.create_connection((p.hostname, port), timeout=4) as stream:
            value['tcpReachable'] = True
            if p.scheme == 'https':
                with ssl.create_default_context().wrap_socket(stream, server_hostname=p.hostname):
                    value['tlsVerified'] = True
    except (OSError, ssl.SSLError) as exc:
        value['error'] = type(exc).__name__ + ': ' + str(exc)[:180]
        if value['tcpReachable'] and p.scheme == 'https':
            value['tlsVerified'] = False
    return value

def dependency_report(role=None):
    role = role or packaged_role() or 'client'
    required = ('PySide6', 'sqlite3', 'psutil') + (('yaml', 'websocket') if role == 'client' else ())
    modules = {name: importlib.util.find_spec(name) is not None for name in required}
    missing = []
    if sys.platform.startswith('linux') and getattr(sys, 'frozen', False):
        root = Path(sys._MEIPASS)
        env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(map(str, (root, root / 'PySide6/Qt/lib'))))
        for path in [root / 'PySide6/Qt/plugins/platforms/libqxcb.so', root / 'PySide6/Qt/lib/libQt6Gui.so.6']:
            if not path.exists():
                continue
            result = subprocess.run(['ldd', str(path)], capture_output=True, text=True, env=env, timeout=10)
            for line in result.stdout.splitlines():
                if '=> not found' in line:
                    missing.append(line.split()[0])
    packages = sorted({LIB_PACKAGES[name] for name in missing if name in LIB_PACKAGES})
    return {'python': platform.python_version(), 'architecture': platform.machine(), 'frozen': bool(getattr(sys, 'frozen', False)), 'bundledModules': modules, 'missingSystemLibraries': sorted(set(missing)), 'repairPackages': packages, 'unmappedLibraries': sorted(set(missing) - LIB_PACKAGES.keys()), 'passed': all(modules.values()) and (not missing)}

def firewall_state():
    if sys.platform == 'win32':
        r = powershell("$p=New-Object -ComObject HNetCfg.FwPolicy2; @(@{Name='Domain';Type=1},@{Name='Private';Type=2},@{Name='Public';Type=4}) | ForEach-Object {[pscustomobject]@{Name=$_.Name;Enabled=$p.FirewallEnabled($_.Type);DefaultOutboundAction=$p.DefaultOutboundAction($_.Type)}} | ConvertTo-Json -Compress")
        return {'provider': 'Windows Defender Firewall', 'profiles': json.loads(r.stdout) if r.returncode == 0 else [], 'readable': r.returncode == 0}
    if sys.platform == 'darwin':
        r = run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--getglobalstate'])
        b = run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--getblockall'])
        return {'provider': 'macOS Application Firewall', 'enabled': 'State = 1' in r.stdout, 'blockAll': 'ENABLED' in b.stdout.upper() or 'State = 1' in b.stdout, 'readable': r.returncode == 0}
    path = Path('/etc/ufw/ufw.conf')
    try:
        enabled = 'ENABLED=yes' in path.read_text(encoding='utf-8')
    except OSError:
        enabled = None
    return {'provider': 'ufw' if shutil.which('ufw') else 'unknown', 'enabled': enabled, 'readable': enabled is not None, 'ufwInstalled': bool(shutil.which('ufw'))}

def plan(role, bind='127.0.0.1', port=8799, url=None):
    if role not in ('client', 'server'):
        raise ClientError('不支持的程序类型')
    executable = str(Path(sys.executable).resolve())
    value = {'schema': 1, 'role': role, 'executable': executable, 'bind': bind, 'port': int(port), 'direction': 'in', 'scopes': [], 'packages': [], 'needsFirewall': False, 'notes': []}
    deps = dependency_report(role)
    fw = firewall_state()
    value['dependencies'], value['firewall'] = (deps, fw)
    value['packages'] = deps['repairPackages']
    if not all(deps['bundledModules'].values()):
        value['notes'].append('包内运行模块缺失，需要完整重新解压安装包；不通过系统 pip 补装')
    if role == 'server':
        address = ipaddress.ip_address(bind)
        if not 1024 <= int(port) <= 65535:
            raise ClientError('服务端监听端口必须在 1024–65535 之间')
        value['scopes'] = local_scopes(bind)
        value['needsFirewall'] = bool(value['scopes'])
        if address.is_loopback:
            value['notes'].append('仅本机监听，不需新增入站规则')
        if sys.platform == 'darwin':
            value['notes'].append('macOS 防火墙按应用授权；监听地址和端口仍由服务配置限制')
    else:
        value['direction'] = 'out'
        if url:
            endpoint = endpoint_probe(url)
            value['endpoint'] = endpoint
            value['port'] = endpoint['port']
            value['scopes'] = endpoint['addresses']
            value['needsFirewall'] = sys.platform == 'win32' and (not endpoint['tcpReachable'])
        value['notes'].append('客户端主动连接服务器，不开放 SSH、模型桥接、终端或调试入站端口')
    if fw.get('blockAll'):
        value['notes'].append('系统启用了阻止所有入站连接；添加应用规则不能覆盖此设置，不自动关闭它')
    if sys.platform.startswith('linux') and value['needsFirewall'] and (not fw.get('ufwInstalled')):
        value['notes'].append('未检测到 UFW；不擅自安装/启用整个防火墙或改动未知 nftables 规则')
        value['needsFirewall'] = False
    if sys.platform.startswith('linux') and fw.get('enabled') is False:
        value['notes'].append('UFW 未启用，不需新增允许规则；不自动启用整个防火墙')
        value['needsFirewall'] = False
    if sys.platform == 'darwin' and fw.get('enabled') is False:
        value['needsFirewall'] = False
    if role == 'client' and (not value['scopes']):
        value['needsFirewall'] = False
    identity = {k: value[k] for k in ('role', 'executable', 'bind', 'port', 'direction', 'scopes')}
    value['ruleName'] = 'CodexRelay-' + role + '-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    if value['needsFirewall'] and firewall_rule_ready(value):
        value['needsFirewall'] = False
        value['notes'].append('当前程序已有匹配的允许规则，无需再次授权')
    return value

def firewall_rule_ready(value):
    """Read-only, exact owned-rule verification; avoids repeated UAC prompts."""
    if sys.platform == 'darwin':
        listing = run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--listapps'])
        path = Path(value['executable'])
        expected = {str(path)} | {str(p) for p in path.parents if p.suffix == '.app'}
        entries = {re.sub('^\\s*\\d+\\s*:\\s*', '', line).strip() for line in listing.stdout.splitlines()}
        if listing.returncode or not entries.intersection(expected):
            return False
        result = run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--getappblocked', value['executable']])
        message = result.stdout.lower()
        return result.returncode == 0 and 'non-existent' not in message and ('not blocked' in message or 'is permitted' in message)
    if sys.platform != 'win32':
        return False
    encoded = base64.b64encode(json.dumps(value).encode()).decode()
    code = "$ErrorActionPreference='Stop'; $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json; "
    code += "$fw=New-Object -ComObject HNetCfg.FwPolicy2; try{$r=$fw.Rules.Item($p.ruleName)}catch{'null';exit}; "
    code += '@{Grouping=$r.Grouping;Enabled=$r.Enabled;Action=$r.Action;Direction=$r.Direction;Profiles=$r.Profiles;Program=$r.ApplicationName;Protocol=$r.Protocol;LocalPort=$r.LocalPorts;RemotePort=$r.RemotePorts;RemoteAddresses=$r.RemoteAddresses} | ConvertTo-Json -Compress'
    try:
        result = powershell(code)
        return result.returncode == 0 and windows_rule_matches(value, json.loads(result.stdout))
    except (ValueError, OSError, subprocess.SubprocessError):
        return False

def canonical_scope(scope):
    if scope.casefold() == 'localsubnet':
        return 'localsubnet'
    return str(ipaddress.ip_network(scope, strict=False))

def windows_rule_matches(value, rule):
    if not isinstance(rule, dict):
        return False
    direction = 1 if value['direction'] == 'in' else 2
    profiles = 2147483647 if direction == 2 or value['scopes'] == ['100.64.0.0/10'] else 3
    try:
        scopes = {canonical_scope(scope.strip()) for scope in rule.get('RemoteAddresses', '').split(',')}
        expected = {canonical_scope(scope) for scope in value['scopes']}
    except ValueError:
        return False
    return rule.get('Grouping') == 'Codex Relay owned rules' and rule.get('Enabled') is True and (rule.get('Action') == 1) and (rule.get('Direction') == direction) and (rule.get('Profiles') == profiles) and (str(rule.get('Program', '')).replace('\\', '/').casefold() == value['executable'].replace('\\', '/').casefold()) and (rule.get('Protocol') == 6) and (str(rule.get('LocalPort' if direction == 1 else 'RemotePort')) == str(value['port'])) and (scopes == expected)

def validate_plan(value):
    if value.get('schema') != 1 or value.get('role') not in ('client', 'server'):
        raise ClientError('授权计划格式错误')
    if Path(value.get('executable', '')).resolve() != Path(sys.executable).resolve():
        raise ClientError('授权计划不是当前程序，拒绝配置其他应用')
    owned = packaged_role()
    if owned and value['role'] != owned:
        raise ClientError('授权计划的客户端/服务端角色不符')
    if not 1 <= int(value.get('port', 0)) <= 65535:
        raise ClientError('端口无效')
    if value.get('direction') != ('in' if value['role'] == 'server' else 'out'):
        raise ClientError('网络方向无效')
    ipaddress.ip_address(value.get('bind', ''))
    if len(value.get('scopes', [])) > 32:
        raise ClientError('来源范围过多')
    for scope in value.get('scopes', []):
        if scope == 'LocalSubnet' and sys.platform == 'win32':
            continue
        network = ipaddress.ip_network(scope, strict=False)
        trusted = network.is_private or (network.version == 4 and network.subnet_of(ipaddress.ip_network('100.64.0.0/10')))
        if value['role'] == 'server' and (not trusted):
            raise ClientError('服务端不自动开放公网来源')
        if network.prefixlen == 0:
            raise ClientError('不允许任意来源或目的地址')
    if set(value.get('packages', [])) - set(LIB_PACKAGES.values()):
        raise ClientError('依赖软件包不在允许清单')
    expected = {k: value[k] for k in ('role', 'executable', 'bind', 'port', 'direction', 'scopes')}
    if value.get('ruleName') != 'CodexRelay-' + value['role'] + '-' + hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()[:16]:
        raise ClientError('网络规则标识与计划不符')

def packaged_role():
    if not getattr(sys, 'frozen', False):
        return None
    root = getattr(sys, '_MEIPASS', None)
    if root:
        path = Path(root) / 'relay-edition.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8')).get('role')
    return None

def apply_plan(value, runner=run):
    validate_plan(value)
    actions = []
    if value.get('packages'):
        if not sys.platform.startswith('linux'):
            raise ClientError('此平台不支持系统 apt 依赖修复')
        for argv in (['apt-get', 'update'], ['apt-get', 'install', '-y', *value['packages']]):
            r = runner(argv, timeout=300)
            if r.returncode:
                raise ClientError('系统图形依赖安装失败，未宣称修复成功')
        actions.append('系统图形依赖已补齐')
    if value.get('needsFirewall'):
        if sys.platform == 'win32':
            encoded = base64.b64encode(json.dumps(value).encode()).decode()
            code = "$ErrorActionPreference='Stop'; $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json; "
            code += "$fw=New-Object -ComObject HNetCfg.FwPolicy2; $old=$null; try{$old=$fw.Rules.Item($p.ruleName)}catch{}; if($old -and $old.Grouping -ne 'Codex Relay owned rules'){throw 'Rule belongs to another owner'}; "
            code += "$r=if($old){$old}else{New-Object -ComObject HNetCfg.FWRule}; $r.Name=$p.ruleName; $r.Grouping='Codex Relay owned rules'; $r.Enabled=$false; $r.Action=1; $r.Protocol=6; $r.ApplicationName=$p.executable; $r.RemoteAddresses=($p.scopes -join ','); "
            if value['direction'] == 'in':
                profiles = 2147483647 if value['scopes'] == ['100.64.0.0/10'] else 3
                code += '$r.Direction=1; $r.LocalPorts=[string]$p.port; $r.Profiles=' + str(profiles) + '; '
            else:
                code += '$r.Direction=2; $r.RemotePorts=[string]$p.port; $r.Profiles=2147483647; '
            code += '$r.Enabled=$true; if(!$old){$fw.Rules.Add($r)}; $verified=$fw.Rules.Item($p.ruleName); @{Name=$verified.Name;Enabled=$verified.Enabled}|ConvertTo-Json -Compress'
            r = runner([powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', base64.b64encode(code.encode('utf-16-le')).decode()], timeout=30)
            if r.returncode:
                raise ClientError('Windows 防火墙授权失败或规则被系统策略拒绝')
            data = json.loads(r.stdout)
            if str(data.get('Enabled')).lower() not in ('true', '1'):
                raise ClientError('防火墙规则没有启用，未宣称开放成功')
            actions.append('已配置仅限当前程序和目标端口的 TCP 允许规则')
        elif sys.platform == 'darwin':
            for operation in ('--add', '--unblockapp'):
                r = runner(['/usr/libexec/ApplicationFirewall/socketfilterfw', operation, value['executable']], timeout=15)
                if r.returncode:
                    raise ClientError('macOS 应用防火墙授权失败')
            actions.append('已允许当前服务端程序接收入站连接')
        elif value['direction'] == 'in' and shutil.which('ufw'):
            for scope in value['scopes']:
                argv = ['ufw', 'allow', 'in', 'proto', 'tcp', 'from', scope, 'to', value['bind'] if value['bind'] not in ('0.0.0.0', '::') else 'any', 'port', str(value['port']), 'comment', value['ruleName']]
                r = runner(argv, timeout=20)
                if r.returncode:
                    raise ClientError('UFW 规则添加失败')
            actions.append('已保存当前监听端口的限定来源 UFW 允许规则；未启用或关闭整个防火墙')
    return {'ok': True, 'ruleName': value['ruleName'], 'actions': actions, 'notes': value.get('notes', []), 'message': '；'.join(actions) if actions else '没有需要修改的端口或系统依赖'}

def is_admin():
    if sys.platform == 'win32':
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0

def prepare_authorization_result(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump({'ok': False, 'pending': True}, stream)

def read_authorization_result(path, exitcode):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except PermissionError as exc:
        raise ClientError('无法读取系统授权结果；配置可能已执行，请重新检测，不要反复授权') from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ClientError('系统授权未返回完整结果；请重新检测端口与依赖') from exc
    if not isinstance(data, dict) or data.get('pending'):
        raise ClientError('系统授权未返回完整结果；请重新检测端口与依赖')
    if data.get('ok') is not True:
        raise ClientError(data.get('error') or '系统授权未完成，未宣称修复成功')
    if exitcode:
        raise ClientError('系统授权未完成，未宣称修复成功')
    return data

def repair(value, password=None):
    validate_plan(value)
    if not value.get('needsFirewall') and (not value.get('packages')):
        return apply_plan(value)
    if is_admin():
        return apply_plan(value)
    if not getattr(sys, 'frozen', False):
        raise ClientError('系统修复请使用完整的原生安装包')
    with tempfile.TemporaryDirectory(prefix='relay-system-authorize-') as directory:
        source, result = (Path(directory) / 'plan.json', Path(directory) / 'result.json')
        source.write_text(json.dumps(value), encoding='utf-8')
        prepare_authorization_result(result)
        args = [sys.executable, '--system-authorized', str(source), str(result)]
        if sys.platform == 'win32':
            import win32api, win32con, win32event, win32process
            from win32com.shell import shell, shellcon
            info = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas', lpFile=sys.executable, lpParameters=subprocess.list2cmdline(args[1:]), nShow=win32con.SW_HIDE)
            handle = info['hProcess']
            try:
                if win32event.WaitForSingleObject(handle, 360000) != win32event.WAIT_OBJECT_0:
                    raise ClientError('系统授权操作超时')
                exitcode = win32process.GetExitCodeProcess(handle)
            finally:
                win32api.CloseHandle(handle)
        elif sys.platform == 'darwin':
            command = ' '.join((shlex.quote(a) for a in args))
            text = 'do shell script ' + json.dumps(command) + ' with administrator privileges'
            r = run(['/usr/bin/osascript', '-e', text], timeout=360)
            exitcode = r.returncode
        else:
            if shutil.which('pkexec'):
                r = run(['pkexec', *args], timeout=360)
            elif password:
                r = run(['sudo', '-S', '-p', '', '--', *args], input_text=password + '\n', timeout=360)
            else:
                raise ClientError('需要系统授权，请输入本机 sudo 密码；不会保存')
            exitcode = r.returncode
        password = None
        return read_authorization_result(result, exitcode)

def authorized_main(source, result):
    try:
        value = json.loads(Path(source).read_text(encoding='utf-8-sig'))
        data = apply_plan(value)
    except Exception as exc:
        data = {'ok': False, 'error': str(exc)}
    Path(result).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    if os.name != 'nt':
        Path(result).chmod(420)
    return 0 if data['ok'] else 1

def native_probe(role, bind='127.0.0.1', port=8799, url=None):
    value = plan(role, bind=bind, port=port, url=url)
    checks = {'dependencies_present': value['dependencies']['passed'], 'no_inbound_rule_for_client': role != 'client' or value['direction'] == 'out', 'no_extra_loopback_rule': not ipaddress.ip_address(bind).is_loopback or not value['needsFirewall']}
    if url:
        checks['endpoint_reachable'] = value['endpoint']['tcpReachable'] and value['endpoint'].get('tlsVerified') is not False
    return {'passed': all(checks.values()), 'checks': checks, 'plan': value, 'systemChanges': 0}

def probe_args(role, args):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8799)
    parser.add_argument('--url')
    options = parser.parse_args(args)
    return native_probe(role, **vars(options))
