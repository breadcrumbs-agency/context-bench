import contextlib
import hashlib
import io
import json
import os
import unittest

from contextbench import cli, engine, run as runner
from support import ROOT, TOY, Fixture, SPY_CEILING, calls, msg, read


def kinds(log):
    return [c[0] for c in calls(log)]


class TestStream(Fixture):
    def run_spy(self, mode, **kw):
        folder, log = self.engine(mode)
        return runner.run(folder, self.source, self.data, **kw), log

    def test_requests_are_sent_in_order_and_questions_come_last(self):
        rec, log = self.run_spy("all", budgets=[1000])
        events = calls(log)
        self.assertEqual([e[0] for e in events], ["init", "select", "select", "select", "state", "select", "select",
                                                  "close", "init", "select", "select", "state", "select", "close"])
        a, b = events[:8], events[8:]
        questions = [q["text"] for c in TOY for q in c["qa"]]
        # each chat: one request at each user message (the messages grow by one turn, the last is a user message)
        self.assertEqual([e[1] for e in a if e[0] == "select"][:3], [2, 4, 6])
        self.assertEqual([e[1] for e in b if e[0] == "select"][:2], [2, 4])
        self.assertTrue(all(e[2] == "user" for e in events if e[0] == "select"))
        # no question before the last request of its chat, and no question text in any request of the chat
        for chat, part in zip(TOY, (a, b)):
            selects = [e for e in part if e[0] == "select"]
            asked = len(chat["qa"])
            replay = selects[:len(selects) - asked]
            self.assertTrue(all(e[3] not in questions for e in replay))
            self.assertEqual([e[3] for e in selects[len(replay):]], [q["text"] for q in chat["qa"]])
            self.assertEqual([e[1] for e in selects[len(replay):]], [len(chat["turns"]) + 2] * asked)
        # the history grows by whole turns: the request at user message i holds the chat up to i
        self.assertEqual(rec["runs"][0]["summary"]["requests"], 5)

    def test_state_is_taken_once_and_restored_after_each_question(self):
        folder, log = self.engine("stateful")
        runner.run(folder, self.source, self.data, budgets=[1000])
        events = calls(log)
        first = events[:events.index(["close"]) + 1]
        self.assertEqual([e[0] for e in first], ["init", "select", "select", "select", "state", "select", "restore",
                                                 "select", "restore", "close"])
        counters = [e[5] for e in first if e[0] == "select"]
        # every question sees the state of the end of the chat (3 requests), not the one that the question before left
        self.assertEqual(counters, [1, 2, 3, 4, 4])
        self.assertEqual([e[1] for e in first if e[0] == "restore"], [{"n": 3}, {"n": 3}])

    def test_an_engine_never_gets_a_gold_turn_or_an_answer(self):
        folder, log = self.engine("all")
        runner.run(folder, self.source, self.data, budgets=[1000])
        text = read(log)
        self.assertNotIn("ZEBRA9", text)  # an answer
        self.assertNotIn("gold", text)
        for e in calls(log):
            if e[0] == "select":
                self.assertEqual(e[6], ["content", "role"])  # a message dict, and nothing else with it
                self.assertEqual(e[4], 1000)  # the budget
            if e[0] == "init":
                self.assertEqual(e[1], [])  # an empty folder

    def test_each_chat_gets_a_new_engine_in_an_empty_folder_that_is_deleted(self):
        folder, log = self.engine("all")
        runner.run(folder, self.source, self.data, budgets=[1000])
        inits = [e for e in calls(log) if e[0] == "init"]
        self.assertEqual(len(inits), 2)
        self.assertEqual(inits[0][1], [])
        self.assertEqual(inits[1][1], [])
        self.assertNotEqual(inits[0][3], inits[1][3])
        for e in inits:
            self.assertFalse(os.path.exists(e[3]))

    def test_one_engine_for_each_chat_and_budget(self):
        folder, log = self.engine("all")
        rec = runner.run(folder, self.source, self.data, budgets=[2000, 1000, 1000])
        self.assertEqual([r["budget"] for r in rec["runs"]], [1000, 2000])
        inits = [(e[2]) for e in calls(log) if e[0] == "init"]
        self.assertEqual(inits, [1000, 2000, 1000, 2000])  # chat a at both budgets, then chat b

    def test_a_ceiling_engine_has_no_budget(self):
        folder, log = self.engine("all", name="spy-top", code=SPY_CEILING)
        rec = runner.run(folder, self.source, self.data, budgets=[1000, 2000])
        self.assertEqual([r["budget"] for r in rec["runs"]], [None])
        self.assertTrue(rec["engine"]["ceiling"])
        self.assertTrue(all(e[4] is None for e in calls(log) if e[0] == "select"))

    def test_the_harness_does_not_cut_but_counts_the_excess(self):
        rec, _ = self.run_spy("flood", budgets=[1000])
        s = rec["runs"][0]["summary"]
        self.assertEqual(s["over"], s["requests"])  # each request is 10,000 tokens over
        self.assertGreater(s["excess"], 5 * 9000)
        self.assertGreater(s["max_tokens"], 10000)
        self.assertEqual(s["dropped"], s["requests"])  # the newest message is not the last one sent
        self.assertEqual(s["context_over"], 3)

    def test_what_an_engine_returns_must_be_messages(self):
        with self.assertRaises(TypeError):
            self.run_spy("bad", budgets=[1000])

    def test_scores_come_from_the_context_the_engine_sent(self):
        rec, _ = self.run_spy("all", budgets=[100000])
        rows = {q["id"]: q for q in rec["runs"][0]["questions"]}
        self.assertEqual(sorted(rows), ["chat-a/0", "chat-a/1", "chat-b/0"])
        self.assertTrue(all(q["exact"] == 1.0 for q in rows.values()))  # the whole chat is there
        rec, _ = self.run_spy("short", budgets=[100000])  # the system message and the question only
        self.assertTrue(all(q["exact"] == 0.0 for q in rec["runs"][0]["questions"]))
        self.assertTrue(all(q["tok"] < 100 for q in rec["runs"][0]["questions"]))

    def test_record_shape_and_hashes(self):
        rec, _ = self.run_spy("all", budgets=[1000, 2000], limit=0)
        self.assertEqual(rec["protocol"], "context-bench/1")
        self.assertEqual(rec["price_card"]["date"], "2026-10-07")
        self.assertEqual(rec["engine"]["adapter_sha256"], hashlib.sha256(
            read(os.path.join(self.dir, "engines", "spy-all", "adapter.py"), "rb")).hexdigest())
        run = rec["runs"][0]
        ids = ["chat-a/0", "chat-a/1", "chat-b/0"]
        self.assertEqual(run["question_set"], hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest())
        self.assertEqual(run["chat_set"], hashlib.sha256("chat-a\nchat-b".encode()).hexdigest())
        self.assertEqual(run["summary"]["engine"], {"seen": 8})  # the engine's own counters, summed over the chats
        self.assertEqual([c["id"] for c in run["chats"]], ["chat-a", "chat-b"])
        self.assertEqual(run["chats"][0]["requests"], 3)
        shown = json.dumps(dict(rec, engine={k: v for k, v in rec["engine"].items() if k != "log"}))  # the spy's own key
        self.assertNotIn(self.dir, shown)  # no local path in a record
        home = os.path.expanduser("~")
        self.assertTrue(len(home) < 2 or home not in shown)  # nor the home folder of the runner

    def test_limit_keeps_the_first_chats(self):
        rec, _ = self.run_spy("all", budgets=[1000], limit=1)
        self.assertEqual([c["id"] for c in rec["runs"][0]["chats"]], ["chat-a"])
        self.assertEqual(rec["limit"], 1)

    def test_a_wrong_file_is_refused_before_the_stream_starts(self):
        folder, log = self.engine("all")
        with open(self.data, "a") as f:
            f.write(" ")
        with self.assertRaises(ValueError):
            runner.run(folder, self.source, self.data, budgets=[1000])
        self.assertFalse(os.path.exists(log))

    def test_cli_run_then_board(self):
        folder, _ = self.engine("all")
        out = os.path.join(self.dir, "public", "rec.json")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["run", folder, "--source", self.source, "--data", self.data, "--budgets", "1000", "--runner",
                      "test", "--out", out])
        with open(out) as f:
            rec = json.load(f)
        self.assertEqual(rec["runs"][0]["budget"], 1000)
        text = read(out)
        self.assertIn('\n    {"id":"chat-a/0",', text)  # one compact line for each question
        self.assertEqual(text.count('"id":"chat-a/1"'), 1)
        md = os.path.join(self.dir, "BOARD.md")
        cli.main(["board", os.path.dirname(out), "--out", md])
        with open(md) as f:
            board = f.read()
        self.assertIn("## toy, budget 1000 tokens", board)
        self.assertIn("| 1 | spy-all | 1 |", board)


