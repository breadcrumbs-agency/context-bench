"""langchain-trim: langchain-core's trim_messages (strategy 'last') as a context engine.

The library sees one message object for each chat message: the type comes from the role, the text is the harness's
view of the message (content and tool calls). The token counter is the harness's: it sums the size of the original
messages that the library hands it. The adapter returns the original message dicts, never the library's copies.
"""

import contextbench
from contextbench import meter


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.require()
        from langchain_core import messages as lc
        self.lc = lc
        self.orig = []  # the message dict for each serial number (the id of the library's message)
        self.made = {}  # id(message dict) -> (message dict, library message)
        self.sizer = meter.Sizer()

    def _lc(self, m):
        hit = self.made.get(id(m))
        if hit is None or hit[0] is not m:
            serial, text, role = str(len(self.orig)), meter.message_text(m), m.get("role")
            if role == "system":
                msg = self.lc.SystemMessage(content=text, id=serial)
            elif role == "user":
                msg = self.lc.HumanMessage(content=text, id=serial)
            elif role == "assistant":
                msg = self.lc.AIMessage(content=text, id=serial)
            elif role == "tool":
                msg = self.lc.ToolMessage(content=text, tool_call_id=str(m.get("tool_call_id") or ""), id=serial)
            else:
                raise ValueError("langchain-trim: no message type for the role %r" % (role,))
            self.orig.append(m)
            hit = self.made[id(m)] = (m, msg)
        return hit[1]

    def _count(self, messages):
        return sum(self.sizer.message(self.orig[int(x.id)]) for x in messages) / 4

    def select(self, request_messages, budget):
        kept = self.lc.trim_messages([self._lc(m) for m in request_messages], max_tokens=budget,
                                     token_counter=self._count, strategy="last", include_system=True,
                                     start_on="human")
        return [self.orig[int(x.id)] for x in kept]
