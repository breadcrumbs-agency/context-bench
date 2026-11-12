"""window: the newest messages that fit the budget (the system message stays), cut again at each request."""

import contextbench
from contextbench.meter import request_bytes


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.limit = 4 * budget  # bytes: the harness counts a token as 4 bytes

    def select(self, request_messages, budget):
        msgs = request_messages
        used, keep = request_bytes(msgs[:1]), []
        for m in reversed(msgs[1:]):
            used += request_bytes([m])
            if used > self.limit and keep:
                break
            keep.append(m)
        return msgs[:1] + keep[::-1]
