"""drop: the window that cuts in steps, like ACE.

Past the budget it keeps the newest messages that fit FOLD_TO of it (from a user message), then only appends until
the budget again. A stable prefix between cuts, no layer.
"""

import contextbench
from contextbench.meter import request_bytes

FOLD_TO = 0.6  # the share of the budget that a cut leaves: ace.FOLD_TO in the runs of Nov 9 2026 (ACE has 0.9 now)


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.limit, self.start, self.folds = 4 * budget, 1, 0

    def select(self, request_messages, budget):
        msgs = request_messages
        out = msgs[:1] + msgs[self.start:]
        if request_bytes(out) <= self.limit:
            return out
        used, start, room = request_bytes(msgs[:1] + msgs[-1:]), len(msgs) - 1, FOLD_TO * self.limit
        while start > 1 and used + request_bytes([msgs[start - 1]]) <= room:
            start -= 1
            used += request_bytes([msgs[start]])
        while start < len(msgs) - 1 and msgs[start]["role"] != "user":
            start += 1
        self.start, self.folds = start, self.folds + 1
        return msgs[:1] + msgs[start:]

    def state(self):
        return {"start": self.start, "folds": self.folds}

    def restore(self, state):
        self.start, self.folds = state["start"], state["folds"]

    def stats(self):
        return {"folds": self.folds}
