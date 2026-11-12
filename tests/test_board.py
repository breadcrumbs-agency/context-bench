import json
import math
import os
import statistics
import tempfile
import unittest

from contextbench import board
from support import make_record


def render(*pairs):
    return board.render(list(pairs))


def ones(n_ones, n):
    return [1.0] * n_ones + [0.0] * (n - n_ones)


class TestPaired(unittest.TestCase):
    def test_the_interval_is_the_normal_one(self):
        a = {str(i): 1.0 for i in range(100)}
        b = {str(i): 1.0 if i < 40 else 0.0 for i in range(100)}
        m, lo, hi, wins, ties, losses = board.paired(a, b)
        half = 1.96 * math.sqrt(24 / 99) / 10  # sd of 60 ones and 40 zeros, over the root of 100
        self.assertAlmostEqual(m, 0.6)
        self.assertAlmostEqual(lo, 0.6 - half)
        self.assertAlmostEqual(hi, 0.6 + half)
        self.assertEqual((wins, ties, losses), (60, 40, 0))
        self.assertEqual(statistics.stdev([1] * 60 + [0] * 40) ** 2 * 99, 24)

    def test_only_the_questions_of_both_count(self):
        m, _lo, _hi, wins, ties, losses = board.paired({"a": 1.0, "b": 0.5, "c": 0.0}, {"b": 0.0, "c": 0.0, "d": 1.0})
        self.assertEqual((m, wins, ties, losses), (0.25, 1, 1, 0))

    def test_one_question_has_no_interval(self):
        _m, lo, hi, *_ = board.paired({"a": 1.0}, {"a": 0.0})
        self.assertEqual((lo, hi), (-math.inf, math.inf))


