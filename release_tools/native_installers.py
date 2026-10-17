"""Build native Setup.exe and Debian packages from already verified payloads."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

def installer_name(product, version, edition, platform):
    ending = 'Windows-x64-Setup.exe' if platform == 'win32' else 'Linux-x64.deb'
    return f'{product}-{version}-{edition.capitalize()}-{ending}'

def windows_uninstaller(directory, compiler=None):
    """Build a small native launcher kept in the update payload beside the App."""
    target = Path(directory) / 'Uninstall.exe'
    if target.exists():
        raise FileExistsError('Refusing to replace an existing uninstall launcher')
    compiler = compiler or os.environ.get('CRB_CC') or shutil.which('gcc')
    if not compiler:
        raise RuntimeError('A MinGW-w64 x64 C compiler is required for the uninstall launcher')
    machine = subprocess.run([str(compiler), '-dumpmachine'], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    if machine != 'x86_64-w64-mingw32':
        raise RuntimeError('The uninstall launcher requires the native Windows x64 target')
    subprocess.run([str(compiler), '-Os', '-s', '-mwindows', '-nostdlib', '-fno-stack-protector', '-fno-ident', '-Werror', '-Wl,--entry=entry,--no-insert-timestamp,--dynamicbase,--nxcompat,--high-entropy-va', str(Path(__file__).with_name('windows_uninstall.c')), '-o', str(target), '-lkernel32', '-luser32'], check=True, capture_output=True, timeout=60)
    import pefile
    binary = pefile.PE(str(target))
    dependencies = {entry.dll.decode('ascii').lower() for entry in binary.DIRECTORY_ENTRY_IMPORT}
    valid = binary.FILE_HEADER.Machine == 34404 and binary.OPTIONAL_HEADER.Subsystem == 2 and (dependencies <= {'kernel32.dll', 'user32.dll'})
    binary.close()
    if not valid:
        raise RuntimeError('The uninstall launcher is not a standalone native GUI executable')
    return target

def build_windows(payload, output, role, edition, version, compiler=None):
    payload, output = (Path(payload).resolve(), Path(output).resolve())
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    if role not in {'client', 'server'} or not (payload / product / (product + '.exe')).is_file():
        raise ValueError('A complete verified native payload is required')
    if not (payload / product / 'Uninstall.exe').is_file():
        raise ValueError('The native uninstall launcher must be present in the verified update payload')
    compiler = compiler or os.environ.get('CRB_ISCC') or shutil.which('ISCC')
    if not compiler:
        raise RuntimeError('Install Inno Setup 7 and set CRB_ISCC to its ISCC.exe')
    target = output / installer_name(product, version, edition, 'win32')
    if target.exists():
        raise FileExistsError('Refusing to overwrite an existing installer')
    script = Path(__file__).with_name('windows-installer.iss')
    command = [str(compiler), '/Qp', '/DSource=' + str(payload), '/DProduct=' + product, '/DRole=' + role, '/DEdition=' + edition, '/DVersion=' + version, '/DOutput=' + str(output), '/DOutputName=' + target.stem, '/DBackupReserve=' + str(max(256 * 1024 ** 2, 2 * sum((p.stat().st_size for p in (payload / product).rglob('*') if p.is_file())))), str(script)]
    compiled = subprocess.run(command, capture_output=True, timeout=300)
    if compiled.returncode:
        raise RuntimeError('Inno Setup compilation failed: ' + (compiled.stdout + compiled.stderr).decode('utf-8', errors='replace')[-5000:])
    if not target.is_file():
        raise RuntimeError('Native installer compiler did not produce its output')
    return target

def deb_control(role, edition, version, minimum_glibc):
    product = 'Client' if role == 'client' else 'Server'
    return '\n'.join(['Package: coderelaybridge-' + role, 'Version: ' + version + '-1', 'Architecture: amd64', 'Section: utils', 'Priority: optional', 'Maintainer: Gabriel <gabriel940368@gmail.com>', 'Depends: libc6 (>= ' + minimum_glibc + '), libstdc++6, libgcc-s1, libgl1, libegl1, libdbus-1-3, libxkbcommon0, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xinerama0, libxcb-xkb1, libx11-xcb1, libxkbcommon-x11-0, pkexec | policykit-1', 'X-CRB-Role: ' + role, 'X-CRB-Edition: ' + edition, 'Description: CodeRelayBridge ' + product + ' desktop bridge', ' Self-contained desktop application. Model applications remain local.', ' User pairing, configuration and history are not removed on uninstall.', ''])

def deb_guard(role):
    return '#!/bin/sh\nset -eu\nrole=ROLE\nbase=/opt/coderelaybridge/$role\nif [ "$role" = client ]; then product=RelayClient; profile=CodexRelayClient; else product=RelayServer; profile=CodexRelayServer; fi\nfor directory in /opt /opt/coderelaybridge "$base"; do\n if [ -L "$directory" ]; then echo \'CRB: installation directory is a symbolic link; refusing to modify it.\' >&2; exit 1; fi\ndone\nif [ -d "$base" ]; then\n if ! dpkg-query -S "$base/CRB-INSTALL.json" >/dev/null 2>&1; then\n  echo \'CRB: an unmanaged application already occupies the install directory.\' >&2; exit 1\n fi\nfi\nfor entry in /proc/[0-9]*/exe; do\n executable=$(readlink "$entry" 2>/dev/null || true)\n case "$executable" in\n  "$base"/*|*/"$profile"/app/"$product") echo \'CRB: close this Relay application and stop its Relay service before installing, upgrading or removing it. Original model Apps may stay open.\' >&2; exit 1;;\n esac\ndone\nexit 0\n'.replace('ROLE', role)

def build_deb(payload, output, role, edition, version, dependencies):
    payload, output = (Path(payload).resolve(), Path(output).resolve())
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    if role not in {'client', 'server'} or not (payload / product / product).is_file():
        raise ValueError('A complete native Linux payload is required')
    target = output / installer_name(product, version, edition, 'linux')
    if target.exists():
        raise FileExistsError('Refusing to overwrite an existing Debian package')
    tree = payload.parent / (payload.name + '-deb')
    tree.mkdir(exist_ok=False)
    app = tree / 'opt/coderelaybridge' / role
    shutil.copytree(payload / product, app, symlinks=True)
    (app / 'CRB-INSTALL.json').write_text(json.dumps({'schema': 1, 'manager': 'deb', 'role': role, 'edition': edition, 'version': version}), encoding='utf-8')
    documentation = tree / 'usr/share/doc' / ('coderelaybridge-' + role)
    documentation.mkdir(parents=True)
    for name in ('RELEASE.json', 'DEPENDENCIES.json', 'LICENSE', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md', '安装说明.md', '安装说明.txt'):
        if (payload / name).is_file():
            shutil.copy2(payload / name, documentation / name)
    for name in ('docs', 'LICENSES'):
        if (payload / name).is_dir():
            shutil.copytree(payload / name, documentation / name, symlinks=True)
    desktop = tree / 'usr/share/applications' / ('coderelaybridge-' + role + '.desktop')
    desktop.parent.mkdir(parents=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=CodeRelayBridge ' + role.title() + '\nName[zh_CN]=CodeRelayBridge ' + ('客户端' if role == 'client' else '服务端') + '\nExec=/opt/coderelaybridge/' + role + '/' + product + '\nTryExec=/opt/coderelaybridge/' + role + '/' + product + '\nTerminal=false\nCategories=Network;Utility;\nStartupNotify=true\n', encoding='utf-8')
    executable = tree / 'usr/bin' / ('coderelaybridge-' + role)
    executable.parent.mkdir(parents=True)
    executable.symlink_to('/opt/coderelaybridge/' + role + '/' + product)
    control = tree / 'DEBIAN'
    control.mkdir()
    (control / 'control').write_text(deb_control(role, edition, version, dependencies.get('minimumGLIBC', '2.38')), encoding='utf-8')
    for name in ('preinst', 'prerm'):
        (control / name).write_text(deb_guard(role), encoding='utf-8')
        (control / name).chmod(493)
    for entry in tree.rglob('*'):
        if entry.is_symlink():
            continue
        entry.chmod(493 if entry.is_dir() or entry.stat().st_mode & 73 else 420)
    subprocess.run(['dpkg-deb', '-Zxz', '--root-owner-group', '--build', str(tree), str(target)], check=True, capture_output=True, timeout=300)
    checked = subprocess.run(['dpkg-deb', '--field', str(target), 'Package', 'Version', 'Architecture'], check=True, capture_output=True, text=True, timeout=15)
    if 'coderelaybridge-' + role not in checked.stdout or version + '-1' not in checked.stdout or 'amd64' not in checked.stdout:
        raise RuntimeError('Debian package identity does not match its payload')
    assert tree.parent == payload.parent and tree.name == payload.name + '-deb' and (not tree.is_symlink())
    shutil.rmtree(tree)
    return target
