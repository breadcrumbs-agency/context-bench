"""Charts of the board: plain SVG text, made from run records only.

No plotting library and no other dependency. The bytes depend on the records and on nothing else: one number format,
series in sorted order, no date. A rebuild of the same records gives the same files, so a gate can compare them.

The charts of a source (file charts/<source>-<chart>.svg):
  exact     exact against budget, a line for each engine, with the 95% interval of the mean at each point
  cost      input cost per request in US dollars against budget: a panel at Sonnet 5.5 and a panel at Haiku 5.5
  tradeoff  exact against input cost per request at Sonnet 5.5, a mark for each engine and budget; full is the ceiling
  cache     cache reuse against budget
  trace     the tokens of each request of one chat (the first by id), in order: a panel for each budget
A source without questions has cost, cache and trace only.

Rules of every chart: each measured axis is linear and starts at zero, at the bottom and at the left. The budget axis
has equal steps, labeled with the budget values. Each axis label names its unit. Each point carries its number from the
record, as an SVG <title> (shown on hover) and in the table under the plot (shown where the SVG is an image).
"""

import math
import os
import re
import statistics
import textwrap
from decimal import Decimal
from xml.sax.saxutils import escape

from contextbench import board

DIR = "charts"
Z95 = 1.96
WIDTH = 840
CHARTS = (  # (name, needs questions, caption)
    ("exact", True, "exact against budget, with the 95% interval of the mean"),
    ("cost", False, "input cost per request in dollars against budget"),
    ("tradeoff", True, "exact against input cost per request"),
    ("cache", False, "cache reuse against budget"),
    ("trace", False, "tokens of each request in one chat"),
)
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
INK, MUTED, GRID, AXIS = "#222222", "#666666", "#dddddd", "#333333"
PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#B8860B", "#56B4E9", "#6B6B6B", "#8C564B")
DASHES = ("", "7 3", "2 3", "9 3 2 3", "", "7 3", "2 3", "9 3 2 3")
MARKERS = ("circle", "square", "triangle", "diamond", "cross", "plus", "circle", "square")
CEILING = (INK, "5 4", "diamond")  # the style of full: the engine with no budget


# -- numbers and text ------------------------------------------------------------------------------------------
def num(v):
    """A coordinate: two decimals at most, no trailing zeros, never '-0'."""
    s = "%.2f" % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def plain(v):
    """A record value as plain decimal text (no exponent) that reads back as the same number."""
    if isinstance(v, int):
        return str(v)
    return format(Decimal(repr(float(v))), "f")


def fix(v, decimals):
    return "%.*f" % (decimals, v)


def scale(vmax, intervals=5):
    """The zero-based linear scale of an axis: (top, step, decimals) with top = k * step >= vmax and k <= intervals."""
    if not vmax > 0:
        vmax = 1.0
    raw = vmax / intervals
    e = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * 10.0 ** e >= raw * (1 - 1e-9):
            break
    if m == 10:
        m, e = 1, e + 1
    step = m * 10.0 ** e
    k = max(1, math.ceil(vmax / step - 1e-9))
    return k * step, step, max(0, -e)


def _text(s):
    return escape(str(s))


def _attr(s):
    return escape(str(s), {'"': "&quot;"})


# -- SVG parts -------------------------------------------------------------------------------------------------
def el(tag, attrs, body=None):
    a = "".join(' %s="%s"' % (k, _attr(v)) for k, v in attrs)
    return "<%s%s/>\n" % (tag, a) if body is None else "<%s%s>%s</%s>\n" % (tag, a, body, tag)


def text(x, y, s, anchor=None, size=None, fill=None, weight=None, rot=False, cls=None, data=()):
    a = [("class", cls)] if cls else []
    a += [("x", num(x)), ("y", num(y))]
    if rot:
        a.append(("transform", "rotate(-90 %s %s)" % (num(x), num(y))))
    for k, v in (("text-anchor", anchor), ("font-size", size), ("font-weight", weight), ("fill", fill)):
        if v:
            a.append((k, v))
    return el("text", a + list(data), _text(s))