class TestRender(unittest.TestCase):
    def test_table_order_and_head_to_head(self):
        strong = make_record("strong", ones(90, 100))
        weak = make_record("weak", ones(40, 100))
        twin = make_record("twin", ones(40, 100))
        close = make_record("close", ones(41, 100))
        text = render(weak, strong, twin, close)
        rows = [line for line in text.splitlines() if line.startswith("| 1 |") or line.startswith("| 2 |")]
        self.assertIn("| 1 | strong | 1 | 0.900 |", rows[0])
        self.assertIn("| 2 | close | 1 | 0.410 |", rows[1])
        self.assertIn("| strong |  | +0.490 ▲ (49/51/0) | +0.500 ▲ (50/50/0) | +0.500 ▲ (50/50/0) |", text)
        self.assertIn("| close | -0.490 ▼ (0/51/49) |  | +0.010 = (1/99/0) | +0.010 = (1/99/0) |", text)  # ahead, not clearly
        self.assertIn("| twin | -0.500 ▼ (0/50/50) | -0.010 = (0/99/1) |  | +0.000 = (0/100/0) |", text)
        self.assertIn("Clearly ahead: strong > close; strong > twin; strong > weak.", text)

    def test_nobody_is_ahead_when_no_interval_excludes_zero(self):
        text = render(make_record("a", ones(41, 100)), make_record("b", ones(40, 100)))
        self.assertIn("Clearly ahead: no pair differs clearly.", text)

    def test_the_ceiling_row_comes_first_and_has_no_rank(self):
        top = make_record("full", [1.0] * 100, ceiling=True)
        text = render(make_record("a", ones(90, 100)), top, make_record("b", ones(40, 100)))
        lines = [x for x in text.splitlines() if x.startswith("|") and "full" in x.split("|")[2]]
        self.assertTrue(lines[0].startswith("| ceiling | full |"))
        self.assertIn("| 1 | a |", text)
        self.assertIn("| row − column | a | b |", text)  # the ceiling is not in the head to head
        self.assertEqual(text.count("## s, budget 1000 tokens"), 1)

    def test_the_ceiling_is_in_each_budget_group_of_its_source(self):
        top = make_record("full", [1.0] * 10, ceiling=True)
        text = render(top, make_record("a", ones(5, 10), budget=1000), make_record("a", ones(6, 10), budget=2000))
        self.assertIn("## s, budget 1000 tokens", text)
        self.assertIn("## s, budget 2000 tokens", text)
        self.assertEqual(text.count("| ceiling | full |"), 2)

    def test_runs_meet_only_when_source_bytes_budget_and_question_set_agree(self):
        a, b = make_record("a", ones(5, 10)), make_record("b", ones(6, 10))
        self.assertEqual(render(a, b).count("## s, budget"), 1)
        other_bytes = make_record("b", ones(6, 10), sha="f" * 64)
        self.assertEqual(render(a, other_bytes).count("## s, budget"), 2)
        other_budget = make_record("b", ones(6, 10), budget=2000)
        self.assertEqual(render(a, other_budget).count("## s, budget"), 2)
        other_questions = make_record("b", ones(6, 10), questions_set="9" * 64)
        self.assertEqual(render(a, other_questions).count("## s, budget"), 2)
        other_chats = make_record("b", ones(6, 10), chat_set="8" * 64)
        self.assertEqual(render(a, other_chats).count("## s, budget"), 2)
        other_source = make_record("b", ones(6, 10), source="t")
        self.assertEqual(render(a, other_source).count("## "), 2)
        other_protocol = make_record("b", ones(6, 10), protocol="context-bench/2")
        self.assertEqual(render(a, other_protocol).count("## s, budget"), 2)

    def test_the_newest_run_of_an_engine_replaces_the_older_one(self):
        old = make_record("x", [0.0] * 10, date="2026-01-01T00:00:00Z")
        new = make_record("x", [1.0] * 10, date="2026-02-01T00:00:00Z")
        text = render(old, new)
        self.assertIn("| 1 | x | 1 | 1.000 |", text)
        self.assertNotIn("0.000 |", text.split("\n\n")[3])
        both = render(old, make_record("x", [1.0] * 10, version="2"))
        self.assertIn("| 1 | x | 2 |", both)  # another version is another row
        self.assertIn("| 2 | x | 1 |", both)

    def test_the_git_commit_shows_beside_the_version(self):
        text = render(make_record("ace", ones(5, 10), version="0.4.1", source_kind="git"))
        self.assertIn("| 1 | ace | 0.4.1 (5555555) |", text)

    def test_over_budget_and_newest_dropped_show_beside_each_run(self):
        text = render(make_record("a", ones(5, 10), over=3, dropped=2))
        self.assertIn("| 990 | 3 | 30 | 2 | 0.05 |", text)
        self.assertIn("Requests per engine: 50.", text)

    def test_a_source_with_no_questions_has_no_head_to_head(self):
        text = render(make_record("a", None), make_record("b", None))
        self.assertNotIn("Head to head", text)
        self.assertNotIn("| Exact |", text)
        self.assertIn("This source has no questions", text)
        self.assertIn("| - | a | 1 | 500 | 300.0 |", text)  # no rank without questions
        self.assertIn("| - | b | 1 | 500 | 300.0 |", text)

    def test_dollars_show_for_sonnet_and_haiku(self):
        text = render(make_record("a", ones(5, 10)))
        self.assertIn("$/request Sonnet 5.5 | $/request Haiku 5.5", text)
        self.assertIn("| 0.000600 | 0.000030 |", text)

    def test_two_price_cards_in_one_group_are_flagged(self):
        text = render(make_record("a", ones(5, 10)), make_record("b", ones(5, 10), card="2026-12-01"))
        self.assertIn("Price card: 2026-10-07, 2026-12-01.", text)
        self.assertIn("The price cards differ in this group", text)

    def test_a_licensed_engine_needs_a_grant_to_show_numbers(self):
        grant = {"granted_by": "Vendor Inc.", "date": "2026-11-11", "evidence": "https://example.org/grant"}
        closed = make_record("closed", ones(9, 10), access="licensed")
        granted = make_record("granted", ones(8, 10), access="licensed", publication=grant)
        text = render(closed, granted, make_record("open", ones(5, 10)))
        self.assertIn("Withheld (licensed, no publication grant): closed.", text)
        self.assertNotIn("| closed |", text)
        self.assertIn("| granted |", text)
        self.assertIn("| closed |", board.render([closed, granted], private=True))
        self.assertNotIn("Withheld", board.render([closed, granted], private=True))
        self.assertEqual(board.render([closed]), "")  # a group with nothing to show is not written

    def test_load_reads_only_context_bench_records(self):
        with tempfile.TemporaryDirectory() as d:
            for i, protocol in enumerate(("context-bench/1", "memory-bench/1")):
                rec, _run = make_record("a", ones(5, 10), protocol=protocol)
                with open(os.path.join(d, "r%d.json" % i), "w") as f:
                    json.dump(rec, f)
            runs = board.load([d])
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0][0]["protocol"], "context-bench/1")


if __name__ == "__main__":
    unittest.main()
