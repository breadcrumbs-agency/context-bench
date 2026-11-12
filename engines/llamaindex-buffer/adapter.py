"""llamaindex-buffer: llama-index-core's ChatMemoryBuffer as a context engine.

token_limit is the budget. tokenizer_fn is the harness's counter of a text (ceil(bytes / 4)). The library counts the
joined texts of the messages in the buffer, so it would see no framing bytes: the adapter pads the text of each message
with the bytes that the harness adds to it (16 of framing), and then the library's count of k messages is the
harness's count of those messages, rounded up. The system message stays outside the buffer, as in a chat engine; its
tokens go in as initial_token_count.

The library's get() starts from the whole history and drops one message at a time, counting all that remain at each
step: quadratic in the length of the chat. The adapter therefore hands over only the newest messages that can matter:
the fewest whose joined text passes the limit. The tokens of the newest k messages never fall as k grows, so the
steps that the library would take over the older messages all fail, and the result is the same.
"""

import contextbench
from contextbench import meter


class _Count:
    """What the harness's counter gives back for a text: the library only takes its len()."""

    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


def count(text):
    return _Count(meter.text_tokens(text))


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.require()
        from llama_index.core.base.llms.types import ChatMessage, MessageRole
        from llama_index.core.memory import ChatMemoryBuffer
        self.ChatMessage, self.MessageRole, self.Buffer = ChatMessage, MessageRole, ChatMemoryBuffer
        self.orig = []  # the message dict for each serial number
        self.made = {}  # id(message dict) -> (message dict, library message, bytes that the harness counts for it)

    def _lc(self, m):
        hit = self.made.get(id(m))
        if hit is None or hit[0] is not m:
            role = {"system": self.MessageRole.SYSTEM, "user": self.MessageRole.USER,
                    "assistant": self.MessageRole.ASSISTANT, "tool": self.MessageRole.TOOL}.get(m.get("role"))
            if role is None:
                raise ValueError("llamaindex-buffer: no message role for %r" % (m.get("role"),))
            text, size = meter.message_text(m), meter.message_bytes(m)
            padded = text + " " * (size - len(text.encode("utf-8")))  # the library then counts what the harness counts
            msg = self.ChatMessage(role=role, content=padded, additional_kwargs={"serial": len(self.orig)})
            self.orig.append(m)
            hit = self.made[id(m)] = (m, msg, size)
        return hit

    def select(self, request_messages, budget):
        head = request_messages[:1] if request_messages and request_messages[0].get("role") == "system" else []
        rest = request_messages[len(head):]
        initial = sum((self._lc(m)[2] + 3) // 4 for m in head)
        joined, start = -1, len(rest)  # bytes of the joined text of rest[start:]
        while start > 0:
            start -= 1
            joined += self._lc(rest[start])[2] + 1
            if (joined + 3) // 4 + initial > budget:
                break
        history = [self._lc(m)[1] for m in rest[start:]]
        memory = self.Buffer.from_defaults(chat_history=history, token_limit=budget, tokenizer_fn=count)
        kept = memory.get(initial_token_count=initial)
        return head + [self.orig[x.additional_kwargs["serial"]] for x in kept]