def line(x1, y1, x2, y2, stroke, width=1, dash="", cls=None, title=None):
    a = [("class", cls)] if cls else []
    a += [("x1", num(x1)), ("y1", num(y1)), ("x2", num(x2)), ("y2", num(y2)), ("stroke", stroke),
          ("stroke-width", width)]
    if dash:
        a.append(("stroke-dasharray", dash))
    if title is None:
        return el("line", a)
    return el("line", a, "<title>%s</title>" % _text(title))


def polyline(points, style, width=1.6, opacity=None):
    color, dash, _kind = style
    a = [("fill", "none"), ("stroke", color), ("stroke-width", width)]
    if dash:
        a.append(("stroke-dasharray", dash))
    if opacity:
        a.append(("stroke-opacity", opacity))
    a.append(("points", " ".join("%s,%s" % (num(x), num(y)) for x, y in points)))
    return el("polyline", a)


def marker(kind, color, s=4.5):
    """A mark drawn around (0, 0)."""
    def poly(*pts):
        return el("polygon", [("points", " ".join("%s,%s" % (num(x), num(y)) for x, y in pts)), ("fill", color)])

    if kind == "circle":
        return el("circle", [("r", num(s)), ("fill", color)])
    if kind == "square":
        return el("rect", [("x", num(-s)), ("y", num(-s)), ("width", num(2 * s)), ("height", num(2 * s)),
                           ("fill", color)])
    if kind == "triangle":
        return poly((0, -1.25 * s), (1.15 * s, 0.9 * s), (-1.15 * s, 0.9 * s))
    if kind == "diamond":
        return poly((0, -1.35 * s), (1.35 * s, 0), (0, 1.35 * s), (-1.35 * s, 0))
    d = "M%s %sL%s %sM%s %sL%s %s" % ((num(-s), num(-s), num(s), num(s), num(-s), num(s), num(s), num(-s))
                                       if kind == "cross" else
                                       (num(-s), "0", num(s), "0", "0", num(-s), "0", num(s)))
    return el("path", [("d", d), ("fill", "none"), ("stroke", color), ("stroke-width", 2)])


def point(x, y, style, series, value, title):
    """One mark with its number: the value from the record is in data-v, in the <title> and in the table."""
    color, _dash, kind = style
    a = [("class", "pt"), ("transform", "translate(%s %s)" % (num(x), num(y))), ("data-series", series),
         ("data-v", plain(value))]
    return el("g", a, "<title>%s</title>\n%s" % (_text(title), marker(kind, color)))


def swatch(x, y, style):
    color, dash, kind = style
    return line(x, y, x + 22, y, color, 1.6, dash) + el(
        "g", [("transform", "translate(%s %s)" % (num(x + 11), num(y)))], "\n" + marker(kind, color, 3.5))


def table(y, cols, header, rows):
    """A header and rows of text under a plot. A row is (style or None, cells); the first cell follows a swatch."""
    out = "".join(text(x, y, h, size=11, weight="bold", fill=MUTED) for x, h in zip(cols, header))
    for style, cells in rows:
        y += 18
        if style:
            out += swatch(24, y - 4, style)
        out += "".join(text(x, y, c) for x, c in zip(cols, cells))
    return out, y + 14


def columns(first, count, room=WIDTH - 24, widest=170):
    """x of `count` equal columns from `first` to the right margin, each at most `widest` wide."""
    step = min(widest, (room - first) / max(count, 1))
    return [first + k * step for k in range(count)]


def document(height, title, body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="Helvetica, Arial, sans-serif" font-size="11" fill="%s">\n<title>%s</title>\n'
            '<rect width="%d" height="%d" fill="#ffffff"/>\n%s</svg>\n'
            % (WIDTH, height, WIDTH, height, INK, _text(title), WIDTH, height, body))


def heading(title, notes):
    """The title and the notes under it. A note wraps at 100 characters, so that a wide fallback font still fits."""
    out, y = text(24, 28, title, size=16, weight="bold"), 48
    for note in notes:
        for part in textwrap.wrap(note, 100, break_long_words=False, break_on_hyphens=False):
            out += text(24, y, part, size=12, fill=MUTED)
            y += 16
    return out, y + 22


