"""Fixtures for the tests: a toy source, a spy engine, and run records of the shape that run.run builds."""

import hashlib
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A toy source with two chats. The answer text ZEBRA9 and the word gold must never reach an engine.
TOY = [
    {"id": "chat-a",
     "turns": [
         {"dia": "D1:1", "sess": "s1", "date": "1 May", "body": "Ann: I brew green tea at 80 C every morning."},
         {"dia": "D1:2", "sess": "s1", "date": "1 May", "body": "Bob: My bike chain broke on the hill."},
         {"dia": "D1:3", "sess": "s1", "date": "1 May", "body": "Ann: Try a new link."},
         {"dia": "D2:1", "sess": "s2", "date": "8 May", "body": "Bob: I fixed the bike chain with a new link."},
         {"dia": "D2:2", "sess": "s2", "date": "8 May", "body": "Ann: Good, the garden tomatoes are red."},
     ],
     "qa": [
         {"id": "chat-a/0", "text": "What temperature does Ann brew green tea at?", "cat": 1, "answer": "80 C ZEBRA9",
          "gold": ["D1:1"]},
         {"id": "chat-a/1", "text": "How did Bob fix the bike chain?", "cat": 4, "answer": "with a new link",
          "gold": ["D1:2", "D2:1"]},
     ]},
    {"id": "chat-b",
     "turns": [
         {"dia": "D1:1", "sess": "s1", "date": "3 June", "body": "Cy: The kettle is new."},
         {"dia": "D1:2", "sess": "s1", "date": "3 June", "body": "Di: Nice, what colour?"},
         {"dia": "D1:3", "sess": "s1", "date": "3 June", "body": "Cy: Blue, like the sea."},
     ],
     "qa": [
         {"id": "chat-b/0", "text": "What colour is the kettle?", "cat": 2, "answer": "blue", "gold": ["D1:3"]},
     ]},
]

SPY = '''
import json
import os
import contextbench


class Adapter(contextbench.Engine):
    def __init__(self, workdir, manifest, budget):
        super().__init__(workdir, manifest, budget)
        self.mode, self.log, self.n = manifest["mode"], manifest["log"], 0
        self.say("init", sorted(os.listdir(workdir)), budget, workdir)
        with open(os.path.join(workdir, "scratch.bin"), "wb") as f:
            f.write(b"x")

    def say(self, *args):
        with open(self.log, "a") as f:
            f.write(json.dumps(args) + "\\n")

    def select(self, request_messages, budget):
        self.n += 1
        self.say("select", len(request_messages), request_messages[-1]["role"], request_messages[-1]["content"],
                 budget, self.n, [sorted(m) for m in request_messages][-1])
        if self.mode == "flood":
            return request_messages + [{"role": "user", "content": "x" * 40000}]
        if self.mode == "bad":
            return ["not a message"]
        if self.mode == "short":
            return request_messages[:1] + request_messages[-1:]
        return request_messages

    def state(self):
        self.say("state", self.n)
        return {"n": self.n} if self.mode == "stateful" else None

    def restore(self, state):
        self.say("restore", state)
        self.n = state["n"]

    def stats(self):
        return {"seen": self.n}

    def close(self):
        self.say("close")
'''

SPY_CEILING = SPY + '''

Adapter.ceiling = True
'''


def msg(role, n, **kw):
    """A message with n characters of content, so n + 16 bytes for the meter."""
    return dict({"role": role, "content": "x" * n}, **kw)


def manifest(name, **kw):
    m = {"name": name, "version": "1", "license": "MIT", "access": "open-source", "homepage": "https://example.org",
         "source": "in-repo: engines/%s/adapter.py" % name, "about": "a test engine", "models": [], "runs_on": "mac",
         "tuned_on": [], "publication": None}
    m.update(kw)
    return m


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


LOADER = '''
import json
from contextbench.chats import Chat, Question, chat_of, dialogue_requests

FILE = "toy.json"


def chats(files, limit=0):
    with open(files[FILE], encoding="utf-8") as f:
        raw = json.load(f)
    for c in raw[:limit or None]:
        msgs, where = chat_of({"turns": c["turns"]})
        qs = [Question(q["id"], q["text"], q["cat"], q["answer"], [where[d] for d in q["gold"]]) for q in c["qa"]]
        yield Chat(c["id"], msgs, dialogue_requests(msgs), qs)
'''


