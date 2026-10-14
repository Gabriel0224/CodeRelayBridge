"""Remap only compiler source-file annotations, preserving native instructions.

Some upstream helpers embed file!()/panic source paths in constant data even
after debug stripping. This release-only transformation is length-preserving,
rejects non-source home references, and checks the machine-code section hash.
It does not change runtime configuration paths, account data or antivirus rules.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
HOME = re.compile(b'(?:/home/|/Users/|[A-Z]:[/\\\\]+Users[/\\\\]+)([A-Za-z0-9_.-]+)')
TAIL = re.compile(b'[/\\\\][A-Za-z0-9_./\\\\:-]+\\.(?:rs|c|cc|cpp|h|swift)')

def text_range(data):
    if data[:2] == b'MZ':
        pe = struct.unpack_from('<I', data, 60)[0]
        count = struct.unpack_from('<H', data, pe + 6)[0]
        optional = struct.unpack_from('<H', data, pe + 20)[0]
        for index in range(count):
            offset = pe + 24 + optional + index * 40
            name = data[offset:offset + 8].rstrip(b'\x00')
            size, position = struct.unpack_from('<II', data, offset + 16)
            if name == b'.text':
                return (position, size)
    elif data[:4] == b'\x7fELF' and data[4:6] == b'\x02\x01':
        table = struct.unpack_from('<Q', data, 40)[0]
        width, count, names = struct.unpack_from('<HHH', data, 58)
        strings = struct.unpack_from('<IIQQQQIIQQ', data, table + names * width)
        names_data = data[strings[4]:strings[4] + strings[5]]
        for index in range(count):
            row = struct.unpack_from('<IIQQQQIIQQ', data, table + index * width)
            name = names_data[row[0]:].split(b'\x00', 1)[0]
            if name == b'.text':
                return (row[4], row[5])
    elif data[:4] == b'\xcf\xfa\xed\xfe':
        count = struct.unpack_from('<I', data, 16)[0]
        offset = 32
        for _ in range(count):
            command, width = struct.unpack_from('<II', data, offset)
            if command == 25:
                sections = struct.unpack_from('<I', data, offset + 64)[0]
                for index in range(sections):
                    start = offset + 72 + index * 80
                    if data[start:start + 16].rstrip(b'\x00') == b'__text':
                        size = struct.unpack_from('<Q', data, start + 40)[0]
                        position = struct.unpack_from('<I', data, start + 48)[0]
                        return (position, size)
            offset += width
    raise RuntimeError('Unsupported native executable layout')

def sanitize(source, target):
    source, target = (Path(source), Path(target))
    original = source.read_bytes()
    position, size = text_range(original)
    original_code = hashlib.sha256(original[position:position + size]).hexdigest()
    data = bytearray(original)
    changes = 0
    for match in HOME.finditer(original):
        source_file = TAIL.match(original, match.end())
        if source_file is None:
            raise RuntimeError('A home path is not a recognized compiler source-file annotation')
        if position <= match.start() < position + size:
            raise RuntimeError('Source path overlaps native instruction section; refusing transformation')
        length = match.end() - match.start()
        replacement = (b'/build/source' + b'x' * length)[:length]
        data[match.start():match.end()] = replacement
        changes += 1
    repaired = False
    if data[:2] == b'MZ':
        pe = struct.unpack_from('<I', data, 60)[0]
        offset, symbols = struct.unpack_from('<II', data, pe + 12)
        if symbols and offset + symbols * 18 > len(data):
            struct.pack_into('<II', data, pe + 12, 0, 0)
            repaired = True
    final_code = hashlib.sha256(data[position:position + size]).hexdigest()
    if final_code != original_code or HOME.search(data):
        raise RuntimeError('Native code changed or a personal home reference remains')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError('Refusing to overwrite an existing native candidate')
    target.write_bytes(data)
    shutil.copymode(source, target)
    result = {'sourceSha256': hashlib.sha256(original).hexdigest(), 'sha256': hashlib.sha256(data).hexdigest(), 'sourcePathAnnotationsRemapped': changes, 'nativeInstructionsUnchanged': True, 'nativeInstructionSha256': final_code, 'invalidCoffDebugPointerCleared': repaired, 'homeReferencesRemaining': 0}
    return result
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print(json.dumps(sanitize(args.source, args.target)))
