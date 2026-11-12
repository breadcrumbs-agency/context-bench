"""python -m contextbench fetch | run | board | plot"""

import argparse
import datetime
import json
import os
import subprocess
import sys

from contextbench import board, engine, plot, sources
from contextbench import run as runner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_record(rec, path):
    """The record as indented JSON, with one compact line for each chat and each question (small files, readable
    diffs)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    marks, head = {}, json.loads(json.dumps(rec))
    for i, run in enumerate(head["runs"]):
        for key in ("chats", "questions", "trace"):
            if key not in run:
                continue  # a record from before the trace
            mark = "@@%s-%d@@" % (key, i)
            lines = ",\n".join("    " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in run[key])
            marks['"%s"' % mark] = "[\n%s\n   ]" % lines if lines else "[]"
            run[key] = mark
    text = json.dumps(head, indent=1, ensure_ascii=False)
    for mark, body in marks.items():
        text = text.replace(mark, body)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


def _folder(arg, kind):
    """A folder given as a path, or as a bare name of engines/ or sources/."""
    if os.path.isdir(arg):
        return arg
    named = os.path.join(ROOT, kind, arg)
    if os.path.isdir(named):
        return named
    raise SystemExit("no folder %s (also not %s)" % (arg, named))


def _progress(done, chat_id):
    print("  %d chats (last: %s)" % (done, chat_id), file=sys.stderr, flush=True)


def fetch_engine(folder, venv, python):
    """Install the pinned requirements of an engine into a scratch venv; print the venv's Python."""
    engine.load_manifest(folder)
    with open(os.path.join(folder, "requirements.txt"), encoding="utf-8") as f:
        pins = [x for x in (line.strip() for line in f) if x and not x.startswith("#")]
    if not pins:
        print("%s has no requirements: it runs with any Python from 3.11" % folder)
        return 0
    if not venv:
        raise SystemExit("%s has requirements: pass --venv DIR" % folder)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    subprocess.run([python or sys.executable, "-m", "venv", venv], check=True, env=env)
    py = os.path.join(venv, "bin", "python")
    subprocess.run([py, "-m", "pip", "install", "-r", os.path.join(folder, "requirements.txt")], check=True, env=env)
    print(py)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="contextbench", description="Compare context engines head to head.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="install an engine's pinned requirements, or download a source's pinned files")
    f.add_argument("target", help="engines/<name> or sources/<name>")
    f.add_argument("--venv", help="engine: the scratch venv to make, with the pinned requirements")
    f.add_argument("--python", help="engine: the Python that makes the venv (default: this one)")
    f.add_argument("--into", help="source: the folder for the files (default: the cache)")

    r = sub.add_parser("run", help="run one engine on one source and write one run record")
    r.add_argument("engine", help="an engine folder, for example engines/window")
    r.add_argument("--source", required=True, help="a source name or folder, for example locomo")
    r.add_argument("--data", help="a local copy of the source file or folder (it must have the pinned SHA-256)")
    r.add_argument("--budgets", type=int, nargs="+", help="token budgets (default: 4096 8192 16384)")
    r.add_argument("--limit", type=int, default=0, help="the first N chats only; for longmemeval-s, N questions "
                                                         "by an even stride (0 = all)")
    r.add_argument("--runner", default="unnamed", help="who runs it")
    r.add_argument("--role", default="other", choices=runner.ROLES)
    r.add_argument("--card", help="a price card file (default: the newest in prices/)")
    r.add_argument("--out", help="the record file (default: results/private/<source>-<engine>-<time>.json)")

    b = sub.add_parser("board", help="write the board of a set of run records")
    b.add_argument("paths", nargs="*", help="record files or folders (default: results/public)")
    b.add_argument("--private", action="store_true", help="also read results/private and show withheld runs")
    b.add_argument("--out", help="write the board to this file instead of stdout")

    c = sub.add_parser("plot", help="write the charts of a set of run records as SVG files")
    c.add_argument("paths", nargs="*", help="record files or folders (default: results/public)")
    c.add_argument("--private", action="store_true", help="also read results/private and chart withheld runs")
    c.add_argument("--out", default="charts", help="the folder for the SVG files (default: charts)")

    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        if os.path.basename(os.path.dirname(os.path.normpath(a.target))) == "sources" or os.path.exists(
                os.path.join(a.target, "source.json")):
            manifest = sources.load_manifest(_folder(a.target, "sources"))
            for name, path in sources.fetch(manifest, a.into or sources.cache_dir(manifest["name"])).items():
                print(path)
            return 0
        return fetch_engine(_folder(a.target, "engines"), a.venv, a.python)
    if a.cmd == "run":
        from contextbench import meter
        efolder, sfolder = _folder(a.engine, "engines"), _folder(a.source, "sources")
        card = meter.load_card(a.card) if a.card else None
        rec = runner.run(efolder, sfolder, a.data, a.budgets, a.limit, a.runner, a.role, card, _progress)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = a.out or os.path.join("results", "private", "%s-%s-%s.json" % (
            rec["source"]["name"], rec["engine"]["name"], stamp))
        write_record(rec, out)
        for run in rec["runs"]:
            s = run["summary"]
            print(json.dumps({"out": out, "budget": run["budget"], **{k: v for k, v in s.items() if k not in (
                "by_category", "questions_by_category")}}))
        return 0
    paths = a.paths or ["results/public"]
    if a.private and os.path.isdir("results/private"):
        paths.append("results/private")
    if a.cmd == "plot":
        files = plot.build(board.load([p for p in paths if os.path.exists(p)]), a.private)
        if not files:
            raise SystemExit("no run records with budgets under %s: nothing to chart" % ", ".join(paths))
        for path in plot.write(files, a.out):
            print(path)
        return 0
    text = board.render(board.load([p for p in paths if os.path.exists(p)]), a.private)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(board.HEAD + text + "\n")
    else:
        print(board.HEAD + text)
    return 0