class TestBaselines(unittest.TestCase):
    def load(self, name, budget):
        manifest_, adapter, _ = engine.load_engine(os.path.join(ROOT, "engines", name))
        return adapter(os.devnull, manifest_, budget)

    def chat(self, n):
        return [msg("system", 4)] + [msg("user" if i % 2 == 0 else "assistant", 44) for i in range(n)]  # 20 + 60 each

    def test_full_sends_everything(self):
        msgs = self.chat(6)
        eng = self.load("full", None)
        self.assertTrue(eng.ceiling)
        self.assertEqual(eng.select(msgs, None), msgs)

    def test_window_keeps_the_newest_messages_that_fit_and_the_system_message(self):
        msgs = self.chat(8)
        out = self.load("window", 50).select(msgs, 50)  # 200 bytes: the system message and the newest three
        self.assertEqual(out, msgs[:1] + msgs[-3:])
        self.assertEqual(self.load("window", 10).select(msgs, 10), msgs[:1] + msgs[-1:])  # the newest always stays
        self.assertEqual(self.load("window", 10000).select(msgs, 10000), msgs)

    def test_drop_cuts_in_steps_from_a_user_message_and_then_only_appends(self):
        msgs = self.chat(8)  # roles: user, assistant, user, ... (index 1 is a user message)
        eng = self.load("drop", 100)  # 400 bytes; a cut leaves 0.6 of it: 240 bytes
        out = eng.select(msgs, 100)  # 20 + 8 * 60 = 500 bytes: over the limit
        # the newest three fit 240 bytes with the system message; the window starts at the user message that follows
        self.assertEqual(out, msgs[:1] + msgs[7:])
        self.assertEqual(eng.state(), {"start": 7, "folds": 1})
        longer = msgs + [msg("user", 44)]
        self.assertEqual(eng.select(longer, 100), msgs[:1] + msgs[7:] + longer[9:])  # appends, no new cut
        self.assertEqual(eng.stats(), {"folds": 1})
        eng.restore({"start": 1, "folds": 0})
        self.assertEqual(eng.stats(), {"folds": 0})
        self.assertEqual(self.load("drop", 100).select(msgs[:4], 100), msgs[:4])  # under the limit: untouched


