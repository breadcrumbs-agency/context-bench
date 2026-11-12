"""Run one engine over one source at one or more budgets, and build the run record.

The stream. For each chat of the source, and for each budget, the harness makes a new engine in an empty folder and
sends the chat request by request, in order: one request at each point where the chat calls the model, with the
whole chat so far (the system message first, the newest message last). When the chat has questions, they come only
after its last request. The harness takes the engine's state once, then asks each question as a new user message at
the end of the chat, scores the request that the engine sends as the context, and restores the state, so that each
question sees the state at the end of the chat. The engine never gets a gold turn, an answer or a score. The harness
does not cut a request: it records the largest request, the requests over the budget and the tokens over it.

The trace. A run keeps the tokens of each request of one chat, for the trace chart. The chat is the first by id whose
largest uncut request is over the largest budget of the run; when no chat is over it, the chat with the largest uncut
request (on a tie, the first by id). An uncut request is the request as the harness hands it to the engine, before
select() cuts it, so the choice does not depend on the engine: every engine of a set of runs draws the same chat.
A run with no budget (a ceiling engine) has none to be over: it takes the first chat by id.
"""

import datetime
import hashlib
import itertools
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time

from contextbench import PROTOCOL, __version__, engine, meter, sources

ROLES = ("maintainer", "donor", "other")
BUDGETS = (4096, 8192, 16384)
ROOT = meter.ROOT


def _commit():
    """The harness commit, and whether its code differs from that commit."""
    try:
        head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", "contextbench", "engines",
                                "sources", "prices"], capture_output=True, text=True, check=True).stdout.strip()
        return {"commit": head, "dirty": bool(dirty)}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _load():
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return None


def mean(xs):
    xs = list(xs)
    return round(statistics.fmean(xs), 4) if xs else None


def _pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return round(v[int(p * (len(v) - 1))], 3)


def hash_ids(ids):
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def sent(out):
    """The list of messages that an engine sent; anything else is an error."""
    if not isinstance(out, (list, tuple)) or not all(isinstance(m, dict) for m in out):
        raise TypeError("select() must return a list of message dicts, not %s" % type(out).__name__)
    return list(out)


def largest_uncut(chat):
    """The tokens of the largest request of the replay of a chat, as the harness hands it to the engine: the chat so
    far, before select() cuts it. It is the same for every engine. The questions come after the replay and do not
    count."""
    upto = list(itertools.accumulate(meter.message_bytes(m) for m in chat.messages))  # bytes of messages[:i + 1]
    return max((upto[i] / 4 for i in chat.requests), default=0.0)


