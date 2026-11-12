"""The board: one table for each group of comparable runs, and a head-to-head matrix for each group with questions.

Two runs share a group only when the protocol, the source (name and bytes), the budget, the question set and the
chat set are the same. A ceiling engine (no budget, no rank) shows in every group of its source, as the first row.
A head to head is a paired per-question difference of exact, with a normal 95% interval (mean +- 1.96 sd / sqrt(n)).
The board names an engine ahead only when that interval excludes zero.
"""

import glob
import json
import math
import os
import statistics

from contextbench.engine import parse_source

PRICED = (("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-haiku-5-5", "Haiku 5.5"))  # the dollar columns of the board
HEAD = ("# Board\n\nWritten by `python -m contextbench board`. Do not edit it by hand: run records in "
        "`results/public/` make it. README.md says how the numbers are made.\n\n"
        "- Exact: the share of gold turns whose full text is in the context, mean over the questions. Words: the "
        "share of the gold answer's words in the context.\n"
        "- Cost: per request of the replay, in ace_bench's token units (0.1 per cached token, 1.25 per other "
        "token), and in dollars of input at the dated price card (cached tokens at the cache-read price, the others "
        "at the 5-minute cache-write price).\n"
        "- Charts: under the tables of each source, made from the same records by `python -m contextbench plot`.\n"
        "- Over budget: the harness does not cut a request. It counts the requests over the budget and the tokens "
        "over it. Newest dropped: requests that did not end with the newest message.\n\n")


def load(paths):
    """[(record, run)] of every run in the record files under paths."""
    out = []
    for p in paths:
        files = sorted(glob.glob(os.path.join(p, "**", "*.json"), recursive=True)) if os.path.isdir(p) else [p]
        for name in files:
            with open(name, encoding="utf-8") as f:
                rec = json.load(f)
            if str(rec.get("protocol", "")).startswith("context-bench/") and rec.get("runs"):
                out.extend((rec, run) for run in rec["runs"])
    return out


def withheld(rec, private):
    """A run of a licensed engine shows numbers only with a publication grant in its manifest, or under --private."""
    e = rec["engine"]
    return e["access"] == "licensed" and not e.get("publication") and not private


def per_question(run):
    """question id -> exact."""
    return {q["id"]: q["exact"] for q in run["questions"]}


def paired(a, b):
    """(mean of a - b, low, high of its 95% interval, a wins, ties, b wins) over the questions of both."""
    d = [a[k] - b[k] for k in sorted(a) if k in b]
    m = statistics.fmean(d)
    half = 1.96 * statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else math.inf
    return m, m - half, m + half, sum(x > 0 for x in d), sum(x == 0 for x in d), sum(x < 0 for x in d)


def _f(x, digits=3):
    return "" if x is None else "%.*f" % (digits, x)


def _version(eng):
    kind, _dist, pin = parse_source(eng["source"])
    return eng["version"] + (" (%s)" % pin[:7] if kind == "git" else "")


def _latest(items):
    out = {}
    for rec, run in items:
        k = (rec["engine"]["name"], rec["engine"]["version"])
        if k not in out or rec["runner"]["date"] > out[k][0]["runner"]["date"]:
            out[k] = (rec, run)
    return list(out.values())


