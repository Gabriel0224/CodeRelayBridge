"""Restart only the relay and its agents, never a Codex App or user task."""
import argparse
import base64
import json
import os
if os.name != 'nt':
    import fcntl
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
SERVICE = 'codex-desktop-relay.service'
MODES = ('service', 'all')
PENDING = ('queued', 'stopping', 'agents', 'starting', 'checking')

def read_record(state_dir):
    try:
        path = Path(state_dir).expanduser() / 'panel-restart.json'
        if path.stat().st_size > 16384:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def write_record(state_dir, value):
    root = Path(state_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=448)
    path = root / 'panel-restart.json'
    temporary = root / ('panel-restart-' + str(uuid.uuid4()) + '.next')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

class RestartManager:

    def __init__(self, config_path, state_dir, runner=None):
        self.config_path = str(Path(config_path).resolve()) if config_path else None
        self.state_dir = state_dir
        self.runner = runner or subprocess.run
        self.lock = threading.Lock()

    def status(self):
        return {'enabled': bool(self.config_path), 'restart': read_record(self.state_dir)}

    def request(self, request_id, mode):
        if mode not in MODES:
            raise ValueError('请选择仅重启服务或重启全部面板组件')
        request_id = str(uuid.UUID(str(request_id)))
        if not self.config_path:
            raise RuntimeError('此实例未配置面板重启入口')
        with self.lock:
            from crb.server.office_engine import assert_idle
            assert_idle({'state_dir': self.state_dir})
            old = read_record(self.state_dir)
            if old.get('requestId') == request_id:
                return {'ok': True, 'accepted': True, 'requestId': request_id, 'restart': old}
            if old.get('phase') in PENDING and time.time() - old.get('requestedAt', 0) < 300:
                raise RuntimeError('面板重启已在进行中，请等待，不会重复重启')
            record = {'requestId': request_id, 'mode': mode, 'phase': 'queued', 'requestedAt': time.time(), 'oldPID': os.getpid(), 'agents': []}
            write_record(self.state_dir, record)
            command = ['systemd-run', '--user', '--collect', '--quiet', '--unit=codex-panel-restart-' + request_id, '/usr/bin/python3', str(Path(__file__).resolve()), '--config', self.config_path, '--mode', mode, '--request-id', request_id, '--delay', '2']
            try:
                self.runner(command, check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception as exc:
                record.update(phase='failed', error='无法启动独立重启程序', errorType=type(exc).__name__)
                write_record(self.state_dir, record)
                raise RuntimeError(record['error']) from exc
            return {'ok': True, 'accepted': True, 'requestId': request_id, 'restart': record}
WINDOWS_CLIENT_RESTART = '\n$ErrorActionPreference=\'Stop\'\n$clientRoot=Join-Path $env:LOCALAPPDATA \'CodexRelayClient\'\n$clientExe=Join-Path $clientRoot \'app\\RelayClient.exe\'\n$session=Join-Path $clientRoot \'client-session.json\'\nif ((Test-Path -LiteralPath $clientExe) -and (Test-Path -LiteralPath $session)) {\n $runtime=Join-Path $clientRoot \'runtime\\codex.json\'\n if (-not (Test-Path -LiteralPath $runtime)) {$runtime=Join-Path $clientRoot \'runtime\\deepseek.json\'}\n $old=Get-Content -LiteralPath $runtime -Raw -Encoding UTF8 | ConvertFrom-Json\n $report=Join-Path $clientRoot (\'runtime\\restart-\'+[Guid]::NewGuid().ToString()+\'.json\')\n $p=Start-Process -FilePath $clientExe -ArgumentList @(\'--client-action\',(\'"\'+$report+\'"\'),\'restart\') -WindowStyle Hidden -Wait -PassThru\n if ($p.ExitCode -ne 0) {throw \'Client UI did not accept restart\'}\n $reply=Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json\n if (-not ($reply.accepted -or $reply.result.accepted)) {throw \'Client restart not acknowledged\'}\n Remove-Item -LiteralPath $report\n for ($i=0; $i -lt 70; $i++) {\n  Start-Sleep -Milliseconds 500\n  try {$new=Get-Content -LiteralPath $runtime -Raw -Encoding UTF8 | ConvertFrom-Json} catch {continue}\n  if (($new.owner_pid -ne $old.owner_pid -or $new.owner_created -ne $old.owner_created) -and $new.owner_pid -and $new.phase -eq \'running\') {\n   @{status=\'restarted\';native=$true;clientRestart=$true;oldPID=$old.owner_pid;newPID=$new.owner_pid} | ConvertTo-Json -Compress\n   return\n  }\n }\n throw \'New client did not reconnect\'\n}\n'
WINDOWS_AGENT_RESTART = WINDOWS_CLIENT_RESTART + '\n$ErrorActionPreference=\'Stop\'\n$native=Join-Path $env:LOCALAPPDATA \'CodexRelayClient\\app\\RelayClient.exe\'\n$task=Get-ScheduledTask -TaskName \'Codex Desktop Relay Agent\'\nif (@($task.Actions).Count -eq 1 -and $task.Actions[0].Execute -eq $native -and $task.Actions[0].Arguments -eq \'--bridge codex\') {\n $report=Join-Path $env:LOCALAPPDATA (\'CodexRelayClient\\runtime\\restart-\'+[Guid]::NewGuid().ToString()+\'.json\')\n $p=Start-Process -FilePath $native -ArgumentList @(\'--control\',(\'"\'+$report+\'"\'),\'codex\',\'restart\') -WindowStyle Hidden -Wait -PassThru\n if ($p.ExitCode -ne 0) {throw \'Native bridge restart failed\'}\n $value=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json\n if (-not $value.result) {throw \'Native bridge did not confirm restart\'}\n Remove-Item -LiteralPath $report\n @{status=\'restarted\';native=$true} | ConvertTo-Json -Compress\n return\n}\n$root=Join-Path ([Environment]::GetFolderPath(\'UserProfile\')) \'codex-desktop-relay\'\n$entries=@((Join-Path $root \'run-agent.pyw\'),(Join-Path $root \'deploy\\run-agent.pyw\'))\n$find={ @(Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" -Property ProcessId,Name,CommandLine,CreationDate | Where-Object {\n $command=$_.CommandLine\n $command -and @($entries | Where-Object { $command.IndexOf($_,[StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0\n}) }\n$old=& $find\nif ($old.Count -gt 1) { throw \'Multiple panel agents; refusing broad termination\' }\nStop-ScheduledTask -TaskName \'Codex Desktop Relay Agent\'\nforeach ($p in $old) {\n $same=Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)" -Property ProcessId,CreationDate\n if ($same -and $same.CreationDate -eq $p.CreationDate) { Stop-Process -Id $p.ProcessId -Force }\n}\nStart-ScheduledTask -TaskName \'Codex Desktop Relay Agent\'\n$found=@()\nfor ($i=0; $i -lt 20; $i++) {\n Start-Sleep -Milliseconds 500\n $found=& $find\n if ($found.Count -eq 1 -and $found[0].ProcessId -notin @($old.ProcessId)) { break }\n}\nif ($found.Count -ne 1 -or $found[0].ProcessId -in @($old.ProcessId)) { throw \'Panel agent restart failed\' }\n@{oldPID=@($old.ProcessId);newPID=$found[0].ProcessId;status=\'restarted\'} | ConvertTo-Json -Compress\n'
POSIX_AGENT_RESTART = "\nimport json,os,re,subprocess,sys,time\nfrom pathlib import Path\nimport tempfile\nroot=(Path.home()/'Library/Application Support/CodexRelayClient' if sys.platform=='darwin'\n      else Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'CodexRelayClient')\nnative=root/('app/Relay Client.app/Contents/MacOS/RelayClient' if sys.platform=='darwin' else 'app/RelayClient')\nkey='codex'\nif native.is_file() and (root/'client-session.json').is_file():\n runtime=root/'runtime'/('codex.json' if (root/'runtime/codex.json').exists() else 'deepseek.json')\n old=json.loads(runtime.read_text(encoding='utf-8'))\n with tempfile.TemporaryDirectory(prefix='relay-restart-ui-') as directory:\n  report=Path(directory)/'result.json'\n  subprocess.run([str(native),'--client-action',str(report),'restart'],check=True,timeout=15)\n  reply=json.loads(report.read_text(encoding='utf-8'))\n  assert reply.get('accepted') or reply.get('result',{}).get('accepted'),'Client UI did not accept restart'\n for _ in range(70):\n  time.sleep(.5)\n  try:new=json.loads(runtime.read_text(encoding='utf-8'))\n  except (OSError,ValueError):continue\n  if new.get('owner_pid') and (new.get('owner_pid'),new.get('owner_created'))!=(old.get('owner_pid'),old.get('owner_created')) and new.get('phase')=='running':break\n else:raise RuntimeError('New client did not reconnect')\n print(json.dumps({'status':'restarted','native':True,'clientRestart':True,'oldPID':old.get('owner_pid'),'newPID':new['owner_pid']}))\n raise SystemExit()\nowned=(Path.home()/'Library/LaunchAgents'/('com.codexrelay.client.'+key+'.plist') if sys.platform=='darwin'\n       else Path.home()/'.config/systemd/user'/('relay-client-'+key+'.service'))\nif native.is_file() and owned.is_file():\n with tempfile.TemporaryDirectory(prefix='relay-restart-check-') as directory:\n  report=Path(directory)/'result.json'\n  subprocess.run([str(native),'--control',str(report),key,'restart'],check=True,timeout=30)\n  value=json.loads(report.read_text());assert value.get('result'),'Native bridge did not confirm restart'\n print(json.dumps({'status':'restarted','native':True}))\n raise SystemExit()\nif sys.platform=='darwin':\n name='gui/'+str(os.getuid())+'/com.'+Path.home().name+'.codex-desktop-relay-agent'\n def pid():\n  r=subprocess.run(['launchctl','print',name],capture_output=True,text=True,timeout=8)\n  m=re.search(r'\\bpid = (\\d+)',r.stdout);return int(m.group(1)) if m else 0\n old=pid()\n subprocess.run(['launchctl','kickstart','-k',name],check=True,timeout=15,stdout=subprocess.DEVNULL)\n new=pid()\n for _ in range(20):\n  if new and new!=old:break\n  time.sleep(.5);new=pid()\nelse:\n name='codex-desktop-agent.service'\n def pid():return int(subprocess.check_output(['systemctl','--user','show',name,'-p','MainPID','--value'],text=True,timeout=8).strip() or '0')\n old=pid()\n subprocess.run(['systemctl','--user','stop',name],check=True,timeout=15)\n assert pid()==0,'Panel agent did not fully stop'\n subprocess.run(['systemctl','--user','start',name],check=True,timeout=15)\n new=pid()\nassert new and new!=old,'Panel agent restart failed'\nprint(json.dumps({'oldPID':old,'newPID':new,'status':'restarted'}))\n"

def restart_agent(host_id, cfg, runner=subprocess.run):
    if host_id not in ('win', 'mint', 'mac', 'local'):
        raise ValueError('未知面板代理')
    if host_id == 'local':
        if host_id not in cfg.get('agents', {}):
            return {'host': host_id, 'status': 'unconfigured'}
        try:
            result = runner(['/usr/bin/python3', '-c', POSIX_AGENT_RESTART], check=True, timeout=45, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
            value = json.loads(result.stdout.strip().splitlines()[-1])
            if value.get('status') != 'restarted':
                raise RuntimeError('代理未确认重启')
            return {'host': host_id, 'status': 'restarted', 'oldPID': value.get('oldPID'), 'newPID': value.get('newPID'), **({'clientRestart': True} if value.get('clientRestart') else {})}
        except Exception as exc:
            return {'host': host_id, 'status': 'failed', 'error': '本机面板代理未确认重启', 'errorType': type(exc).__name__}
    target = cfg.get('ssh_hosts', {}).get(host_id)
    if not target:
        return {'host': host_id, 'status': 'unconfigured'}
    options = ['ssh', '-F', os.devnull, '-i', target['identity_file'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + target['known_hosts_file'], '-p', str(target.get('port', 22)), target['user'] + '@' + target['hostname']]
    if host_id == 'win':
        encoded = base64.b64encode(WINDOWS_AGENT_RESTART.encode('utf-16-le')).decode()
        remote = "& ([ScriptBlock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded + "'))))"
    else:
        encoded = base64.b64encode(POSIX_AGENT_RESTART.encode()).decode()
        python = '/opt/homebrew/bin/python3' if host_id == 'mac' else 'python3'
        import shlex
        remote = python + ' -c ' + shlex.quote('import base64;exec(base64.b64decode(' + repr(encoded) + '))')
    try:
        result = runner([*options, remote], check=True, timeout=45, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
        value = json.loads(result.stdout.strip().splitlines()[-1])
        if value.get('status') != 'restarted':
            raise RuntimeError('代理未确认重启')
        return {'host': host_id, 'status': 'restarted', 'oldPID': value.get('oldPID'), 'newPID': value.get('newPID'), **({'clientRestart': True} if value.get('clientRestart') else {})}
    except Exception as exc:
        return {'host': host_id, 'status': 'failed', 'error': 'SSH 不可达或代理未确认重启', 'errorType': type(exc).__name__}
WINDOWS_HARNESS_AGENT_RESTART = WINDOWS_CLIENT_RESTART + '\n$ErrorActionPreference=\'Stop\'\n$native=Join-Path $env:LOCALAPPDATA \'CodexRelayClient\\app\\RelayClient.exe\'\n$task=Get-ScheduledTask -TaskName \'DeepSeek Harness Relay Agent\'\nif (@($task.Actions).Count -eq 1 -and $task.Actions[0].Execute -eq $native -and $task.Actions[0].Arguments -eq \'--bridge deepseek\') {\n $report=Join-Path $env:LOCALAPPDATA (\'CodexRelayClient\\runtime\\restart-\'+[Guid]::NewGuid().ToString()+\'.json\')\n $p=Start-Process -FilePath $native -ArgumentList @(\'--control\',(\'"\'+$report+\'"\'),\'deepseek\',\'restart\') -WindowStyle Hidden -Wait -PassThru\n if ($p.ExitCode -ne 0) {throw \'Native Harness restart failed\'}\n $value=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json\n if (-not $value.result) {throw \'Native Harness did not confirm restart\'}\n Remove-Item -LiteralPath $report\n @{status=\'restarted\';native=$true} | ConvertTo-Json -Compress\n return\n}\n$root=Join-Path ([Environment]::GetFolderPath(\'UserProfile\')) \'codex-desktop-relay\'\n$launcher=Join-Path $root \'run-harness.pyw\'\n$python=Join-Path $root \'harness-venv\\Scripts\\pythonw.exe\'\n$task=Get-ScheduledTask -TaskName \'DeepSeek Harness Relay Agent\'\nif (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $python -or $task.Actions[0].Arguments -ne (\'"\'+$launcher+\'"\')) {throw \'Unexpected Harness task\'}\n$find={ @(Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | Where-Object {\n $_.CommandLine -and $_.CommandLine.EndsWith(\'"\'+$launcher+\'"\',[StringComparison]::OrdinalIgnoreCase)\n}) }\n$old=& $find\nif ($old.Count -gt 2) {throw \'Multiple Harness bridges; refusing broad termination\'}\nStop-ScheduledTask -TaskName \'DeepSeek Harness Relay Agent\'\nforeach ($p in $old) {\n $same=Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)"\n if ($same -and $same.CreationDate -eq $p.CreationDate) {Stop-Process -Id $p.ProcessId -Force}\n}\n# Task stop and singleton-mutex release are asynchronous. Do not race a new\n# launcher against the old bridge; never terminate unrelated Python processes.\nfor ($i=0; $i -lt 20; $i++) {\n if ((& $find).Count -eq 0 -and (Get-ScheduledTask -TaskName \'DeepSeek Harness Relay Agent\').State -ne \'Running\') {break}\n Start-Sleep -Milliseconds 250\n}\nif ((& $find).Count -ne 0) {throw \'Old Harness bridge has not stopped\'}\nStart-ScheduledTask -TaskName \'DeepSeek Harness Relay Agent\'\n$found=@()\nfor ($i=0; $i -lt 20; $i++) {\n Start-Sleep -Milliseconds 500\n $found=& $find\n if ($found.Count -ge 1 -and $found.Count -le 2 -and @($found | Where-Object {$_.ProcessId -in @($old.ProcessId)}).Count -eq 0) {break}\n}\nif ($found.Count -lt 1 -or $found.Count -gt 2 -or @($found | Where-Object {$_.ProcessId -in @($old.ProcessId)}).Count) {throw \'Harness bridge restart failed\'}\n@{oldPID=@($old.ProcessId);newPID=@($found.ProcessId);status=\'restarted\'} | ConvertTo-Json -Compress\n'
POSIX_HARNESS_AGENT_RESTART = POSIX_AGENT_RESTART.replace('.codex-desktop-relay-agent', '.deepseek-harness-relay-agent').replace('codex-desktop-agent.service', 'deepseek-harness-relay.service').replace("key='codex'", "key='deepseek'")

def restart_application_agent(host_id, cfg, runner=subprocess.run):
    node = cfg.get('application_nodes', {}).get(host_id, {})
    physical = node.get('physicalHostId')
    if node.get('applicationId') != 'deepseek' or physical not in ('win', 'mint', 'mac', 'local') or host_id != physical + '.deepseek' or (host_id not in cfg.get('agents', {})):
        return {'host': host_id, 'status': 'unconfigured'}
    if physical == 'local':
        try:
            result = runner(['/usr/bin/python3', '-c', POSIX_HARNESS_AGENT_RESTART], check=True, timeout=45, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
            value = json.loads(result.stdout.strip().splitlines()[-1])
            if value.get('status') != 'restarted':
                raise RuntimeError('Local Harness bridge restart not confirmed')
            return {'host': host_id, 'status': 'restarted', 'oldPID': value.get('oldPID'), 'newPID': value.get('newPID')}
        except Exception as exc:
            return {'host': host_id, 'status': 'failed', 'error': '服务器 Harness 面板桥接未确认重启', 'errorType': type(exc).__name__}
    target = cfg.get('ssh_hosts', {}).get(physical)
    if not target:
        return {'host': host_id, 'status': 'unconfigured'}
    options = ['ssh', '-F', os.devnull, '-i', target['identity_file'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + target['known_hosts_file'], '-p', str(target.get('port', 22)), target['user'] + '@' + target['hostname']]
    if physical == 'win':
        encoded = base64.b64encode(WINDOWS_HARNESS_AGENT_RESTART.encode('utf-16-le')).decode()
        remote = "& ([ScriptBlock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded + "'))))"
    else:
        import shlex
        python = '/opt/homebrew/bin/python3' if physical == 'mac' else 'python3'
        remote = python + ' -c ' + shlex.quote(POSIX_HARNESS_AGENT_RESTART)
    try:
        result = runner([*options, remote], check=True, timeout=45, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace')
        value = json.loads(result.stdout.strip().splitlines()[-1])
        if value.get('status') != 'restarted':
            raise RuntimeError('Harness bridge restart not confirmed')
        return {'host': host_id, 'status': 'restarted', 'oldPID': value.get('oldPID'), 'newPID': value.get('newPID')}
    except Exception as exc:
        return {'host': host_id, 'status': 'failed', 'error': 'Harness 面板桥接未确认重启', 'errorType': type(exc).__name__}

def restart_panel(config_path, mode, request_id, delay=0, runner=subprocess.run, health=None):
    if mode not in MODES:
        raise ValueError('未知重启方式')
    request_id = str(uuid.UUID(str(request_id)))
    cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
    state_dir = Path(cfg['state_dir']).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True, mode=448)
    with (state_dir / 'panel-restart.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'requestId': request_id, 'mode': mode, 'phase': 'failed', 'error': '另一项面板重启正在进行，请等待其完成'}
        previous = read_record(state_dir)
        if previous.get('requestId') == request_id and previous.get('phase') in ('completed', 'partial'):
            return previous
        record = {'requestId': request_id, 'mode': mode, 'requestedAt': previous.get('requestedAt', time.time()) if previous.get('requestId') == request_id else time.time(), 'phase': 'queued', 'agents': []}

        def phase(value, **fields):
            record.update(phase=value, **fields)
            write_record(state_dir, record)

        def control(action):
            return runner(['systemctl', '--user', action, SERVICE], check=True, timeout=25, stdin=subprocess.DEVNULL, capture_output=True, text=True)

        def pid():
            r = runner(['systemctl', '--user', 'show', SERVICE, '-p', 'MainPID', '--value'], check=True, timeout=8, capture_output=True, text=True)
            return int(r.stdout.strip() or '0')
        if delay:
            time.sleep(min(max(delay, 0), 3))
        stopped = False
        try:
            old = pid()
            phase('stopping', oldPID=old)
            control('stop')
            stopped = True
            if pid() != 0:
                raise RuntimeError('面板旧服务没有完全关闭')
            if mode == 'all':
                phase('agents')
                targets = ('win', 'mint', 'mac') + (('local',) if 'local' in cfg.get('agents', {}) else ())
                for host_id in targets:
                    record['agents'].append(restart_agent(host_id, cfg, runner))
                    write_record(state_dir, record)
                for host_id, node in cfg.get('application_nodes', {}).items():
                    if node.get('applicationId') == 'deepseek':
                        record['agents'].append(restart_application_agent(host_id, cfg, runner))
                        write_record(state_dir, record)
            phase('starting')
            control('start')
            stopped = False
            new = pid()
            if not new or new == old:
                raise RuntimeError('面板服务没有生成新进程')
            phase('checking', newPID=new)
            if health:
                health(cfg)
            else:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for attempt in range(20):
                    try:
                        with op.open(f"http://{cfg['bind']}:{cfg['port']}/healthz", timeout=2) as reply:
                            if json.load(reply).get('ok'):
                                break
                    except OSError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError('面板健康检查失败')
            phase('partial' if any((a['status'] != 'restarted' for a in record['agents'])) else 'completed', completedAt=time.time())
        except Exception as exc:
            phase('failed', error='重启未完成，请检查桌面脚本或服务日志', errorType=type(exc).__name__)
            if stopped:
                try:
                    control('start')
                except Exception:
                    pass
        return record

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--request-id', default=None)
    parser.add_argument('--delay', type=float, default=0)
    parser.add_argument('--interactive', action='store_true')
    args = parser.parse_args()
    mode = args.mode
    if args.interactive and (not mode):
        if shutil.which('zenity'):
            choice = subprocess.run(['zenity', '--list', '--radiolist', '--title=重启 Codex 面板', '--text=只重启面板，不重启 Codex App。网页 SSH 终端会断开。', '--column=选择', '--column=方式', 'TRUE', '仅重启面板服务', 'FALSE', '重启全部面板组件'], capture_output=True, text=True)
            if choice.returncode:
                return
            mode = 'all' if choice.stdout.strip() == '重启全部面板组件' else 'service'
        else:
            print('1. 仅重启面板服务\n2. 重启全部面板组件\n其他输入取消。Codex App 不会停止，网页终端会断开。')
            answer = input('选择：').strip()
            if answer not in ('1', '2'):
                return
            mode = 'service' if answer == '1' else 'all'
    if not mode:
        parser.error('请提供 --mode 或 --interactive')
    result = restart_panel(args.config, mode, args.request_id or str(uuid.uuid4()), args.delay)
    print(json.dumps(result, ensure_ascii=False))
    if args.interactive and shutil.which('zenity'):
        label = '面板已重新启动' if result['phase'] == 'completed' else '面板重启结果：' + result['phase']
        if result.get('error'):
            label += '\n' + result['error']
        failed = [a['host'] for a in result.get('agents', []) if a['status'] != 'restarted']
        if failed:
            label += '\n未确认重启的代理：' + '、'.join(failed)
        subprocess.run(['zenity', '--info' if result['phase'] == 'completed' else '--warning', '--text=' + label], check=False)
    if result['phase'] == 'failed':
        raise SystemExit(1)
if __name__ == '__main__':
    main()