class BudgetRun:
    """All chats of one engine at one budget. top is the largest budget of the whole run (None for a ceiling)."""

    def __init__(self, budget, card, top=None):
        self.budget, self.card, self.top = budget, card, top
        self.rows, self.lines, self.times, self.chat_ids = [], [], [], []
        self.passing = None  # trace: (id, tokens) of the first chat by id with an uncut request over top
        self.largest = None  # trace, when no chat is over top: (largest uncut request, id, tokens)
        self.t0 = time.perf_counter()

    def offer(self, chat_id, peak, tokens):
        """Offer a chat for the trace. peak is the largest uncut request of the chat, tokens the size of each request
        that the engine sent for it. A chat with no request has nothing to draw."""
        if not tokens:
            return
        over = self.top is None or peak > self.top  # equal is not over, as in the meter; no budget: none to be over
        if over and (self.passing is None or chat_id < self.passing[0]):
            self.passing = (chat_id, tokens)
        if self.largest is None or peak > self.largest[0] or (peak == self.largest[0] and chat_id < self.largest[1]):
            self.largest = (peak, chat_id, tokens)

    @property
    def trace(self):
        """{"chat": id, "tokens": [...]} for the trace chart, or None: the first chat by id over the largest budget of
        the run; when no chat is over it, the chat with the largest uncut request."""
        pick = self.passing or (self.largest[1:] if self.largest else None)
        return {"chat": pick[0], "tokens": pick[1]} if pick else None

    def chat(self, adapter, manifest, chat):
        sizer = meter.Sizer()
        replay = meter.Replay(sizer, self.card, self.budget)
        peak = largest_uncut(chat)  # taken before the engine sees the chat
        qdropped, stats = 0, {}
        with tempfile.TemporaryDirectory(prefix="context-bench-") as work:
            eng = adapter(work, manifest, self.budget)
            try:
                for i in chat.requests:
                    request = chat.messages[:i + 1]
                    t = time.perf_counter()
                    out = sent(eng.select(request, self.budget))
                    self.times.append((time.perf_counter() - t) * 1000)
                    replay.add(out, request[-1])
                saved = eng.state() if chat.questions else None
                for q in chat.questions:
                    if not q.gold:
                        raise ValueError("question %s has no gold turn" % q.id)
                    asked = {"role": "user", "content": q.text}
                    out = sent(eng.select(chat.messages + [asked], self.budget))
                    ctx = meter.context_text(out)
                    row = {"id": q.id, "cat": str(q.category), "gold": len(q.gold),
                           "exact": round(meter.exact_share(ctx, chat.messages, q.gold), 6)}
                    w = meter.words_share(ctx, q.text, q.answer)
                    if w is not None:
                        row["words"] = round(w, 6)
                    row["tok"] = sizer.request(out) / 4
                    qdropped += not meter.tail_kept(out, asked)
                    self.rows.append(row)
                    if saved is not None:
                        eng.restore(saved)  # each question sees the state at the end of the chat
                stats = dict(eng.stats() or {})
            finally:
                eng.close()
        self.chat_ids.append(chat.id)
        self.offer(chat.id, peak, replay.series)
        self.lines.append({"id": chat.id, "msgs": len(chat.messages), "requests": replay.requests,
                           "tokens": replay.tokens, "cached": replay.cached, "max": replay.top, "over": replay.over,
                           "excess": round(replay.excess, 2), "dropped": replay.dropped,
                           "units": round(replay.units, 2), "usd": {k: round(v, 9) for k, v in replay.usd.items()},
                           "qdropped": qdropped, "engine": stats})

    def finish(self):
        rows, lines = self.rows, self.lines
        requests = sum(x["requests"] for x in lines)
        tokens = sum(x["tokens"] for x in lines)
        s: dict = {"chats": len(lines), "questions": len(rows)}
        if rows:
            cats = sorted({r["cat"] for r in rows}, key=lambda c: (len(c), c))
            s.update({"exact": mean(r["exact"] for r in rows), "words": mean(r["words"] for r in rows if "words" in r),
                      "words_questions": sum(1 for r in rows if "words" in r),
                      "context_tokens": mean(r["tok"] for r in rows), "context_max": max(r["tok"] for r in rows),
                      "context_over": 0, "context_excess": 0.0, "questions_dropped": sum(x["qdropped"] for x in lines),
                      "by_category": {c: mean(r["exact"] for r in rows if r["cat"] == c) for c in cats},
                      "questions_by_category": {c: sum(r["cat"] == c for r in rows) for c in cats}})
            if self.budget is not None:
                over = [r["tok"] - self.budget for r in rows if r["tok"] > self.budget]
                s["context_over"], s["context_excess"] = len(over), round(sum(over), 2)
        s.update({"requests": requests, "messages": sum(x["msgs"] for x in lines)})
        if requests:
            s.update({"tokens_per_request": round(tokens / requests, 1),
                      "cache_reuse": round(sum(x["cached"] for x in lines) / tokens, 4),
                      "cost_units": round(sum(x["units"] for x in lines) / requests, 1),
                      "usd": {k: round(sum(x["usd"][k] for x in lines) / requests, 9) for k in self.card["models"]},
                      "max_tokens": max(x["max"] for x in lines), "over": sum(x["over"] for x in lines),
                      "excess": round(sum(x["excess"] for x in lines), 2),
                      "dropped": sum(x["dropped"] for x in lines)})
        totals = {}
        for x in lines:
            for k, v in x["engine"].items():
                totals[k] = totals.get(k, 0) + v
        s["engine"] = totals
        s.update({"select_ms_p50": _pct(self.times, 0.5), "select_ms_p95": _pct(self.times, 0.95),
                  "wall_s": round(time.perf_counter() - self.t0, 1)})
        return {"budget": self.budget, "question_set": hash_ids(r["id"] for r in rows),
                "chat_set": hash_ids(self.chat_ids), "summary": s, "chats": lines, "questions": rows,
                "trace": [self.trace] if self.trace else []}


def run(engine_folder, source_folder, data=None, budgets=None, limit=0, runner="unnamed", role="other", card=None,
        progress=None):
    """The run record of one engine on one source, at each budget."""
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    manifest, adapter, adapter_sha = engine.load_engine(engine_folder)
    source, loader, loader_sha = sources.load_source(source_folder)
    files = sources.locate(source, data)
    card = card or meter.load_card()
    ceiling = bool(getattr(adapter, "ceiling", False))
    if ceiling:
        budgets, top = [None], None
    else:
        budgets = sorted(set(budgets or BUDGETS))
        if not all(isinstance(b, int) and not isinstance(b, bool) and b > 0 for b in budgets):
            raise ValueError("a budget is a positive number of tokens")
        top = budgets[-1]  # the largest budget of the run: the trace chat is the first chat over it
    runs = [BudgetRun(b, card, top) for b in budgets]
    load_before, started = _load(), datetime.datetime.now(datetime.timezone.utc)
    done = 0
    for chat in loader.chats(files, limit):
        for r in runs:
            r.chat(adapter, manifest, chat)
        done += 1
        if progress:
            progress(done, chat.id)
    return {
        "protocol": PROTOCOL,
        "harness": dict(version=__version__, **_commit()),
        "meter": meter.RULE,
        "price_card": {"date": card["date"], "sha256": card["sha256"]},
        "source": {"name": source["name"], "license": source["license"], "revision": source["revision"],
                   "files": [{"file": f["file"], "sha256": f["sha256"], "bytes": f["bytes"]}
                             for f in source["files"]],
                   "loader_sha256": loader_sha},
        "limit": limit,
        "engine": dict(manifest, adapter_sha256=adapter_sha, ceiling=ceiling),
        "runner": {"name": runner, "role": role, "date": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
                   "python": sys.version.split()[0], "hash_seed": os.environ.get("PYTHONHASHSEED"),
                   "load_before": load_before, "load_after": _load()},
        "runs": [r.finish() for r in runs],
    }