class Panel:
    """A frame with a zero-based linear y axis, and an x axis that is either numeric (zero-based, linear) or a row
    of categories at equal steps."""

    def __init__(self, left, top, height, ymax, cats=None, xmax=None, xsteps=6, right=24):
        self.left, self.top, self.height = left, top, height
        self.right, self.bottom = WIDTH - right, top + height
        self.width = self.right - left
        self.ytop, self.ystep, self.ydec = scale(ymax)
        self.cats = cats
        if cats is None:
            self.xtop, self.xstep, self.xdec = scale(xmax, xsteps)

    def y(self, v):
        return self.bottom - v / self.ytop * self.height

    def x(self, v):
        return self.left + v / self.xtop * self.width

    def cx(self, i):
        return self.left + (i + 0.5) * self.width / len(self.cats)

    def frame(self, ytitle, xtitle):
        out = ""
        for i in range(int(round(self.ytop / self.ystep)) + 1):
            v = i * self.ystep
            if i:
                out += line(self.left, self.y(v), self.right, self.y(v), GRID)
            out += text(self.left - 7, self.y(v) + 4, fix(v, self.ydec), anchor="end", cls="tick",
                        data=(("data-axis", "y"), ("data-v", fix(v, self.ydec))))
        if self.cats is None:
            for i in range(int(round(self.xtop / self.xstep)) + 1):
                v = i * self.xstep
                if i:
                    out += line(self.x(v), self.top, self.x(v), self.bottom, GRID)
                out += text(self.x(v), self.bottom + 17, fix(v, self.xdec), anchor="middle", cls="tick",
                            data=(("data-axis", "x"), ("data-v", fix(v, self.xdec))))
        else:
            for i, c in enumerate(self.cats):
                out += line(self.cx(i), self.bottom, self.cx(i), self.bottom + 4, AXIS)
                out += text(self.cx(i), self.bottom + 18, c, anchor="middle", cls="tick",
                            data=(("data-axis", "x"), ("data-v", c)))
        out += el("path", [("class", "axis"), ("fill", "none"), ("stroke", AXIS), ("stroke-width", 1.2),
                           ("d", "M%s %sL%s %sL%s %s" % (num(self.left), num(self.top), num(self.left),
                                                         num(self.bottom), num(self.right), num(self.bottom)))])
        out += text((self.left + self.right) / 2, self.bottom + 40, xtitle, anchor="middle", size=12)
        out += text(14, (self.top + self.bottom) / 2, ytitle, anchor="middle", size=12, rot=True)
        return out


def left_margin(panels):
    """Room for the longest y tick label."""
    longest = max(len(fix(p.ytop, p.ydec)) for p in panels)
    return int(36 + 6.6 * longest)


def styles(labels):
    return {label: (PALETTE[i % len(PALETTE)], DASHES[i % len(DASHES)], MARKERS[i % len(MARKERS)])
            for i, label in enumerate(labels)}


# -- the records ----------------------------------------------------------------------------------------------
def _trace(run):
    t = (run.get("trace") or [None])[0]
    return (t["chat"], t["tokens"]) if t and t.get("tokens") else None


def _family(g, base):
    ceil = board._latest(g["ceiling"])
    by = {b: board._latest(items) for b, items in g["by"].items()}
    versions = {}
    for items in list(by.values()) + [ceil]:
        for rec, _run in items:
            versions.setdefault(rec["engine"]["name"], set()).add(rec["engine"]["version"])

    def label(rec):
        e = rec["engine"]
        return e["name"] if len(versions[e["name"]]) == 1 else "%s %s" % (e["name"], e["version"])

    cells = {(label(rec), b): (rec, run) for b, items in by.items() for rec, run in items}
    ceilings = sorted((label(rec), rec, run) for rec, run in ceil)
    traces = {k: _trace(run) for k, (rec, run) in cells.items() if _trace(run)}
    ceiling_trace = _trace(ceilings[0][2]) if ceilings else None
    chats = sorted({t[0] for t in traces.values()} | ({ceiling_trace[0]} if ceiling_trace else set()))
    first = chats[0] if chats else None
    traces = {k: t[1] for k, t in traces.items() if t[0] == first}
    items = [x for b in by for x in by[b]] + ceil
    return {"source": base[1], "questions": any(run["summary"]["questions"] for _rec, run in items),
            "budgets": sorted(by), "labels": sorted({k[0] for k in cells}), "cells": cells,
            "ceiling": ceilings[0] if ceilings else None, "traces": traces, "trace_chat": first,
            "ceiling_trace": ceiling_trace[1] if ceiling_trace and ceiling_trace[0] == first else None,
            "cards": sorted({rec["price_card"]["date"] for rec, _run in items}),
            "summary": items[0][1]["summary"], "protocol": base[0]}


