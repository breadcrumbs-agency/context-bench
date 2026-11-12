"""Tests of the charts: fixed bytes, the numbers of the records in the labels, axes from zero, equal budget steps."""

import contextlib
import io
import json
import math
import os
import re
import statistics
import tempfile
import unittest
import xml.etree.ElementTree as ET

from contextbench import board, cli, meter, plot, run as runner
from support import TOY, Fixture, make_record

NS = "{http://www.w3.org/2000/svg}"
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
SONNET, HAIKU = "claude-sonnet-5-5", "claude-haiku-5-5"

# Awkward numbers on purpose: a label that is rounded, cut or taken from another field shows at once.
EXACTS = {
    ("alpha", 100): [0, 1, 0.5, 0, 1, 0.3333, 0, 0, 1, 0.5, 0, 1],
    ("alpha", 200): [1, 1, 0.5, 0, 1, 0.6667, 0, 1, 1, 0.5, 0, 1],
    ("beta", 100): [0, 0, 0.5, 0, 1, 0.3333, 0, 0, 1, 0, 0, 1],
    ("beta", 200): [0, 1, 0.5, 0, 1, 0.3333, 0, 1, 1, 0.5, 0, 1],
}
USD = {("alpha", 100): (0.000379089, 0.0000189), ("alpha", 200): (0.0006123, 0.0000306),
       ("beta", 100): (0.00182, 0.000091), ("beta", 200): (0.0031415926, 0.000157),
       ("full", None): (0.0123456, 0.000617)}
CACHE = {("alpha", 100): 0.5123, ("alpha", 200): 0.7071, ("beta", 100): 0.2, ("beta", 200): 0.3333, ("full", None): 0.9901}
TOKENS = {
    ("alpha", 100): [20.25, 45.25, 70.5, 95.75, 40, 65.25],
    ("alpha", 200): [20.25, 60.5, 100.75, 140.25, 180.5, 205.75],
    ("beta", 100): [20.25, 45.25, 70.5, 95.75, 99.5, 99.75],
    ("beta", 200): [20.25, 45.25, 70.5, 95.75, 121.25, 146.5],
    ("full", None): [20.25, 45.25, 70.5, 95.75, 121.25, 146.5],
}


def pair(name, budget, source="toy", **kw):
    ceiling = budget is None
    rec, run = make_record(name, [1.0] * 12 if ceiling else EXACTS[(name, budget)], budget=budget, source=source,
                           ceiling=ceiling, **kw)
    usd, s = USD[(name, budget)], run["summary"]
    s["usd"], s["cache_reuse"] = {SONNET: usd[0], HAIKU: usd[1]}, CACHE[(name, budget)]
    run["trace"] = [{"chat": "chat-a", "tokens": TOKENS[(name, budget)]}]
    return rec, run


def fixture(**kw):
    return [pair("full", None, **kw)] + [pair(n, b, **kw) for n in ("alpha", "beta") for b in (100, 200)]


def parse(svg):
    return ET.fromstring(svg.encode("utf-8"))


def frames(root):
    """(left, top, right, bottom) of each plot frame, in the order of the document."""
    out = []
    for p in root.iter(NS + "path"):
        if p.get("class") == "axis":
            n = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", p.get("d"))]
            out.append((n[0], n[1], n[4], n[3]))
    return out


def ticks(root, axis):
    return [(float(t.get("data-v")), float(t.get("x")), float(t.get("y"))) for t in root.iter(NS + "text")
            if t.get("class") == "tick" and t.get("data-axis") == axis]


def marks(root):
    out = []
    for g in root.iter(NS + "g"):
        if g.get("class") == "pt":
            at = re.fullmatch(r"translate\((\S+) (\S+)\)", g.get("transform"))
            out.append({"series": g.get("data-series"), "v": g.get("data-v"), "x": float(at.group(1)),
                        "y": float(at.group(2)), "title": g.find(NS + "title").text})
    return out


