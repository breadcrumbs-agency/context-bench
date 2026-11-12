"""The meter: one token counter, one cache rule, one cost rule and one scoring rule for every engine.

The harness counts. An engine never reports a size, a cost or a score, and it never sees a gold turn, an answer
or one of the texts below that score it.

Tokens. The counter is the one of ace_bench (the private bench that came before this one): the size of a request
is the sum, over its messages, of the UTF-8 bytes of the text, the tool calls and the thinking that go back with
the message, plus 16 bytes of framing; a token is 4 bytes. It is not the tokenizer of a model. It is the same for
every engine, and it needs no download. PROTOCOL in contextbench/__init__.py names it.

Not in this meter: route@k and reach of ace_bench. They need the crumb refs of one engine (breadcrumbs ACE), so
they cannot be one rule for all engines.
"""

import hashlib
import json
import os
import re

RULE = ("tokens(request) = sum over its messages of (UTF-8 bytes of text, tool calls and thinking, + 16) / 4; "
        "cache_reuse = share of request tokens in the leading messages that the request before also sent; "
        "cost units = 0.1 per cached token + 1.25 per other token; dollars use the dated price card")
CACHE_READ, CACHE_WRITE = 0.1, 1.25  # input price of a cached and of a newly written token (Anthropic, 5 min)
FRAME = 16  # bytes of framing for each message
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICE_DIR = os.path.join(ROOT, "prices")

# The stop list of breadcrumbs.index.STOP at 5be1e74, copied so that the harness has no dependency.
STOP = frozenset("""a an the and or of to in on at for with by from is are was were be been being do does did
done have has had i you he she it we they me my your his her its our their what when where which who whom
why how that this these those there here as about into over after before during than then so if not no
yes can could would should will shall may might must just also very much many any some more most other""".split())


# -- messages ---------------------------------------------------------------------------------------------
def _content(m):
    c = m.get("content")
    if isinstance(c, list):
        c = "\n".join(p["text"] if isinstance(p, dict) and isinstance(p.get("text"), str)
                      else "[%s]" % (p.get("type", "part") if isinstance(p, dict) else "part") for p in c)
    return c if isinstance(c, str) else ("" if c is None else json.dumps(c, ensure_ascii=False))


def _calls_of(m):
    fs = [(c.get("function") or {}) if isinstance(c, dict) else {} for c in m.get("tool_calls") or []]
    return [(f.get("name", "?"), f.get("arguments", "")) for f in fs]


def message_text(m):
    """The text of a message as the model sees it: its content, then one line per tool call."""
    text = _content(m)
    for name, args in _calls_of(m):
        text += ("\n" if text else "") + "\u2192 %s %s" % (name, args)
    return text


def reasoning_text(m):
    """The thinking that goes back to the model with an assistant message."""
    d = m.get("reasoning_details")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = None
    if isinstance(d, list) and d:
        return "".join(str(x.get("thinking") or x.get("text") or "") for x in d if isinstance(x, dict))
    r = m.get("reasoning") or m.get("reasoning_content")
    return r if isinstance(r, str) else ""


def message_bytes(m):
    return len(message_text(m).encode("utf-8")) + len(reasoning_text(m).encode("utf-8")) + FRAME


def request_bytes(messages):
    """The size of a request in bytes."""
    return sum(message_bytes(m) for m in messages)


def tokens(messages):
    """The size of a request in tokens (a float: bytes / 4)."""
    return request_bytes(messages) / 4


def text_tokens(text):
    """Tokens of a bare text, for a library that counts a string (no framing): ceil(bytes / 4)."""
    return (len(text.encode("utf-8")) + 3) // 4


class Sizer:
    """request_bytes with a memo for the messages of one chat: a message that comes back is sized once."""

    def __init__(self):
        self.memo = {}

    def message(self, m):
        hit = self.memo.get(id(m))
        if hit is None or hit[0] is not m:  # the memo keeps m alive, so an id cannot be reused while it is here
            hit = self.memo[id(m)] = (m, message_bytes(m))
        return hit[1]

    def request(self, messages):
        return sum(self.message(m) for m in messages)