def families(groups):
    """{source name: family}: for each source the set of comparable runs with most runs (the board shows the others)."""
    best = {}
    for base in sorted(groups):
        g = groups[base]
        count = len(g["ceiling"]) + sum(len(v) for v in g["by"].values())
        if count and (base[1] not in best or count > best[base[1]][0]):
            best[base[1]] = (count, base)
    return {name: _family(groups[base], base) for name, (_count, base) in sorted(best.items())}


def file_name(source, chart):
    if not SAFE_NAME.fullmatch(source):
        raise ValueError("a source name is letters, digits, dot, dash and underscore: %r" % (source,))
    return "%s-%s.svg" % (source, chart)


def charts_of(fam):
    """The charts that the records of a family can make: [(name, caption)]."""
    if not fam["budgets"]:
        return []
    return [(n, c) for n, needs, c in CHARTS
            if (fam["questions"] or not needs) and (n != "trace" or fam["traces"])]


def chart_files(fams):
    """{source: [(file name, caption)]}: what the board links under its tables."""
    return {name: [(file_name(name, n), c) for n, c in charts_of(fam)] for name, fam in fams.items()}


# -- the charts -----------------------------------------------------------------------------------------------
def interval(run):
    """(half width, questions): the 95% interval of the mean of exact is mean +- 1.96 sd / sqrt(n)."""
    xs = [q["exact"] for q in run["questions"]]
    n = len(xs)
    return (round(Z95 * statistics.stdev(xs) / math.sqrt(n), 6) if n > 1 else 0.0), n


def _exact(rec, run):
    v = run["summary"]["exact"]
    half, n = interval(run)
    return v, half, "exact %s, 95%% interval %s to %s (%d questions)" % (plain(v), fix(v - half, 4), fix(v + half, 4), n)


def _usd(key, name):
    def get(rec, run):
        v = run["summary"]["usd"].get(key)
        return None if v is None else (v, 0.0, "input cost %s US dollars per request at %s" % (plain(v), name))
    return get


def _cache(rec, run):
    v = run["summary"]["cache_reuse"]
    return v, 0.0, "cache reuse %s" % plain(v)


def _k(budget):
    return str(budget)


def _ceil_note(ceil, note):
    """A note about the ceiling line, only when the chart has that line."""
    return [note] if ceil else []