def _table(items, questions):
    head = ["#", "Engine", "Version"] + (["Exact", "Words", "Context tokens"] if questions else [])
    head += ["Tokens/request", "Cost units/request"] + ["$/request %s" % n for _k, n in PRICED]
    head += ["Cache reuse", "Max request", "Requests over", "Tokens over", "Newest dropped", "Select ms p50"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    rank = 0
    for rec, run in items:
        s, eng = run["summary"], rec["engine"]
        if eng.get("ceiling"):
            mark = "ceiling"
        elif not questions:
            mark = "-"  # no questions: the rows go by name, and nothing ranks them
        else:
            rank += 1
            mark = str(rank)
        cells = [mark, eng["name"], _version(eng)]
        if questions:
            cells += [_f(s["exact"]), _f(s["words"]), _f(s["context_tokens"], 0)]
        qd = s.get("questions_dropped") or 0
        cells += [_f(s["tokens_per_request"], 0), _f(s["cost_units"], 1)] + [_f(s["usd"].get(k), 6) for k, _n in PRICED]
        cells += [_f(s["cache_reuse"]), _f(s["max_tokens"], 0), "" if eng.get("ceiling") else str(s["over"]),
                  "" if eng.get("ceiling") else _f(s["excess"], 0),
                  "%d" % s["dropped"] + (" (+%dq)" % qd if qd else ""), _f(s["select_ms_p50"], 2)]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _matrix(names, vals):
    lines = ["| row − column | " + " | ".join(names) + " |", "|" + "---|" * (len(names) + 1)]
    for i, a in enumerate(vals):
        cells = []
        for j, b in enumerate(vals):
            if i == j:
                cells.append("")
                continue
            m, lo, hi, wins, ties, losses = paired(a, b)
            mark = "▲" if lo > 0 else "▼" if hi < 0 else "="
            cells.append("%+.3f %s (%d/%d/%d)" % (m, mark, wins, ties, losses))
        lines.append("| %s | %s |" % (names[i], " | ".join(cells)))
    return lines


def verdicts(names, vals):
    """['A > B', ...]: the pairs where the 95% interval of A - B excludes zero on the side of A."""
    out = []
    for i, a in enumerate(vals):
        for j, b in enumerate(vals):
            if i != j and paired(a, b)[1] > 0:
                out.append("%s > %s" % (names[i], names[j]))
    return out


def group(runs, private=False):
    """{base: {"ceiling": [...], "by": {budget: [...]}, "hidden": {budget: {names}}}} of (record, run) pairs."""
    groups = {}
    for rec, run in runs:
        s = rec["source"]
        base = (rec["protocol"], s["name"], tuple(f["sha256"] for f in s["files"]), run["question_set"], run["chat_set"])
        g = groups.setdefault(base, {"ceiling": [], "by": {}, "hidden": {}})
        if withheld(rec, private):
            g["hidden"].setdefault(run["budget"], set()).add(rec["engine"]["name"])
        elif run["budget"] is None:
            g["ceiling"].append((rec, run))
        else:
            g["by"].setdefault(run["budget"], []).append((rec, run))
    return groups


def render(runs, private=False):
    from contextbench import plot  # late: plot reads the board's groups
    groups = group(runs, private)
    charts = plot.chart_files(plot.families(groups))
    order = sorted(groups, key=lambda k: (k[1], k[2], k[3], k[4]))
    last = {base[1]: base for base in order}
    out = []
    for base in order:
        g = groups[base]
        for budget in sorted(g["by"]) or [None]:
            ceil = _latest(g["ceiling"])
            ranked = _latest(g["by"].get(budget, []))
            if not ceil and not ranked:
                continue
            questions = any(run["summary"]["questions"] for _rec, run in ceil + ranked)
            if questions:
                ranked.sort(key=lambda it: (-it[1]["summary"]["exact"], it[0]["engine"]["name"]))
            else:
                ranked.sort(key=lambda it: it[0]["engine"]["name"])
            items = sorted(ceil, key=lambda it: it[0]["engine"]["name"]) + ranked
            rec0, run0 = items[0]
            s0 = run0["summary"]
            out += ["## %s, %s" % (rec0["source"]["name"], "budget %d tokens" % budget if budget else "no budget"), ""]
            info = "Chats: %d." % s0["chats"]
            if questions:
                info += " Questions: %d." % s0["questions"]
            info += " Requests per engine: %d. Source SHA-256: %s…." % (s0["requests"], base[2][0][:12])
            if questions:
                info += " Question set: %s…." % base[3][:12]
            cards = sorted({rec["price_card"]["date"] for rec, _run in items})
            info += " Price card: %s." % ", ".join(cards)
            out += [info, ""] + _table(items, questions) + [""]
            if len(cards) > 1:
                out += ["The price cards differ in this group: compare dollars only between rows of one card.", ""]
            if questions and len(ranked) > 1:
                names = [rec["engine"]["name"] for rec, _run in ranked]
                vals = [per_question(run) for _rec, run in ranked]
                ahead = verdicts(names, vals)
                out += ["Head to head: the mean of (row − column) exact per question. ▲ the row is ahead and ▼ behind "
                        "(the 95% interval excludes 0); = no clear difference. In brackets: questions won, tied and "
                        "lost by the row.", ""] + _matrix(names, vals) + [""]
                out += ["Clearly ahead: %s." % ("; ".join(ahead) if ahead else "no pair differs clearly"), ""]
            elif not questions:
                out += ["This source has no questions. It measures exposure and cost, not accuracy or behavior: the "
                        "board names no engine ahead.", ""]
            who = sorted({"%s (%s), %s %s, Python %s" % (r["runner"]["name"], r["runner"]["role"],
                                                         r["runner"]["platform"].split("-")[0],
                                                         r["runner"]["machine"], r["runner"]["python"])
                          for r, _run in items})
            out += ["Runner: %s." % "; ".join(who), ""]
            hidden = sorted(g["hidden"].get(budget, set()) | g["hidden"].get(None, set()))
            if hidden:
                out += ["Withheld (licensed, no publication grant): %s." % ", ".join(hidden), ""]
        if last[base[1]] == base and charts.get(base[1]):
            out += ["### Charts of %s" % base[1], ""]
            for name, caption in charts[base[1]]:
                out += ["![%s: %s](%s/%s)" % (base[1], caption, plot.DIR, name), ""]
    return "\n".join(out)
