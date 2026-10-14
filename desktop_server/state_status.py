"""Non-critical runtime status; configuration commits remain strict."""
import logging
from pathlib import Path
import time
from desktop_client.connections import atomic_json

class ServerStateWriter:

    def __init__(self, path, logger=None):
        self.path = Path(path)
        self.logger = logger if logger is not None else logging.getLogger('relay-server-state')
        self.pending = False
        self.failures = 0
        self.last_warning = None

    def publish(self, state):
        try:
            atomic_json(self.path, state)
        except OSError as exc:
            self.pending = True
            self.failures += 1
            now = time.monotonic()
            if self.last_warning is None or now - self.last_warning >= 60:
                self.logger.warning('服务端运行状态暂未写入（%s，winerror=%s）；服务继续运行，下轮重试', type(exc).__name__, getattr(exc, 'winerror', None))
                self.last_warning = now
            return False
        if self.failures:
            self.logger.info('服务端运行状态写入已恢复；跳过 %d 次状态写入', self.failures)
        self.pending = False
        self.failures = 0
        self.last_warning = None
        return True
