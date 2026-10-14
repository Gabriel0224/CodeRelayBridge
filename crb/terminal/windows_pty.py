"""Small ConPTY adapter exposing the process operations used by the relay."""
import subprocess
import time

class WindowsPtyProcess:

    def __init__(self, argv, rows, cols):
        from winpty import PtyProcess
        from winpty.enums import Backend
        self.pty = PtyProcess.spawn(subprocess.list2cmdline(argv), dimensions=(rows, cols), backend=Backend.ConPTY)
        self.pid = self.pty.pid

    def read(self):
        return self.pty.read(65536).encode('utf-8')

    def write(self, data):
        self.pty.write(data.decode('utf-8', errors='replace'))
        return len(data)

    def resize(self, rows, cols):
        self.pty.setwinsize(rows, cols)

    def poll(self):
        return None if self.pty.isalive() else self.pty.exitstatus if self.pty.exitstatus is not None else 0

    def wait(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else float('inf')
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('ConPTY', timeout)
            time.sleep(0.05)
        return self.poll()

    def terminate(self):
        self.pty.terminate(force=True)
    kill = terminate