def category_chart(fam, kind):
    """exact, cost and cache: lines against the budget axis; cost has a panel for each priced model."""
    budgets, labels = fam["budgets"], fam["labels"]
    st = styles(labels)
    s0, ceil = fam["summary"], fam["ceiling"]
    if kind == "exact":
        panels = [(None, "exact (share of gold turns, 0 to 1)", _exact, True)]
        title = "%s: exact against budget" % fam["source"]
        notes = ["%d questions in %d chats. Mark: exact, the mean over the questions. Bar: 95%% interval of the "
                 "mean (± 1.96 sd/√n)." % (s0["questions"], s0["chats"])] + _ceil_note(
            ceil, "Dashed black line: full, the ceiling that sends the whole chat.")
    elif kind == "cost":
        panels = [(name, "input cost per request (US dollars)", _usd(key, name), False) for key, name in board.PRICED]
        title = "%s: input cost per request against budget" % fam["source"]
        notes = ["Dollars of input at the price card of %s: cached tokens at the cache-read price, the others at the "
                 "5-minute cache-write price." % ", ".join(fam["cards"]),
                 "Mean over the %d requests of the replay.%s"
                 % (s0["requests"], " Dashed black line: full, the ceiling." if ceil else "")]
    else:
        panels = [(None, "cache reuse (share of request tokens, 0 to 1)", _cache, False)]
        title = "%s: cache reuse against budget" % fam["source"]
        notes = ["Cache reuse: the share of the request's tokens that sit in the leading messages which the request "
                 "before also sent."] + _ceil_note(ceil, "Dashed black line: full, the ceiling.")
    drawn = []
    for head, ytitle, get, bars in panels:
        data = {lab: [(i, get(*fam["cells"][(lab, b)])) for i, b in enumerate(budgets) if (lab, b) in fam["cells"]]
                for lab in labels}
        data = {lab: [(i, d) for i, d in pts if d] for lab, pts in data.items()}
        top = ceil and get(ceil[1], ceil[2])
        values = [d[0] + d[1] for pts in data.values() for _i, d in pts] + ([top[0]] if top else [])
        drawn.append((head, ytitle, get, bars, data, top, max(values) if values else 0))
    left = left_margin([Panel(0, 0, 1, d[6], cats=[]) for d in drawn])
    out, y = heading(title, notes)
    for head, ytitle, get, bars, data, top, ymax in drawn:
        if head:
            out += text(left, y + 4, head, size=13, weight="bold")
            y += 18
        pn = Panel(left, y + 8, 270, ymax, cats=[_k(b) for b in budgets])
        out += pn.frame(ytitle, "budget (tokens per request)")
        if top:
            out += line(pn.left, pn.y(top[0]), pn.right, pn.y(top[0]), INK, 1.4, CEILING[1], "ceiling", top[2])
            low = pn.y(top[0]) - pn.top < 16
            out += text(pn.right - 4, pn.y(top[0]) + (14 if low else -5), "full (ceiling) " + plain(top[0]),
                        anchor="end", size=11)
        m = len(labels)
        for j, lab in enumerate(labels):
            pts = [(pn.cx(i) + (j - (m - 1) / 2) * 7, i, d) for i, d in data[lab]]
            if bars:
                for x, i, d in pts:
                    lo, hi = pn.y(max(d[0] - d[1], 0)), pn.y(d[0] + d[1])  # the bar stops at the zero line
                    out += line(x, lo, x, hi, st[lab][0], 1.4, cls="bar")
                    out += line(x - 3.5, lo, x + 3.5, lo, st[lab][0], 1.4)
                    out += line(x - 3.5, hi, x + 3.5, hi, st[lab][0], 1.4)
            if len(pts) > 1:
                out += polyline([(x, pn.y(d[0])) for x, _i, d in pts], st[lab])
            for x, i, d in pts:
                out += point(x, pn.y(d[0]), st[lab], lab, d[0], "%s, budget %s: %s" % (lab, budgets[i], d[2]))
        cols = [52] + columns(250, len(budgets))
        rows = []
        for lab in labels:
            cells = [lab]
            for i, b in enumerate(budgets):
                d = dict(data[lab]).get(i)
                cells.append("" if not d else plain(d[0]) + (" ±" + fix(d[1], 4) if bars else ""))
            rows.append((st[lab], cells))
        if top:
            rows.append((CEILING, ["full (ceiling)"] + [plain(top[0]) + (" ±" + fix(interval(ceil[2])[0], 4)
                                                                         if bars else "")] * len(budgets)))
        tab, y = table(pn.bottom + 66, cols, ["engine"] + ["budget " + _k(b) for b in budgets], rows)
        out += tab
        y += 6
    return document(int(y + 10), title, out)