class TestShippedEngines(unittest.TestCase):
    def test_every_engine_manifest_is_valid_and_its_folder_complete(self):
        names = sorted(os.listdir(os.path.join(ROOT, "engines")))
        self.assertEqual(names, ["breadcrumbs-ace", "drop", "full", "langchain-trim", "llamaindex-buffer", "window"])
        for name in names:
            folder = os.path.join(ROOT, "engines", name)
            m = engine.load_manifest(folder)
            self.assertEqual(m["name"], name)
            self.assertEqual(m["models"], [])  # the first board has no model
            self.assertEqual(m["runs_on"], "mac")
            self.assertTrue(os.path.exists(os.path.join(folder, "adapter.py")))
            self.assertTrue(os.path.exists(os.path.join(folder, "requirements.txt")))

    def test_requirements_are_exact_pins_and_hold_no_model_runtime(self):
        forbidden = ("torch", "onnxruntime", "tensorflow", "nvidia", "triton", "transformers", "sentence-transformers",
                     "llama-cpp", "mlx")
        for name in sorted(os.listdir(os.path.join(ROOT, "engines"))):
            folder = os.path.join(ROOT, "engines", name)
            m = engine.load_manifest(folder)
            with open(os.path.join(folder, "requirements.txt")) as f:
                pins = [x.strip() for x in f if x.strip() and not x.startswith("#")]
            kind, dist, _pin = engine.parse_source(m["source"])
            if kind == "repo":
                self.assertEqual(pins, [], name)
                continue
            self.assertEqual(pins[0], m["source"], name)  # the engine's own pin comes first
            for line in pins:
                self.assertTrue(" @ git+https://" in line or "==" in line, line)  # nothing loose
                self.assertFalse(any(word in line.lower() for word in forbidden), line)

    def test_a_library_engine_raises_when_its_package_is_missing_or_another_version(self):
        from unittest import mock
        import importlib.metadata as md
        for name in ("breadcrumbs-ace", "langchain-trim", "llamaindex-buffer"):
            m, adapter, _ = engine.load_engine(os.path.join(ROOT, "engines", name))
            with mock.patch("importlib.metadata.version", side_effect=md.PackageNotFoundError):
                with self.assertRaises(RuntimeError) as cm:
                    adapter(os.devnull, m, 8192)
                self.assertIn("not installed", str(cm.exception))
            with mock.patch("importlib.metadata.version", return_value="0.0.1"):
                with self.assertRaises(RuntimeError) as cm:
                    adapter(os.devnull, m, 8192)
                self.assertIn("pins", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
