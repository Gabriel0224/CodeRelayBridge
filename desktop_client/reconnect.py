"""Five connection attempts per outage, separated by five seconds locally."""
import threading
import time
ATTEMPT_LIMIT = 5
RETRY_INTERVAL = 5
EXHAUSTED_EXIT_CODE = 75

class RetryDeferred(ConnectionError):
    """A local cooldown, not another network attempt or delivery failure."""

class ConnectionRetry:

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.channels = {}

    def begin(self, channel):
        with self.lock:
            state = self.channels.setdefault(channel, {'attempts': 0, 'next': 0, 'failed': False})
            if state['attempts'] >= ATTEMPT_LIMIT or self.clock() < state['next']:
                return False
            state['attempts'] += 1
            return True

    def success(self, channel):
        with self.lock:
            self.channels[channel] = {'attempts': 0, 'next': 0, 'failed': False}

    def failure(self, channel):
        with self.lock:
            state = self.channels[channel]
            state.update(failed=True, next=self.clock() + RETRY_INTERVAL)

    @property
    def exhausted(self):
        with self.lock:
            return any((state['failed'] and state['attempts'] >= ATTEMPT_LIMIT for state in self.channels.values()))

    def snapshot(self):
        with self.lock:
            active = [(key, state) for key, state in self.channels.items() if state['failed']]
            channel, state = max(active, key=lambda item: item[1]['attempts']) if active else ('', {'attempts': 0, 'next': 0})
            return {'retry_attempts': state['attempts'], 'retry_limit': ATTEMPT_LIMIT, 'retry_interval': RETRY_INTERVAL, 'retry_channel': channel, 'retry_exhausted': self.exhausted}