def tradeoff_chart(fam):
    """Exact against input cost per request at Sonnet 5.5: a mark for each engine and budget."""
    key, name = board.PRICED[0]
    budgets, labels = fam["budgets"], fam["labels"]
    st = styles(labels)
    pts = {}
    for lab in labels:
        for b in budgets:
            if (lab, b) in fam["cells"]:
                rec, run = fam["cells"][(lab, b)]
                if run["summary"]["usd"].get(key) is not None:
                    pts.setdefault(lab, []).append((b, run["summary"]["exact"], run["summary"]["usd"][key]))
    ceil = fam["ceiling"]
    cpt = ceil and (ceil[2]["summary"]["exact"], ceil[2]["summary"]["usd"].get(key))
    xs = [p[2] for v in pts.values() for p in v] + ([cpt[1]] if cpt and cpt[1] is not None else [])
    ys = [p[1] for v in pts.values() for p in v] + ([cpt[0]] if cpt else [])
    out, y = heading("%s: exact against input cost per request" % fam["source"],
                     ["One mark for each engine and budget; the tag is the budget in tokens.%s"
                      % (" Dashed black line and diamond: full, the ceiling." if ceil else ""),
                      "Dollars of input at %s, price card of %s." % (name, ", ".join(fam["cards"]))])
    probe = Panel(0, 0, 1, max(ys) if ys else 0, xmax=max(xs) if xs else 0)
    pn = Panel(left_margin([probe]), y, 360, max(ys) if ys else 0, xmax=max(xs) if xs else 0)
    out += pn.frame("exact (share of gold turns, 0 to 1)", "input cost per request at %s (US dollars)" % name)
    if cpt and cpt[1] is not None:
        out += line(pn.left, pn.y(cpt[0]), pn.right, pn.y(cpt[0]), INK, 1.4, CEILING[1], "ceiling",
                    "full (ceiling): exact %s" % plain(cpt[0]))
    tags = []
    for lab in labels:
        p = pts.get(lab, [])
        if len(p) > 1:
            out += polyline([(pn.x(c), pn.y(e)) for _b, e, c in p], st[lab], 1.2, 0.6)
        for b, e, c in p:
            out += point(pn.x(c), pn.y(e), st[lab], lab, e,
                         "%s, budget %s: exact %s, input cost %s US dollars per request at %s"
                         % (lab, b, plain(e), plain(c), name))
            if not any(abs(tx - pn.x(c)) < 24 and abs(ty - pn.y(e)) < 10 for tx, ty in tags):
                tags.append((pn.x(c), pn.y(e)))
                out += text(pn.x(c) + 7, pn.y(e) - 7, b, size=9, fill=MUTED)
    if cpt and cpt[1] is not None:
        out += point(pn.x(cpt[1]), pn.y(cpt[0]), CEILING, "full", cpt[0],
                     "full (ceiling): exact %s, input cost %s US dollars per request at %s"
                     % (plain(cpt[0]), plain(cpt[1]), name))
        out += text(pn.x(cpt[1]) - 9, pn.y(cpt[0]) + (14 if pn.y(cpt[0]) - pn.top < 16 else -8), "full (ceiling)",
                    anchor="end", size=11)
    cols = [52, 250] + columns(370, len(budgets), widest=150)
    rows = []
    for lab in labels:
        have = {b: (e, c) for b, e, c in pts.get(lab, [])}
        rows.append((st[lab], [lab, "exact"] + [plain(have[b][0]) if b in have else "" for b in budgets]))
        rows.append((None, ["", "$ per request"] + [plain(have[b][1]) if b in have else "" for b in budgets]))
    if cpt and cpt[1] is not None:
        rows.append((CEILING, ["full (ceiling)", "exact"] + [plain(cpt[0])] + [""] * (len(budgets) - 1)))
        rows.append((None, ["", "$ per request"] + [plain(cpt[1])] + [""] * (len(budgets) - 1)))
    tab, y = table(pn.bottom + 66, cols, ["engine", "measure"] + ["budget " + _k(b) for b in budgets], rows)
    return document(int(y + 16), "%s: exact against input cost per request" % fam["source"], out + tab)


