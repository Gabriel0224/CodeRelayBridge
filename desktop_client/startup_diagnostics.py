"""Opt-in read-only timings; no bridge, App, task or network is started."""
import time

def configure_boot_trace(argv):
    import os
    if '--startup-trace' in argv:
        index = argv.index('--startup-trace')
        from pathlib import Path
        os.environ['RELAY_STARTUP_TRACE'] = str(Path(argv[index + 1]).resolve())
        del argv[index:index + 2]

def boot_mark(phase, details=None):
    import os
    directory = os.environ.get('RELAY_STARTUP_TRACE')
    if not directory:
        return
    import json
    from pathlib import Path
    try:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        with (root / f'{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
            value = {'phase': phase, 'epoch': time.time(), 'pid': os.getpid()}
            if details is not None:
                value['details'] = details
            stream.write(json.dumps(value) + '\n')
    except OSError:
        pass

def boot_span(phase):
    from contextlib import contextmanager

    @contextmanager
    def measured():
        boot_mark(phase + '.begin')
        try:
            yield
        finally:
            boot_mark(phase + '.end')
    return measured()

def profile_process_scan():
    from contextlib import contextmanager

    @contextmanager
    def measured():
        import os
        if not os.environ.get('RELAY_STARTUP_TRACE'):
            yield
            return
        import sys
        totals, active = ({}, {})
        old = sys.getprofile()

        def profile(frame, event, argument):
            if event in {'c_call', 'c_return', 'c_exception'}:
                if 'psutil' not in str(getattr(argument, '__module__', '')):
                    return
                name = 'native.' + getattr(argument, '__name__', '')
                key = (id(frame), name)
                begin = event == 'c_call'
            elif event in {'call', 'return'}:
                if 'psutil' not in frame.f_code.co_filename or frame.f_code.co_name not in {'ppid', 'exe', 'name', 'create_time', 'cmdline', '_get_ident'}:
                    return
                name = 'python.' + frame.f_code.co_name
                key = (id(frame), name)
                begin = event == 'call'
            else:
                return
            if begin:
                active[key] = time.perf_counter()
            else:
                started = active.pop(key, None)
                if started is not None:
                    elapsed = time.perf_counter() - started
                    row = totals.setdefault(name, {'calls': 0, 'seconds': 0.0, 'maxSeconds': 0.0})
                    row['calls'] += 1
                    row['seconds'] += elapsed
                    row['maxSeconds'] = max(row['maxSeconds'], elapsed)
        sys.setprofile(profile)
        try:
            yield
        finally:
            sys.setprofile(old)
            boot_mark('process.profile', totals)
    return measured()

def probe(entered_at):
    from collections import defaultdict
    import psutil
    from unittest.mock import patch
    from contextlib import ExitStack
    from functools import wraps
    from desktop_client.backend import process_rows
    measured = defaultdict(lambda: {'calls': 0, 'seconds': 0.0, 'maximumSeconds': 0.0})

    def wrapped(name, original):

        @wraps(original)
        def call(process, *args, **kwargs):
            started = time.perf_counter()
            try:
                return original(process, *args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                row = measured[name]
                row['calls'] += 1
                row['seconds'] += elapsed
                row['maximumSeconds'] = max(row['maximumSeconds'], elapsed)
        return call
    started = time.perf_counter()
    with ExitStack() as stack:
        for name in ('ppid', 'name', 'exe', 'create_time', 'cmdline'):
            stack.enter_context(patch.object(psutil.Process, name, wrapped(name, getattr(psutil.Process, name))))
        rows = process_rows()
    elapsed = time.perf_counter() - started
    for item in measured.values():
        item['seconds'] = round(item['seconds'], 6)
        item['maximumSeconds'] = round(item['maximumSeconds'], 6)
    return {'passed': True, 'enteredAtEpoch': entered_at, 'completedAtEpoch': time.time(), 'processRowsSeconds': round(elapsed, 6), 'processCount': len(rows), 'methods': dict(measured), 'systemChanges': 0, 'networkRequests': 0}