def tail_kept(out, newest):
    """True when the request ends with the newest message, also with a layer added in front of it."""
    return bool(out) and message_text(out[-1]).endswith(message_text(newest))


# -- cost -------------------------------------------------------------------------------------------------
def cost_units(size, hit):
    """ace_bench's cost of one request in token units: the cached part at CACHE_READ, the rest at CACHE_WRITE."""
    return CACHE_READ * hit + CACHE_WRITE * (size - hit)


def load_card(path=None):
    """The price card at path, or the newest card in prices/. Its sha256 goes into each record."""
    if path is None:
        names = sorted(n for n in os.listdir(PRICE_DIR) if n.endswith(".json"))
        path = os.path.join(PRICE_DIR, names[-1])
    with open(path, "rb") as f:
        raw = f.read()
    card = json.loads(raw.decode("utf-8"))
    card["sha256"] = hashlib.sha256(raw).hexdigest()
    return card


def tier_of(model, size):
    """The price tier of a request of size tokens: the first tier whose up_to holds the whole prompt."""
    for tier in model["tiers"]:
        if tier["up_to"] is None or size <= tier["up_to"]:
            return tier
    raise ValueError("no price tier for %s tokens" % size)


def dollars(card, name, size, hit):
    """Dollars of input for one request: hit tokens at the cache-read price, the others at the 5-minute
    cache-write price. A tiered model (Claude Haiku 5.5) prices all tokens of the request at the tier of its size."""
    tier = tier_of(card["models"][name], size)
    return (hit * tier["cache_read"] + (size - hit) * tier["cache_write"]) / 1e6


class Replay:
    """The meter for the requests of one chat, one request at a time, in order."""

    def __init__(self, sizer, card, budget):
        self.sizer, self.card, self.budget = sizer, card, budget
        self.prev = []
        self.requests = self.over = self.dropped = 0
        self.tokens = self.cached = self.units = self.excess = self.top = 0.0
        self.usd = dict.fromkeys(card["models"], 0.0)

    def add(self, out, newest):
        """Count one request: out is what the engine sent, newest the last message of the request it got."""
        n, top = 0, min(len(out), len(self.prev))
        while n < top and (out[n] is self.prev[n] or out[n] == self.prev[n]):
            n += 1  # the leading messages that the request before also sent: the prefix a provider caches
        size, hit = self.sizer.request(out) / 4, self.sizer.request(out[:n]) / 4
        self.requests += 1
        self.tokens += size
        self.cached += hit
        self.units += cost_units(size, hit)
        for name in self.usd:
            self.usd[name] += dollars(self.card, name, size, hit)
        self.top = max(self.top, size)
        if self.budget is not None and size > self.budget:
            self.over += 1
            self.excess += size - self.budget
        if not tail_kept(out, newest):
            self.dropped += 1
        self.prev = out


# -- scoring ----------------------------------------------------------------------------------------------
def words(s):
    return [w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if w not in STOP]


def flat(s):
    return " ".join(str(s).split())


def context_text(out):
    """The context of a question: every message of the request but the last, which is the question."""
    return flat("\n".join(message_text(m) for m in out[:-1]))


def needle(content):
    """The text of a gold turn: its message without the '[date] ' stamp that opens a session."""
    return flat(content.split("] ", 1)[-1] if content.startswith("[") else content)


def exact_share(ctx, messages, gold):
    """The share of gold turns (message indexes) whose full text is in the context."""
    found = {i: needle(messages[i]["content"]) in ctx for i in gold}
    return sum(found.values()) / len(gold)


def words_share(ctx, question, answer):
    """The share of the words of the gold answer that the context holds (the question's own words do not count).
    None when there is no answer to score."""
    if answer is None:
        return None
    asked = set(words(question))
    ans = [w for w in dict.fromkeys(words(answer)) if w not in asked]
    if not ans:
        return None
    have = set(words(ctx))
    return sum(w in have for w in ans) / len(ans)