def trace_chart(fam):
    """The tokens of each request of one chat, in order: a panel for each budget, a line for each engine."""
    chat = fam["trace_chat"]
    budgets = [b for b in fam["budgets"] if any((lab, b) in fam["traces"] for lab in fam["labels"])]
    st = styles(fam["labels"])
    ceil_tokens = fam["ceiling_trace"]
    out, y = heading("%s: tokens of each request, chat %s" % (fam["source"], chat),
                     ["Tokens of each request of the replay of this chat, in order. Dashed grey line: the budget."
                      ] + _ceil_note(ceil_tokens, "Dashed black line: full, which has no budget; it is cut at the top "
                                                 "of the plot where it leaves the scale."))
    panels = []
    for b in budgets:
        series = {lab: fam["traces"][(lab, b)] for lab in fam["labels"] if (lab, b) in fam["traces"]}
        ymax = max([b] + [v for t in series.values() for v in t])
        n = max(len(t) for t in series.values())
        panels.append((b, series, Panel(0, 0, 1, ymax, xmax=n, xsteps=8)))
    left = left_margin([p[2] for p in panels])
    for k, (b, series, probe) in enumerate(panels):
        out += text(left, y + 4, "budget %s tokens" % _k(b), size=13, weight="bold")
        n = max(len(t) for t in series.values())
        pn = Panel(left, y + 14, 220, probe.ytop, xmax=n, xsteps=8)
        out += pn.frame("tokens in the request (harness tokens)", "request number in the chat (count)")
        out += line(pn.left, pn.y(b), pn.right, pn.y(b), MUTED, 1.2, "3 3", "budget", "budget %s tokens" % b)
        clip = "clip%d" % k
        out += el("clipPath", [("id", clip)], el("rect", [("x", num(pn.left)), ("y", num(pn.top)),
                                                           ("width", num(pn.width)), ("height", num(pn.height))]))
        out += '<g clip-path="url(#%s)">\n' % clip
        rows = []
        if ceil_tokens:
            out += polyline([(pn.x(i + 1), pn.y(v)) for i, v in enumerate(ceil_tokens)], CEILING, 1.4)
        for lab in fam["labels"]:
            if lab in series:
                out += polyline([(pn.x(i + 1), pn.y(v)) for i, v in enumerate(series[lab])], st[lab])
        for lab in fam["labels"]:
            if lab in series:
                for i, v in enumerate(series[lab]):
                    out += el("circle", [("cx", num(pn.x(i + 1))), ("cy", num(pn.y(v))), ("r", "1.5"),
                                         ("fill", st[lab][0])], "<title>%s %d: %s</title>" % (_text(lab), i + 1, plain(v)))
        if ceil_tokens:
            for i, v in enumerate(ceil_tokens):
                out += el("circle", [("cx", num(pn.x(i + 1))), ("cy", num(pn.y(v))), ("r", "1.5"), ("fill", INK)],
                          "<title>full %d: %s</title>" % (i + 1, plain(v)))
        out += "</g>\n"
        if ceil_tokens and max(ceil_tokens) > pn.ytop:
            first = next(i + 1 for i, v in enumerate(ceil_tokens) if v > pn.ytop)
            out += text(pn.right, pn.top - 10, "full leaves the scale at request %d (largest: %s tokens)"
                        % (first, plain(max(ceil_tokens))), anchor="end", size=11, fill=MUTED)
        for lab in fam["labels"]:
            if lab in series:
                tk = series[lab]
                rows.append((st[lab], [lab, str(len(tk)), fix(math.fsum(tk) / len(tk), 1), plain(max(tk)),
                                       str(sum(1 for v in tk if v > b))]))
        if ceil_tokens:
            rows.append((CEILING, ["full (ceiling)", str(len(ceil_tokens)), fix(math.fsum(ceil_tokens) / len(ceil_tokens), 1),
                                   plain(max(ceil_tokens)), ""]))
        tab, y = table(pn.bottom + 62, [52, 250, 350, 470, 590],
                       ["engine", "requests", "mean tokens", "largest request", "requests over budget"], rows)
        out += tab
        y += 14
    return document(int(y), "%s: tokens of each request, chat %s" % (fam["source"], chat), out)


DRAW = {"exact": lambda f: category_chart(f, "exact"), "cost": lambda f: category_chart(f, "cost"),
        "tradeoff": tradeoff_chart, "cache": lambda f: category_chart(f, "cache"), "trace": trace_chart}


def build(runs, private=False):
    """{file name: SVG text} of every chart that the records can make, in the order of the names."""
    out = {}
    for name, fam in families(board.group(runs, private)).items():
        for chart, _caption in charts_of(fam):
            out[file_name(name, chart)] = DRAW[chart](fam)
    return dict(sorted(out.items()))


def write(files, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name in sorted(files):
        path = os.path.join(out_dir, name)
        with open(path, "wb") as fh:
            fh.write(files[name].encode("utf-8"))
        paths.append(path)
    return paths