def parse_titles(root):
    return " | ".join(t.text for t in root.iter(NS + "title") if t.text)


def y_axis(root, frame):
    """(top value, ticks) of the y axis of one frame; a tick label is 7 px left of the frame, its baseline 4 px low."""
    left, top, _right, bottom = frame
    found = [(v, y - 4) for v, x, y in ticks(root, "y") if abs(x - (left - 7)) < 0.01 and top - 0.01 <= y - 4 <= bottom + 0.01]
    return max(v for v, _y in found), found


class TestNumbers(unittest.TestCase):
    def test_a_coordinate_has_two_decimals_at_most_and_no_negative_zero(self):
        self.assertEqual([plot.num(x) for x in (3.0, 3.456, 0.004, -0.001, 10.0, 100.0, 12.5)],
                         ["3", "3.46", "0", "0", "10", "100", "12.5"])

    def test_a_record_value_reads_back_as_the_same_number(self):
        for v in (0.3456, 0.000006, 1e-05, 0.000379089, 3790.9, 4095.75, 0.0, 1.0, 123456.789):
            self.assertEqual(float(plot.plain(v)), v)
            self.assertNotIn("e", plot.plain(v))
        self.assertEqual(plot.plain(0.000006), "0.000006")
        self.assertEqual(plot.plain(8192), "8192")

    def test_a_scale_starts_at_zero_and_reaches_the_largest_value_in_few_steps(self):
        self.assertEqual(plot.scale(1.0), (1.0, 0.2, 1))
        self.assertEqual(plot.scale(205.75), (250.0, 50.0, 0))
        self.assertEqual(plot.scale(0.0), (1.0, 0.2, 1))  # nothing to show: the axis still spans 0 to 1
        for v in (0.0004, 0.0123456, 0.3, 0.69, 0.97, 5, 99.5, 4095.75, 19370.5, 65536, 250000):
            top, step, dec = plot.scale(v)
            self.assertGreaterEqual(top, v)
            self.assertLessEqual(round(top / step), 5)
            self.assertAlmostEqual(top / step, round(top / step), places=9)
            self.assertEqual(plot.fix(step, dec), plot.fix(float(plot.fix(step, dec)), dec))  # the step shows in full


