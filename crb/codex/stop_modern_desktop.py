"""Stop only this user's exact Desktop and descendants, including GNOME scopes.

GNOME moves an Electron App into app-*.scope outside its launcher service's
cgroup. Killing only the wrapper leaves its backend alive. Process identities
include uid/exe/data-home/start-time; never kill by name or an unverified PID.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import time

def identity(process):
    fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
    return fields[19]

def matching_home(process, home):
    env = dict((x.split('=', 1) for x in (process / 'environ').read_bytes().decode().split('\x00') if '=' in x))
    actual = env.get('DSH_HOME', '').strip() or str(Path(env.get('HOME', '/')) / '.dsh')
    return Path(actual).expanduser().resolve() == home

def collect(home, app, payload, proc=Path('/proc'), uid=None):
    home, app, payload = (Path(home).resolve(), Path(app).resolve(), Path(payload).resolve())
    uid = os.getuid() if uid is None else uid
    candidates, nodes, parents, stamps, cgroups, launchers = ({}, {}, {}, {}, {}, set())
    for p in proc.iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid != uid:
                continue
            pid = int(p.name)
            fields = (p / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[pid] = int(fields[1])
            stamps[pid] = fields[19]
            args = (p / 'cmdline').read_bytes().decode().split('\x00')
            exe = (p / 'exe').resolve()
            if exe == app and (not re.search('(?:^|\\s)--type=', ' '.join(args))):
                candidates[pid] = stamps[pid]
            if str(app.parent / 'dsh-desktop-launcher') in args and matching_home(p, home):
                launchers.add(pid)
            if exe == (payload / 'node/bin/node').resolve() and len(args) > 2 and (args[1:3] == [str(payload / 'runtime/node_modules/@deepseek-ai/dsh/lib/bin.js'), 'web']) and matching_home(p, home):
                nodes[pid] = stamps[pid]
                cgroups[pid] = (p / 'cgroup').read_text()
        except (OSError, ValueError, UnicodeError, IndexError):
            continue
    gui, children = ({}, {})
    for pid, stamp in nodes.items():
        ancestor = parents.get(pid)
        for _ in range(32):
            if ancestor in candidates:
                gui[ancestor] = candidates[ancestor]
                children[pid] = stamp
                break
            next_parent = parents.get(ancestor)
            if not next_parent or next_parent == ancestor:
                break
            ancestor = next_parent
        if pid not in children and re.search('/app-dsh\\\\x2ddesktop-\\d+\\.scope(?:\\n|$)', cgroups.get(pid, '')):
            children[pid] = stamp
    for pid, stamp in candidates.items():
        ancestor = parents.get(pid)
        for _ in range(32):
            if ancestor in launchers:
                gui[pid] = stamp
                break
            ancestor = parents.get(ancestor)
            if not ancestor:
                break
    return (gui, children)

def alive(pid, stamp, proc=Path('/proc')):
    try:
        p = proc / str(pid)
        fields = (p / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == stamp
    except (OSError, ValueError, IndexError):
        return False

def stop(home, app, payload, proc=Path('/proc')):
    gui, children = collect(home, app, payload, proc)
    for pid, stamp in gui.items():
        if alive(pid, stamp, proc):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any((alive(pid, stamp, proc) for pid, stamp in {**gui, **children}.items())):
        time.sleep(0.1)
    for pid, stamp in children.items():
        if alive(pid, stamp, proc):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any((alive(pid, stamp, proc) for pid, stamp in {**gui, **children}.items())):
        time.sleep(0.1)
    assert not any((alive(pid, stamp, proc) for pid, stamp in {**gui, **children}.items())), 'Exact Desktop did not shut down; no new engine should start'
    return {'desktop_processes_stopped': len(gui), 'native_descendants_stopped': len(children)}
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--app', required=True)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    if args.inspect:
        gui, nodes = collect(args.home, args.app, args.payload)
        print(json.dumps({'desktop_pids': list(gui), 'descendant_pids': list(nodes)}))
    else:
        print(json.dumps(stop(args.home, args.app, args.payload)))
