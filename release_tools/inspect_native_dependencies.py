"""Read-only dependency audit of an onedir/App package, without opening a UI."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

def audit(package):
    package = Path(package).resolve()
    result = {'platform': sys.platform, 'architecture': os.uname().machine, 'missing': [], 'externalNonSystemLibraries': [], 'librariesChecked': 0}
    if sys.platform == 'linux':
        paths = [package / '_internal', package / '_internal/PySide6/Qt/lib']
        env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(map(str, paths)))
        versions = set()
        externals = set()
        for file in package.rglob('*'):
            if not file.is_file() or file.is_symlink():
                continue
            with file.open('rb') as stream:
                if stream.read(4) != b'\x7fELF':
                    continue
                versions.update(re.findall(b'GLIBC_(\\d+\\.\\d+)', stream.read()))
            result['librariesChecked'] += 1
            proc = subprocess.run(['ldd', str(file)], capture_output=True, text=True, env=env)
            for line in (proc.stdout + proc.stderr).splitlines():
                if 'not found' in line:
                    result['missing'].append({'file': file.relative_to(package).as_posix(), 'detail': line.strip()})
                match = re.search('=> (/\\S+)', line)
                if match:
                    lib = Path(match[1]).resolve()
                    if not lib.is_relative_to(package):
                        externals.add(str(lib))
                        if not str(lib).startswith(('/lib/', '/lib64/', '/usr/lib/', '/usr/lib64/')):
                            result['externalNonSystemLibraries'].append(str(lib))
        result['minimumGLIBC'] = max((v.decode() for v in versions), key=lambda v: tuple(map(int, v.split('.'))))
        result['systemLibraries'] = sorted(externals)
    elif sys.platform == 'darwin':
        minimums = set()
        result['fileMinimumMacOS'] = {}
        magics = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
        for file in package.rglob('*'):
            if not file.is_file() or file.is_symlink():
                continue
            with file.open('rb') as stream:
                if stream.read(4) not in magics:
                    continue
            result['librariesChecked'] += 1
            proc = subprocess.run(['/usr/bin/otool', '-L', str(file)], capture_output=True, text=True, check=True)
            for line in proc.stdout.splitlines()[1:]:
                lib = line.strip().split(' (', 1)[0]
                if lib.startswith('/') and (not lib.startswith(('/usr/lib/', '/System/Library/'))):
                    result['externalNonSystemLibraries'].append(lib)
            loads = subprocess.run(['/usr/bin/otool', '-l', str(file)], capture_output=True, text=True, check=True).stdout
            file_minimums = set(re.findall('\\bminos (\\d+(?:\\.\\d+){1,2})', loads))
            file_minimums.update(re.findall('LC_VERSION_MIN_MACOSX\\s+cmdsize \\d+\\s+version (\\d+(?:\\.\\d+){1,2})', loads))
            minimums.update(file_minimums)
            if file_minimums:
                result['fileMinimumMacOS'][file.relative_to(package).as_posix()] = sorted(file_minimums)
        result['minimumMacOS'] = max(minimums, key=lambda v: tuple(map(int, v.split('.'))))
    result['externalNonSystemLibraries'] = sorted(set(result['externalNonSystemLibraries']))
    result['passed'] = not result['missing'] and (not result['externalNonSystemLibraries'])
    return result
if __name__ == '__main__':
    print(json.dumps(audit(sys.argv[1])))
