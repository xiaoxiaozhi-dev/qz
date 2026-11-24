"""Connection state is distinct from a host's ability to run scene operations."""


class ConnectionHealth:
    def __init__(self):
        self.state = 'offline'
        self.connected = False
        self.failures = 0
        self.refusals = 0
        self.reason = ''

    def update(self, report):
        state = report['state']
        self.reason = report.get('reason', '')
        if state in ('ready', 'busy', 'error'):
            self.state = state
            self.connected = True
            self.failures = self.refusals = 0
        elif state == 'offline':
            self.failures += 1
            self.refusals += 1
            # A listening port may briefly disappear during a bridge restart.
            if self.connected and self.refusals < 2:
                self.state = 'waiting'
            else:
                self.state = 'offline'
                self.connected = False
        else:
            self.failures += 1
            self.refusals = 0
            self.connected = self.connected or report.get('reachable', False)
            # Even repeated response timeouts do not prove the port is closed.
            self.state = 'unresponsive' if self.failures >= 3 else 'waiting'
        return self.state

    def disconnect(self):
        self.__init__()
