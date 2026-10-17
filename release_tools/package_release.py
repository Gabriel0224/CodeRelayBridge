"""Dual-edition clients and one universal server; native builds only."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desktop_client import VERSION
from release_tools.release_matrix import PLANS, LABELS, bridge_hashes, inspect_bundle, package_name, policy, validate_release
SUPPORT = ROOT / 'release_tools'
DEFAULT_OUTPUT = ROOT.parent / ('Relay安装包-v' + VERSION)

def sha256(file):
    digest = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def metadata(root, role, edition, audit):
    files = []
    for file in sorted(root.rglob('*')):
        if file.is_symlink():
            files.append({'file': file.relative_to(root).as_posix(), 'symlink': os.readlink(file)})
        elif file.is_file():
            files.append({'file': file.relative_to(root).as_posix(), 'bytes': file.stat().st_size, 'sha256': sha256(file)})
    value = {'version': VERSION, 'role': role, 'edition': edition, 'editionLabel': LABELS[edition], 'credentialsBundled': False, 'pythonInstallationRequired': False, 'audit': audit, 'files': files}
    (root / 'RELEASE.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def notices(root):
    """Ship the actual runtime-distribution notices, not development environments."""
    names = ['PySide6-Essentials', 'shiboken6', 'psutil', 'PyYAML', 'websocket-client', 'PyInstaller', 'cryptography', 'keyring', 'cffi', 'jaraco.classes', 'jaraco.context', 'jaraco.functools', 'more-itertools', 'pycparser']
    if sys.platform == 'win32':
        names += ['pywin32', 'pywinpty']
    elif sys.platform.startswith('linux'):
        names += ['SecretStorage', 'jeepney']
    versions = {}
    directory = root / 'LICENSES'
    directory.mkdir()
    for name in names:
        dist = importlib.metadata.distribution(name)
        versions[name] = dist.version
        for file in dist.files or []:
            if file.parts and '.dist-info' in file.parts[0] and any((token in file.name.lower() for token in ('license', 'copying', 'notice'))):
                target = directory / name / Path(*file.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dist.locate_file(file), target)
    python_license = Path(sys.base_prefix) / ('LICENSE.txt' if sys.platform == 'win32' else 'LICENSE')
    if python_license.is_file():
        shutil.copy2(python_license, directory / 'Python-LICENSE.txt')
    return versions

def prepare(root, guide=None):
    root.mkdir(parents=True, exist_ok=False)
    guide = guide or SUPPORT / 'README.zh-CN.md'
    shutil.copy2(guide, root / '安装说明.md')
    shutil.copy2(guide, root / '安装说明.txt')
    for filename in ('LICENSE', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md'):
        if (ROOT / filename).is_file():
            shutil.copy2(ROOT / filename, root / filename)
    if (ROOT / 'docs').is_dir():
        shutil.copytree(ROOT / 'docs', root / 'docs')
    if (SUPPORT / 'README-question-link.zh-CN.md').is_file():
        shutil.copy2(SUPPORT / 'README-question-link.zh-CN.md', root / '提问窗口联动说明.md')

def windows(output, client_only=False):
    policy()
    output.mkdir(parents=True, exist_ok=True)
    qa = output / 'verification'
    qa.mkdir(exist_ok=True)
    results = []
    for role, product, edition in PLANS:
        if client_only and role != 'client':
            continue
        source = ROOT / 'desktop_client/dist/win32' / product
        if edition == 'standard':
            source = source.parent / 'standard' / product
        assert (source / (product + '.exe')).is_file()
        archive = output / package_name(product, VERSION, edition, 'Windows-x64.zip')
        if archive.exists():
            raise RuntimeError('Refusing to overwrite a published package: ' + archive.name)
        stage = output / '.staging' / (product + '-' + edition + '-Windows-' + str(time.time_ns()))
        prepare(stage)
        shutil.copytree(source, stage / product)
        from release_tools.native_installers import windows_uninstaller
        windows_uninstaller(stage / product)
        deps = {'platform': 'Windows', 'architecture': 'x64', 'minimumOS': 'Windows 10 22H2', 'pythonBundled': True, 'qtBundled': True, 'visualCppRuntimeBundled': True, 'systemPythonRequired': False, 'buildRuntimeVersions': notices(stage)}
        if role == 'client':
            manifest = json.loads((source / '_internal/relay_bridge_bundle/manifest.json').read_text(encoding='utf-8'))
            deps['bridgeSourceHashes'] = bridge_hashes(manifest, edition)
        identity = json.loads((source / '_internal/relay-edition.json').read_text(encoding='utf-8'))
        assert identity == {'edition': edition, 'role': role}, identity
        (stage / 'DEPENDENCIES.json').write_text(json.dumps(deps, indent=2), encoding='utf-8')
        exe = stage / product / (product + '.exe')
        deps['compiledPayloadAudit'] = inspect_bundle(exe, edition, VERSION)
        checks = []
        env = dict(os.environ, PATH=os.path.join(os.environ['SystemRoot'], 'System32'), QT_QPA_PLATFORM='offscreen')
        for flag in ['--ui-check', '--self-test', '--package-probe', '--session-probe', '--independent-probe', '--system-probe'] if role == 'client' else ['--ui-check', '--self-test', '--first-setup-probe', '--ipv6-setup-probe', '--system-probe']:
            report = qa / (product + '-' + edition + '-' + flag[2:] + '.json')
            proc = subprocess.run([str(exe), flag, str(report)], env=env, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=45)
            value = json.loads(report.read_text(encoding='utf-8'))
            assert proc.returncode == 0 and value.get('passed') is not False, (flag, proc.stderr.decode(errors='replace'))
            if flag == '--package-probe':
                assert value['edition'] == edition and value['version'] == VERSION, value
                assert all(value['edition_contract'].values()), value
            if flag == '--independent-probe':
                assert not value.get('skipped') and all(value['checks'].values()), value
            checks.append({'check': flag, 'passed': True, 'tests': value.get('tests')})
        deps['cleanDeveloperPathChecks'] = checks
        (stage / 'DEPENDENCIES.json').write_text(json.dumps(deps, indent=2), encoding='utf-8')
        metadata(stage, role, edition, deps)
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for file in sorted(stage.rglob('*')):
                if file.is_file():
                    bundle.write(file, file.relative_to(stage).as_posix())
        with zipfile.ZipFile(archive) as bundle:
            assert bundle.testzip() is None
        compatibility = {'file': archive.name, 'bytes': archive.stat().st_size, 'sha256': sha256(archive)}
        from release_tools.native_installers import build_windows
        archive = build_windows(stage, output, role, edition, VERSION)
        from desktop_client.relay_updates import extract_package, validate_identity
        from desktop_client.update_apply import tree_hashes
        probe = qa / (product + '-' + edition + '-installer-extraction-' + str(time.time_ns()))
        probe.mkdir()
        extracted = extract_package(archive, probe, role)
        validate_identity(extracted, role, edition)
        assert tree_hashes(extracted) == tree_hashes(stage / product), 'Installer changed the verified native payload'
        checks.append({'check': 'native-installer-extraction', 'passed': True})
        item = {'version': VERSION, 'platform': 'Windows-x64', 'role': role, 'edition': edition, 'editionLabel': LABELS[edition], 'file': archive.name, 'bytes': archive.stat().st_size, 'sha256': sha256(archive), 'checks': checks, 'legacyUpdate': compatibility, 'bridgeSourceHashes': deps.get('bridgeSourceHashes', {})}
        results.append(item)
        print(json.dumps(item), flush=True)
    (output / 'verification-windows.json').write_text(json.dumps({'version': VERSION, 'packages': results}, indent=2), encoding='utf-8')
REMOTE = "\nimport hashlib,importlib.metadata,json,os,shutil,subprocess,sys,tarfile,time,zipfile\nfrom pathlib import Path\nfrom io import BytesIO\n\ndef digest(file):\n value=hashlib.sha256()\n with file.open('rb') as stream:\n  for part in iter(lambda:stream.read(1024*1024),b''):value.update(part)\n return value.hexdigest()\n\nns={'__name__':'dependency_audit'}\nexec(audit_code,ns)\nmatrix={'__file__':str(Path(stage)/'release_tools/release_matrix.py')};exec(matrix_code,matrix)\noutput=Path(stage)/('deliverables-'+version+'-'+str(time.time_ns()));output.mkdir(exist_ok=False)\nresults=[]\nfor role,product,edition in plans:\n dist=Path(stage)/'desktop_client/dist'/sys.platform\n if edition=='standard':dist=dist/'standard'\n source=dist/(('Relay Client.app' if role=='client' else 'Relay Server.app') if sys.platform=='darwin' else product)\n assert source.is_dir()\n deps=ns['audit'](source)\n assert deps['passed'],deps\n resource=source/('Contents/Frameworks' if sys.platform=='darwin' else '_internal')\n identity=json.loads((resource/'relay-edition.json').read_text())\n assert identity=={'edition':edition,'role':role},identity\n if role=='client':\n  manifest=json.loads((resource/'relay_bridge_bundle/manifest.json').read_text())\n  deps['bridgeSourceHashes']=matrix['bridge_hashes'](manifest,edition)\n work=output/(product+'-'+edition+'-stage-'+str(time.time_ns()));work.mkdir()\n shutil.copytree(source,work/source.name,symlinks=True)\n for name in ('LICENSE','LICENSING.md','THIRD_PARTY_NOTICES.md'):\n  if (Path(stage)/name).is_file():shutil.copy2(Path(stage)/name,work/name)\n if (Path(stage)/'docs').is_dir():shutil.copytree(Path(stage)/'docs',work/'docs')\n (work/'安装说明.md').write_text(guide,encoding='utf-8')\n (work/'安装说明.txt').write_text(guide,encoding='utf-8')\n versions={}\n for name in ['PySide6-Essentials','shiboken6','psutil','PyYAML','websocket-client','PyInstaller','cryptography','keyring','cffi','jaraco.classes','jaraco.context','jaraco.functools','more-itertools','pycparser']:\n  dist=importlib.metadata.distribution(name);versions[name]=dist.version\n  for file in dist.files or []:\n   if file.parts and '.dist-info' in file.parts[0] and any(t in file.name.lower() for t in ('license','copying','notice')):\n    target=work/'LICENSES'/name/Path(*file.parts[1:]);target.parent.mkdir(parents=True,exist_ok=True)\n    shutil.copy2(dist.locate_file(file),target)\n py_license=Path(sys.base_prefix)/'LICENSE'\n if py_license.is_file():shutil.copy2(py_license,work/'LICENSES/Python-LICENSE.txt')\n deps.update(pythonBundled=True,qtBundled=True,systemPythonRequired=False,buildRuntimeVersions=versions)\n relative=(source.name+'/Contents/MacOS/'+product) if sys.platform=='darwin' else product+'/'+product\n exe=work/relative\n deps['compiledPayloadAudit']=matrix['inspect_bundle'](exe,edition,version)\n qa=output/'verification';qa.mkdir(exist_ok=True)\n checks=[]\n env=dict(os.environ,PATH='/usr/bin:/bin:/usr/sbin:/sbin',QT_QPA_PLATFORM='offscreen')\n for flag in (['--ui-check','--self-test','--package-probe','--session-probe','--independent-probe','--system-probe'] if role=='client' else ['--ui-check','--self-test','--first-setup-probe','--ipv6-setup-probe','--system-probe']):\n  report=qa/(product+'-'+edition+'-'+flag[2:]+'.json')\n  run=subprocess.run([str(exe),flag,str(report)],capture_output=True,timeout=45,env=env)\n  data=json.loads(report.read_text())\n  assert run.returncode==0 and data.get('passed') is not False,(flag,run.stderr.decode(errors='replace'))\n  if flag=='--package-probe':\n   assert data['edition']==edition and data['version']==version,data\n   assert all(data['edition_contract'].values()),data\n  if flag=='--independent-probe':assert not data.get('skipped') and all(data['checks'].values()),data\n  checks.append({'check':flag,'passed':True,'tests':data.get('tests')})\n deps['cleanDeveloperPathChecks']=checks\n (work/'DEPENDENCIES.json').write_text(json.dumps(deps,indent=2),encoding='utf-8')\n if sys.platform=='linux':\n  (work/'prepare-linux.sh').write_text(prepare_linux,encoding='utf-8');(work/'prepare-linux.sh').chmod(0o755)\n  entry=work/('启动客户端.sh' if role=='client' else '启动服务端.sh')\n  entry.write_text(start_linux,encoding='utf-8');entry.chmod(0o755)\n  for script in [entry,work/'prepare-linux.sh']:\n   subprocess.run(['/bin/bash','-n',str(script)],check=True)\n  checked=subprocess.run(['/bin/bash',str(work/'prepare-linux.sh')],capture_output=True,text=True)\n  assert checked.returncode==0,checked.stdout+checked.stderr\n else:\n  (work/'Applications').symlink_to('/Applications',target_is_directory=True)\n files=[]\n for file in sorted(work.rglob('*')):\n  if file.is_symlink():files.append({'file':file.relative_to(work).as_posix(),'symlink':os.readlink(file)})\n  elif file.is_file():files.append({'file':file.relative_to(work).as_posix(),'bytes':file.stat().st_size,'sha256':digest(file)})\n (work/'RELEASE.json').write_text(json.dumps({'version':version,'role':role,'edition':edition,'editionLabel':labels[edition],'credentialsBundled':False,'pythonInstallationRequired':False,'files':files},indent=2),encoding='utf-8')\n extension=('macOS'+deps['minimumMacOS'].split('.')[0]+'-arm64.dmg') if sys.platform=='darwin' else 'Linux-x64.tar.gz'\n archive=output/matrix['package_name'](product,version,edition,extension)\n if archive.exists():raise RuntimeError('Refusing to overwrite a published archive')\n if sys.platform=='darwin':\n  subprocess.run(['/usr/bin/hdiutil','create','-quiet','-volname',('Relay Client' if role=='client' else 'Relay Server')+' '+edition+' '+version,\n      '-srcfolder',str(work),'-format','UDZO',str(archive)],check=True,timeout=90,capture_output=True)\n  subprocess.run(['/usr/bin/hdiutil','verify','-quiet',str(archive)],check=True,timeout=45,capture_output=True)\n else:\n  with tarfile.open(archive,'x:gz') as tar:\n   for file in sorted(work.iterdir()):tar.add(file,arcname=file.name)\n  with tarfile.open(archive) as tar:\n   assert len(tar.getmembers())>20\n compatibility=None\n if sys.platform=='linux':\n  compatibility={'file':archive.name,'path':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive)}\n  from release_tools.native_installers import build_deb\n  archive=build_deb(work,output,role,edition,version,deps)\n  from crb.common.deb_archive import extract_payload,read_control\n  probe=output/(product+'-'+edition+'-deb-probe-'+str(time.time_ns()))\n  extract_payload(archive,probe,role)\n  from desktop_client.update_apply import tree_hashes\n  assert tree_hashes(probe)==dict(tree_hashes(work/product),**{'CRB-INSTALL.json':tree_hashes(probe)['CRB-INSTALL.json']}),'Debian payload differs'\n  shutil.rmtree(probe)\n  checks.append({'check':'native-debian-payload','passed':True})\n results.append({'version':version,'platform':'macOS-arm64' if sys.platform=='darwin' else 'Linux-x64',\n  'role':role,'edition':edition,'editionLabel':labels[edition],'file':archive.name,'path':str(archive),\n  'bytes':archive.stat().st_size,'sha256':digest(archive),'dependencies':deps,'bridgeSourceHashes':deps.get('bridgeSourceHashes',{}),\n  **({'legacyUpdate':compatibility} if compatibility else {})})\n # Keep the verified archive; release only its new packaging workspace.\n assert work.parent==output and work.resolve().is_relative_to(output.resolve()) and not work.is_symlink()\n shutil.rmtree(work)\nprint(json.dumps({'version':version,'packages':results}))\n"

def native(output):
    policy()
    namespace = {'stage': str(ROOT), 'version': VERSION, 'guide': (SUPPORT / 'README.zh-CN.md').read_text(encoding='utf-8'), 'prepare_linux': (SUPPORT / 'prepare-linux.sh').read_text(encoding='utf-8'), 'start_linux': (SUPPORT / 'start-linux.sh').read_text(encoding='utf-8'), 'audit_code': (SUPPORT / 'inspect_native_dependencies.py').read_text(encoding='utf-8'), 'matrix_code': (SUPPORT / 'release_matrix.py').read_text(encoding='utf-8'), 'plans': PLANS, 'labels': LABELS}
    exec(REMOTE, namespace)
    output.mkdir(parents=True, exist_ok=True)
    for item in namespace['results']:
        archive = output / item['file']
        if archive.exists():
            raise RuntimeError('Refusing to overwrite a published archive')
        shutil.copy2(item['path'], archive)
        assert sha256(archive) == item['sha256']
        if item.get('legacyUpdate'):
            legacy = item['legacyUpdate']
            shutil.copy2(legacy['path'], output / legacy['file'])
            assert sha256(output / legacy['file']) == legacy['sha256']
    name = 'mac' if sys.platform == 'darwin' else 'mint'
    (output / ('verification-' + name + '.json')).write_text(json.dumps({'version': VERSION, 'packages': namespace['results']}, indent=2), encoding='utf-8')

def collect(output):
    records = []
    for platform in ('windows', 'mint', 'mac'):
        records += json.loads((output / ('verification-' + platform + '.json')).read_text(encoding='utf-8'))['packages']
    audit = validate_release(records, VERSION)
    artifacts = [item for record in records for item in ([record, record['legacyUpdate']] if record.get('legacyUpdate') else [record])]
    files = sorted((p for p in output.iterdir() if p.name.startswith(('RelayClient-', 'RelayServer-')) and p.name.endswith(('.exe', '.deb', '.zip', '.dmg', '.tar.gz'))))
    assert {p.name for p in files} == {item['file'] for item in artifacts}, [p.name for p in files]
    for p in files:
        record = next((item for item in artifacts if item['file'] == p.name))
        assert p.stat().st_size == record['bytes'] and sha256(p) == record['sha256'], p.name
    value = {'version': VERSION, 'credentialsBundled': False, 'releasePolicy': policy(), 'audit': audit, 'files': [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in files]}
    (output / 'SHA256SUMS.json').write_text(json.dumps(value, indent=2), encoding='utf-8')
    shutil.copy2(SUPPORT / 'README.zh-CN.md', output / '安装说明.md')
    shutil.copy2(ROOT / 'release_policy.json', output / '发布规则.json')
    print(json.dumps(value))
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['local', 'collect'])
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--client-only', action='store_true')
    args = parser.parse_args()
    if args.command == 'local' and sys.platform == 'win32':
        windows(args.output, client_only=args.client_only)
    elif args.command == 'collect':
        collect(args.output)
    else:
        native(args.output)