class TestCharts(unittest.TestCase):
    def setUp(self):
        self.runs = fixture()
        self.files = plot.build(self.runs)

    def test_a_source_with_questions_has_five_charts_and_one_without_has_three(self):
        self.assertEqual(sorted(self.files), ["toy-cache.svg", "toy-cost.svg", "toy-exact.svg", "toy-trace.svg",
                                             "toy-tradeoff.svg"])
        flat = [make_record(n, None, budget=b, source="flat", ceiling=False) for n, b in (("a", 100), ("b", 100))]
        for rec, run in flat:
            run["trace"] = [{"chat": "c", "tokens": [1.0, 2.0]}]
        self.assertEqual(sorted(plot.build(flat)), ["flat-cache.svg", "flat-cost.svg", "flat-trace.svg"])
        for rec, run in flat:
            del run["trace"]
        self.assertEqual(sorted(plot.build(flat)), ["flat-cache.svg", "flat-cost.svg"])  # no trace in the records

    def test_the_bytes_are_fixed(self):
        for name, svg in self.files.items():
            path = os.path.join(GOLDEN, name)
            if os.environ.get("CONTEXTBENCH_GOLDEN") == "update":
                os.makedirs(GOLDEN, exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(svg.encode("utf-8"))
            with open(path, "rb") as fh:
                self.assertEqual(svg.encode("utf-8"), fh.read(), name + " differs from tests/golden")

    def test_a_long_note_wraps_so_that_it_fits_the_canvas(self):
        for name, svg in self.files.items():
            for t in parse(svg).iter(NS + "text"):
                if t.get("font-size") == "12" and t.get("fill") == "#666666":
                    self.assertLessEqual(len(t.text), 100, name)
        long_note = plot.heading("t", ["word " * 60])[0]
        self.assertGreater(long_note.count("<text"), 3)  # the title and more than two lines

    def test_the_input_order_does_not_change_the_bytes(self):
        self.assertEqual(plot.build(list(reversed(self.runs))), self.files)
        self.assertEqual(plot.build(self.runs), self.files)

    def test_every_file_is_xml_with_no_script_and_no_path(self):
        for name, svg in self.files.items():
            root = parse(svg)
            self.assertEqual(root.tag, NS + "svg")
            self.assertNotIn("<script", svg)
            home = os.path.expanduser("~")
            self.assertTrue(len(home) < 2 or home not in svg)  # no path of the machine that made the records
            self.assertTrue(svg.startswith('<?xml version="1.0" encoding="UTF-8"?>\n'))

    def test_every_y_axis_starts_at_zero_at_the_bottom_and_every_mark_is_on_the_linear_scale(self):
        for name, svg in self.files.items():
            root = parse(svg)
            for frame in frames(root):
                left, top, right, bottom = frame
                ytop, found = y_axis(root, frame)
                low = max(found, key=lambda t: t[1])
                self.assertEqual((name, low[0]), (name, 0.0))
                self.assertAlmostEqual(low[1], bottom, places=2, msg=name)  # the zero tick sits on the bottom edge
                self.assertAlmostEqual(min(y for _v, y in found), top, places=2, msg=name)
                for m in marks(root):
                    if top - 0.01 <= m["y"] <= bottom + 0.01 and left <= m["x"] <= right:
                        self.assertAlmostEqual(m["y"], bottom - float(m["v"]) / ytop * (bottom - top), delta=0.011,
                                               msg="%s %s" % (name, m["title"]))

    def test_a_number_axis_starts_at_zero_at_the_left(self):
        for name in ("toy-tradeoff.svg", "toy-trace.svg"):
            root = parse(self.files[name])
            for left, top, right, bottom in frames(root):
                xs = [(v, x) for v, x, y in ticks(root, "x") if abs(y - (bottom + 17)) < 0.01]
                low = min(xs, key=lambda t: t[1])
                self.assertEqual((low[0], round(low[1], 2)), (0.0, round(left, 2)), name)
                self.assertAlmostEqual(max(x for _v, x in xs), right, places=2, msg=name)
                steps = sorted(x for _v, x in xs)
                gaps = {round(b - a, 1) for a, b in zip(steps, steps[1:])}
                self.assertEqual(len(gaps), 1, name)  # linear: equal gaps for equal value steps

    def test_an_axis_does_not_start_above_zero_when_all_values_are_high(self):
        high = []
        for name, base in (("alpha", 0.92), ("beta", 0.95)):
            for budget in (100, 200):
                rec, run = make_record(name, [base, base + 0.01, base + 0.02, base - 0.01] * 3, budget=budget,
                                       source="toy")
                run["summary"]["usd"], run["summary"]["cache_reuse"] = {SONNET: 0.5, HAIKU: 0.4}, 0.9
                high.append((rec, run))
        files = plot.build(high)
        for chart in ("exact", "cache"):
            root = parse(files["toy-%s.svg" % chart])
            frame = frames(root)[0]
            ytop, found = y_axis(root, frame)
            self.assertEqual(min(v for v, _y in found), 0.0)
            self.assertAlmostEqual(max(y for _v, y in found), frame[3], places=2)
            for m in marks(root):
                self.assertAlmostEqual(m["y"], frame[3] - float(m["v"]) / ytop * (frame[3] - frame[1]), delta=0.011)

    def test_the_budget_axis_has_equal_steps_whatever_the_budgets(self):
        runs = []
        for budget in (100, 200, 800):
            rec, run = make_record("alpha", [0, 1, 0.5, 1], budget=budget, source="toy")
            run["summary"]["usd"], run["summary"]["cache_reuse"] = {SONNET: 0.5, HAIKU: 0.4}, 0.9
            runs.append((rec, run))
        root = parse(plot.build(runs)["toy-exact.svg"])
        pos = [(int(v), x) for v, x, _y in ((t.get("data-v"), float(t.get("x")), 0) for t in root.iter(NS + "text")
                                            if t.get("class") == "tick" and t.get("data-axis") == "x")]
        self.assertEqual([b for b, _x in pos], [100, 200, 800])
        gaps = [round(b[1] - a[1], 2) for a, b in zip(pos, pos[1:])]
        self.assertEqual(gaps[0], gaps[1])

    def test_a_label_carries_the_value_of_the_record(self):
        # exact: one mark for each engine and budget, the record's own number in data-v, in the title and in the table
        root, text = parse(self.files["toy-exact.svg"]), self.files["toy-exact.svg"]
        got = {(m["series"], int(m["title"].split("budget ")[1].split(":")[0])): m for m in marks(root)}
        for (name, budget), exacts in EXACTS.items():
            _rec, run = pair(name, budget)
            value = run["summary"]["exact"]
            self.assertEqual(float(got[(name, budget)]["v"]), value)
            self.assertIn("exact %s," % plot.plain(value), got[(name, budget)]["title"])
            self.assertIn(">%s ±" % plot.plain(value), text)
        # cost: the Sonnet panel above the Haiku panel, each with its own price
        root = parse(self.files["toy-cost.svg"])
        frs = frames(root)
        self.assertEqual(len(frs), 2)
        for m in marks(root):
            key, budget = m["series"], int(m["title"].split("budget ")[1].split(":")[0])
            panel = 0 if frs[0][1] <= m["y"] <= frs[0][3] else 1
            self.assertEqual(float(m["v"]), USD[(key, budget)][panel])
            self.assertIn("at %s" % ("Sonnet 5.5", "Haiku 5.5")[panel], m["title"])
            self.assertIn(plot.plain(USD[(key, budget)][panel]), m["title"])
        # cache
        for m in marks(parse(self.files["toy-cache.svg"])):
            self.assertEqual(float(m["v"]), CACHE[(m["series"], int(m["title"].split("budget ")[1].split(":")[0]))])
        # trade-off: exact in data-v, the Sonnet cost in the title, the ceiling mark with the value of full
        for m in marks(parse(self.files["toy-tradeoff.svg"])):
            if m["series"] == "full":
                self.assertEqual((float(m["v"]), USD[("full", None)][0]), (1.0, float(re.search(
                    r"input cost (\S+) US", m["title"]).group(1))))
                continue
            budget = int(m["title"].split("budget ")[1].split(":")[0])
            self.assertEqual(float(re.search(r"input cost (\S+) US", m["title"]).group(1)), USD[(m["series"], budget)][0])

    def test_the_trade_off_mark_is_placed_by_its_cost_on_a_linear_axis_from_zero(self):
        root = parse(self.files["toy-tradeoff.svg"])
        left, top, right, bottom = frames(root)[0]
        xs = [(v, x) for v, x, y in ticks(root, "x") if abs(y - (bottom + 17)) < 0.01]
        xtop = max(v for v, _x in xs)
        for m in marks(root):
            cost = float(re.search(r"input cost (\S+) US", m["title"]).group(1))
            self.assertAlmostEqual(m["x"], left + cost / xtop * (right - left), delta=0.011)

    def test_the_trace_has_the_tokens_of_each_request_of_the_chat(self):
        root = parse(self.files["toy-trace.svg"])
        clips = [g for g in root.iter(NS + "g") if g.get("clip-path")]
        self.assertEqual(len(clips), 2)  # a panel for each budget
        for g, frame, budget in zip(clips, frames(root), (100, 200)):
            left, top, right, bottom = frame
            ytop, _found = y_axis(root, frame)
            xs = [(v, x) for v, x, y in ticks(root, "x") if abs(y - (bottom + 17)) < 0.01]
            xtop = max(v for v, _x in xs)
            seen = set()
            for c in g.iter(NS + "circle"):
                label, rest = c.find(NS + "title").text.split(" ", 1)
                n, value = rest.split(": ")
                tokens = TOKENS[("full", None) if label == "full" else (label, budget)]
                self.assertEqual(float(value), tokens[int(n) - 1])
                self.assertAlmostEqual(float(c.get("cx")), left + int(n) / xtop * (right - left), delta=0.011)
                self.assertAlmostEqual(float(c.get("cy")), bottom - float(value) / ytop * (bottom - top), delta=0.011)
                seen.add((label, int(n)))
            self.assertEqual(len(seen), 3 * 6)  # alpha, beta and full, six requests each
        text = self.files["toy-trace.svg"]
        self.assertIn("full leaves the scale at request 5 (largest: 146.5 tokens)", text)  # the 100 panel only
        self.assertEqual(text.count("full leaves the scale"), 1)
        # the table: requests over the budget in this chat (205.75 > 200 for alpha)
        self.assertRegex(text, r">alpha</text>\s*<text[^>]*>6</text>\s*<text[^>]*>118\.0</text>\s*<text[^>]*>205\.75</text>\s*<text[^>]*>1</text>")

    def test_the_interval_bar_is_196_sd_over_root_n(self):
        root = parse(self.files["toy-exact.svg"])
        left, top, right, bottom = frames(root)[0]
        ytop, _found = y_axis(root, frames(root)[0])
        px = lambda v: bottom - v / ytop * (bottom - top)
        want = {}
        for (name, budget), exacts in EXACTS.items():
            v = pair(name, budget)[1]["summary"]["exact"]
            half = round(1.96 * statistics.stdev(exacts) / math.sqrt(len(exacts)), 6)
            want[(name, budget)] = (px(max(v - half, 0)), px(v + half))
        mark = {(m["series"], int(m["title"].split("budget ")[1].split(":")[0])): m for m in marks(root)}
        bars = [l for l in root.iter(NS + "line") if l.get("class") == "bar"]
        self.assertEqual(len(bars), 4)
        for key, m in mark.items():
            bar = next(l for l in bars if abs(float(l.get("x1")) - m["x"]) < 0.011)
            self.assertAlmostEqual(float(bar.get("y1")), want[key][0], delta=0.011, msg=str(key))
            self.assertAlmostEqual(float(bar.get("y2")), want[key][1], delta=0.011, msg=str(key))

    def test_a_bar_stops_at_the_zero_line(self):
        rec, run = make_record("alpha", [0] * 11 + [1], budget=100, source="toy")  # exact 0.0833, interval below 0
        run["summary"]["usd"], run["summary"]["cache_reuse"] = {SONNET: 0.5, HAIKU: 0.4}, 0.9
        root = parse(plot.build([(rec, run)])["toy-exact.svg"])
        bottom = frames(root)[0][3]
        bar = next(l for l in root.iter(NS + "line") if l.get("class") == "bar")
        self.assertAlmostEqual(float(bar.get("y1")), bottom, delta=0.011)
        self.assertIn("95% interval -0.0800 to 0.2466", parse_titles(root))  # the title keeps the unclipped numbers

    def test_full_is_a_dashed_line_at_its_value_and_has_no_budget_position(self):
        root = parse(self.files["toy-exact.svg"])
        left, top, right, bottom = frames(root)[0]
        ceil = [l for l in root.iter(NS + "line") if l.get("class") == "ceiling"]
        self.assertEqual(len(ceil), 1)
        self.assertEqual((float(ceil[0].get("y1")), float(ceil[0].get("y2"))), (top, top))  # exact 1.0 is the top
        self.assertTrue(ceil[0].get("stroke-dasharray"))
        self.assertEqual((float(ceil[0].get("x1")), float(ceil[0].get("x2"))), (left, right))
        root = parse(self.files["toy-cache.svg"])
        left, top, right, bottom = frames(root)[0]
        ytop, _found = y_axis(root, frames(root)[0])
        ceil = next(l for l in root.iter(NS + "line") if l.get("class") == "ceiling")
        self.assertAlmostEqual(float(ceil.get("y1")), bottom - 0.9901 / ytop * (bottom - top), delta=0.011)

    def test_a_name_with_markup_is_escaped_and_still_parses(self):
        runs = fixture()
        for rec, _run in runs:
            if rec["engine"]["name"] == "alpha":
                rec["engine"]["name"] = 'a&b<c>"d'
        files = plot.build(runs)
        for svg in files.values():
            root = parse(svg)
        found = [t.text for t in parse(files["toy-exact.svg"]).iter(NS + "text")]
        self.assertIn('a&b<c>"d', found)
        self.assertNotIn('a&b<c>', files["toy-exact.svg"])

    def test_two_versions_of_an_engine_are_two_lines_with_the_version_in_the_label(self):
        runs = fixture() + [pair("alpha", 100, version="2")]
        runs[-1][0]["runner"]["date"] = "2026-11-12T00:00:00Z"
        labels = {m["series"] for m in marks(parse(plot.build(runs)["toy-exact.svg"]))}
        self.assertEqual(labels, {"alpha 1", "alpha 2", "beta"} | set())  # alpha 2 has one budget only

    def test_a_source_name_must_be_a_file_name(self):
        for bad in ("../x", "a/b", "", ".hidden", "a b"):
            with self.assertRaises(ValueError):
                plot.file_name(bad, "exact")
        runs = fixture(source="a/b")
        with self.assertRaises(ValueError):
            plot.build(runs)

    def test_a_licensed_engine_without_a_grant_is_not_drawn_unless_private(self):
        runs = fixture()
        runs += [pair("secret", 100) if False else pair("alpha", 100)]
        rec, run = pair("alpha", 200)
        rec["engine"].update(name="secret", access="licensed", publication=None)
        runs.append((rec, run))
        shown = plot.build(runs)["toy-exact.svg"]
        self.assertNotIn("secret", shown)
        self.assertIn("secret", plot.build(runs, True)["toy-exact.svg"])

    def test_only_the_largest_comparable_set_of_a_source_is_drawn(self):
        runs = fixture()
        rec, run = pair("alpha", 100)
        run["chat_set"] = "d" * 64  # another chat set: the board shows it as a group of its own
        files = plot.build(runs + [(rec, run)])
        self.assertEqual(files, plot.build(runs))

    def test_no_chart_speaks_of_a_ceiling_that_was_not_run(self):
        files = plot.build(fixture()[1:])  # the same runs without full
        self.assertEqual(sorted(files), sorted(plot.build(fixture())))
        for name, svg in files.items():
            self.assertNotIn("full", svg, name)
            self.assertNotIn("ceiling", svg, name)
        self.assertEqual(len(frames(parse(files["toy-exact.svg"]))), 1)

    def test_charts_of_runs_with_no_budget_alone_are_none(self):
        self.assertEqual(plot.build([pair("full", None)]), {})
        self.assertEqual(plot.build([]), {})


class TestBoardLinks(unittest.TestCase):
    def test_the_board_links_each_chart_under_the_tables_of_its_source(self):
        runs = fixture() + fixture(source="zeta")
        text = board.render(runs)
        files = plot.build(runs)
        links = re.findall(r"!\[[^\]]*\]\((charts/[^)]+)\)", text)
        self.assertEqual(sorted(links), sorted("charts/" + n for n in files))
        for source in ("toy", "zeta"):
            at = text.index("### Charts of %s" % source)
            self.assertGreater(at, text.rindex("## %s, budget" % source))  # after its last table
            nxt = text.find("\n## ", at)
            block = text[at:nxt if nxt > 0 else len(text)]
            for chart in ("exact", "cost", "tradeoff", "cache", "trace"):
                self.assertIn("(charts/%s-%s.svg)" % (source, chart), block)
        self.assertLess(text.index("### Charts of toy"), text.index("## zeta, budget"))

    def test_a_board_with_no_budget_has_no_chart_section(self):
        self.assertNotIn("Charts of", board.render([pair("full", None)]))


class TestTrace(Fixture):
    def run_all(self, **kw):
        folder, _log = self.engine("all")
        return runner.run(folder, self.source, self.data, budgets=[1000, 2000], **kw)

    def test_the_trace_is_the_tokens_of_each_request_of_the_first_chat_by_id(self):
        rec = self.run_all()
        for run in rec["runs"]:
            self.assertEqual(len(run["trace"]), 1)
            trace = run["trace"][0]
            self.assertEqual(trace["chat"], "chat-a")
            line = run["chats"][0]
            self.assertEqual(len(trace["tokens"]), line["requests"])
            self.assertAlmostEqual(math.fsum(trace["tokens"]), line["tokens"], places=6)
            self.assertEqual(max(trace["tokens"]), line["max"])
            self.assertEqual(trace["tokens"], sorted(trace["tokens"]))  # the "all" engine sends the whole chat: it grows

    def test_the_first_chat_by_id_does_not_depend_on_the_order_of_the_file(self):
        with open(self.data, "w") as f:
            json.dump(list(reversed(TOY)), f)
        import hashlib
        with open(self.data, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        path = os.path.join(self.source, "source.json")
        with open(path) as f:
            manifest = json.load(f)
        manifest["files"][0]["sha256"] = digest
        manifest["files"][0]["bytes"] = manifest["size"] = os.path.getsize(self.data)
        with open(path, "w") as f:
            json.dump(manifest, f)
        rec = self.run_all()
        self.assertEqual([c["id"] for c in rec["runs"][0]["chats"]], ["chat-b", "chat-a"])
        self.assertEqual(rec["runs"][0]["trace"][0]["chat"], "chat-a")
        self.assertEqual(len(rec["runs"][0]["trace"][0]["tokens"]), 3)

    def test_the_trace_is_one_compact_line_in_the_record_file(self):
        rec = self.run_all()
        path = os.path.join(self.dir, "out", "rec.json")
        cli.write_record(rec, path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["runs"][0]["trace"], rec["runs"][0]["trace"])
        tokens = [x for x in lines if '"tokens":[' in x and '"chat":"chat-a"' in x]
        self.assertEqual(len(tokens), 2)  # one line for each budget
        self.assertTrue(all(x.strip().startswith("{") for x in tokens))

    def test_the_trace_does_not_change_any_measured_number(self):
        rec = self.run_all()
        for run in rec["runs"]:
            s, lines = run["summary"], run["chats"]
            self.assertEqual(s["requests"], sum(x["requests"] for x in lines))
            self.assertEqual(s["max_tokens"], max(x["max"] for x in lines))

    def test_cli_plot_writes_the_charts_and_refuses_an_empty_set(self):
        recs = os.path.join(self.dir, "recs")
        for i, (rec, _run) in enumerate(fixture()):
            cli.write_record(rec, os.path.join(recs, "r%d.json" % i))
        out = os.path.join(self.dir, "charts")
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(cli.main(["plot", recs, "--out", out]), 0)
        self.assertEqual(sorted(shown.getvalue().split()), sorted(os.path.join(out, n) for n in os.listdir(out)))
        self.assertEqual(sorted(os.listdir(out)), ["toy-cache.svg", "toy-cost.svg", "toy-exact.svg", "toy-trace.svg",
                                                   "toy-tradeoff.svg"])
        with open(os.path.join(out, "toy-exact.svg"), "rb") as f:
            self.assertEqual(f.read(), plot.build(board.load([recs]))["toy-exact.svg"].encode("utf-8"))
        with self.assertRaises(SystemExit):
            cli.main(["plot", os.path.join(self.dir, "empty"), "--out", os.path.join(self.dir, "none")])


if __name__ == "__main__":
    unittest.main()