class Fixture(unittest.TestCase):
    """A scratch folder with a toy source and a way to make spy engines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.dir, "toy.json")
        with open(self.data, "w") as f:
            json.dump(TOY, f)
        size = os.path.getsize(self.data)
        with open(self.data, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.source = os.path.join(self.dir, "sources", "toy")
        os.makedirs(self.source)
        rev = "a" * 40
        with open(os.path.join(self.source, "source.json"), "w") as f:
            json.dump({"name": "toy", "license": "MIT", "homepage": "https://example.org", "revision": rev,
                       "files": [{"file": "toy.json", "url": "https://example.org/%s/toy.json" % rev,
                                  "sha256": digest, "bytes": size}], "size": size, "about": "a toy"}, f)
        with open(os.path.join(self.source, "loader.py"), "w") as f:
            f.write(LOADER)

    def engine(self, mode, name=None, code=SPY, **kw):
        name = name or "spy-" + mode
        folder = os.path.join(self.dir, "engines", name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "adapter.py"), "w") as f:
            f.write(code)
        log = os.path.join(self.dir, name + ".log")
        with open(os.path.join(folder, "engine.json"), "w") as f:
            json.dump(manifest(name, mode=mode, log=log, **kw), f)
        return folder, log


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def calls(path):
    with open(path) as f:
        return [json.loads(x) for x in f]


def make_record(name, exacts, budget=1000, sha="a" * 64, source="s", date="2026-11-11T00:00:00Z", version="1",
                ceiling=False, questions_set=None, card="2026-10-07", over=0, dropped=0, protocol="context-bench/1",
                chat_set="c" * 64, source_kind="repo", **engine_kw):
    """A (record, run) pair of the shape that run.run builds, with one gold turn per question and exacts[i] as the
    exact score of question q<i>. exacts None: a source with no questions."""
    rows = [] if exacts is None else [{"id": "q%d" % i, "cat": "1", "gold": 1, "exact": x, "tok": 900.0}
                                      for i, x in enumerate(exacts)]
    summary = {"chats": 2, "questions": len(rows), "requests": 50, "messages": 120, "tokens_per_request": 500.0,
               "cache_reuse": 0.5, "cost_units": 300.0, "usd": {"claude-haiku-5-5": 0.00003, "claude-sonnet-5-5": 0.0006,
                                                                "claude-opus-5-5": 0.0012},
               "max_tokens": 990.0, "over": over, "excess": 10.0 * over, "dropped": dropped, "engine": {},
               "select_ms_p50": 0.05, "select_ms_p95": 0.1, "wall_s": 1.0}
    if rows:
        summary.update({"exact": round(sum(exacts) / len(exacts), 4), "words": 0.5, "words_questions": len(rows),
                        "context_tokens": 900.0, "context_max": 900.0, "context_over": 0, "context_excess": 0.0,
                        "questions_dropped": 0})
    source_pin = ("breadcrumbs @ git+https://example.org/x@" + "5" * 40) if source_kind == "git" else (
        "in-repo: engines/%s/adapter.py" % name)
    rec = {"protocol": protocol, "harness": {"version": "0.1.0", "commit": "0" * 40, "dirty": False},
           "price_card": {"date": card, "sha256": "d" * 64},
           "source": {"name": source, "license": "MIT", "revision": "r", "files": [{"file": "f", "sha256": sha,
                                                                                     "bytes": 1}]},
           "engine": manifest(name, version=version, source=source_pin, ceiling=ceiling, **engine_kw),
           "runner": {"name": "test", "role": "other", "date": date, "platform": "Linux-6", "machine": "x86_64",
                      "python": "3.14.0"}}
    qset = questions_set or ("b" * 64 if rows else "e" * 64)
    run = {"budget": None if ceiling else budget, "question_set": qset, "chat_set": chat_set, "summary": summary,
           "chats": [], "questions": rows}
    rec["runs"] = [run]
    return rec, run
