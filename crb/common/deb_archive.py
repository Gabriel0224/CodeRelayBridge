"""Bounded Debian payload reader. Never executes package scripts while verifying."""
import email.parser
import io
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shutil
import tarfile
MAX_ARCHIVE = 2 * 1024 ** 3
MAX_EXPANDED = 4 * 1024 ** 3

def members(path):
    result = {}
    with Path(path).open('rb') as source:
        if source.read(8) != b'!<arch>\n':
            raise ValueError('Not a Debian ar archive')
        total = Path(path).stat().st_size
        while source.tell() < total:
            header = source.read(60)
            if len(header) != 60 or header[58:] != b'`\n':
                raise ValueError('Invalid Debian archive header')
            name = header[:16].decode('ascii').strip().removesuffix('/')
            size = int(header[48:58].decode('ascii').strip())
            if name in result or '/' in name or size < 0 or (size > MAX_ARCHIVE) or (source.tell() + size > total):
                raise ValueError('Invalid Debian archive member')
            result[name] = (source.tell(), size)
            source.seek(size + size % 2, 1)
    if not {'debian-binary'} <= result.keys() or len(result) != 3:
        raise ValueError('Unexpected Debian archive layout')
    with Path(path).open('rb') as source:
        offset, size = result['debian-binary']
        source.seek(offset)
        if size != 4 or source.read(size) != b'2.0\n':
            raise ValueError('Unsupported Debian archive version')
    return result

class Section(io.RawIOBase):

    def __init__(self, source, offset, length):
        self.source, self.offset, self.length, self.position = (source, offset, length, 0)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        if not 0 <= target <= self.length:
            raise ValueError('Debian section seek outside bounds')
        self.position = target
        return target

    def read(self, size=-1):
        remaining = self.length - self.position
        size = remaining if size < 0 else min(size, remaining)
        self.source.seek(self.offset + self.position)
        data = self.source.read(size)
        self.position += len(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

@contextmanager
def section(path, prefix, limit=MAX_ARCHIVE):
    rows = members(path)
    names = [name for name in rows if name.startswith(prefix + '.tar.')]
    if len(names) != 1:
        raise ValueError('Missing unique Debian payload section')
    offset, size = rows[names[0]]
    if size > limit:
        raise ValueError('Oversized Debian section')
    with Path(path).open('rb') as handle:
        yield Section(handle, offset, size)

def read_control(path):
    with section(path, 'control', 1024 * 1024) as data, tarfile.open(fileobj=data, mode='r:*') as archive:
        matches = []
        total = 0
        for number, row in enumerate(archive):
            total += row.size
            if number >= 256 or total > 1024 * 1024:
                raise ValueError('Oversized Debian control content')
            if row.name.removeprefix('./') == 'control':
                matches.append(row)
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 65536:
            raise ValueError('Invalid Debian control file')
        raw = archive.extractfile(matches[0]).read().decode('utf-8')
    return dict(email.parser.Parser().parsestr(raw).items())

def extract_payload(path, destination, role):
    if role not in {'client', 'server'}:
        raise ValueError('Unknown package role')
    control = read_control(path)
    if control.get('Package') != 'coderelaybridge-' + role or control.get('Architecture') != 'amd64' or control.get('X-CRB-Role') != role:
        raise ValueError('Wrong Debian package identity')
    destination = Path(destination)
    destination.mkdir(mode=448, exist_ok=False)
    prefix = 'opt/coderelaybridge/' + role + '/'
    seen, selected = (set(), [])
    with section(path, 'data') as data, tarfile.open(fileobj=data, mode='r:*') as archive:
        total = 0
        for number, row in enumerate(archive):
            total += row.size
            if number >= 20000 or total > MAX_EXPANDED:
                raise ValueError('Debian payload is too large')
            name = row.name.removeprefix('./')
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or ('\x00' in name) or (name in seen) or row.isdev() or row.isfifo() or row.islnk():
                raise ValueError('Unsafe Debian member')
            seen.add(name)
            if not name.startswith(prefix):
                if not (row.isdir() or name.startswith('usr/share/doc/coderelaybridge-' + role + '/') or name == 'usr/share/applications/coderelaybridge-' + role + '.desktop' or (name == 'usr/bin/coderelaybridge-' + role)):
                    raise ValueError('Unexpected Debian install location')
                continue
            relative = name[len(prefix):]
            if not relative:
                continue
            row.name = relative
            target = destination / relative
            if row.issym():
                if PurePosixPath(row.linkname).is_absolute() or not (target.parent / row.linkname).resolve().is_relative_to(destination.resolve()):
                    raise ValueError('External Debian application link')
            selected.append(row)
        archive.extractall(destination, members=selected, filter='data')
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    if not (destination / product).is_file():
        raise ValueError('Debian package has no native application')
    return destination
