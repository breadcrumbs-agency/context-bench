"""Mutation pass: change one guard string of the harness at a time, in a scratch copy of the tree, and expect the
tests to fail. A mutant that lives needs a test.

    python3.14 tools/mutate.py [PYTHON]

The copy skips .git, results and __pycache__. Each mutant must change exactly one place (the old string must occur
once). The pass exits 1 when a mutant lives or when a string is not unique.
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTANTS = [
    # (label, file, old string, new string)
    ("framing bytes of a message", "contextbench/meter.py", "FRAME = 16", "FRAME = 15"),
    ("cache-write price in token units", "contextbench/meter.py", "CACHE_READ, CACHE_WRITE = 0.1, 1.25",
     "CACHE_READ, CACHE_WRITE = 0.1, 1.0"),
    ("Haiku step: 100,000 is inclusive", "contextbench/meter.py", 'size <= tier["up_to"]', 'size < tier["up_to"]'),
    ("dollars use the write price", "contextbench/meter.py", '(size - hit) * tier["cache_write"]',
     '(size - hit) * tier["input"]'),
    ("over budget: equal is not over", "contextbench/meter.py", "size > self.budget:", "size >= self.budget:"),
    ("the question is not context", "contextbench/meter.py", "for m in out[:-1]))", "for m in out))"),
    ("the newest message check", "contextbench/meter.py", ".endswith(message_text(newest))",
     ".startswith(message_text(newest))"),
    ("cache prefix stops one early", "contextbench/meter.py", "while n < top and", "while n < top - 1 and"),
    ("exact divides by distinct gold", "contextbench/meter.py", "sum(found.values()) / len(gold)",
     "sum(found.values()) / len(found)"),
    ("request is the chat up to the message", "contextbench/run.py", "request = chat.messages[:i + 1]",
     "request = chat.messages[:i]"),
    ("state is not restored", "contextbench/run.py", "eng.restore(saved)", "pass"),
    ("state is not taken", "contextbench/run.py", "saved = eng.state() if chat.questions else None", "saved = None"),
    ("the question is an assistant message", "contextbench/run.py", 'asked = {"role": "user", "content": q.text}',
     'asked = {"role": "assistant", "content": q.text}'),
    ("a ceiling engine gets a budget", "contextbench/run.py", 'ceiling = bool(getattr(adapter, "ceiling", False))',
     "ceiling = False"),
    ("interval width", "contextbench/board.py", "half = 1.96 *", "half = 1.64 *"),
    ("ahead when the interval touches zero", "contextbench/board.py", 'mark = "▲" if lo > 0', 'mark = "▲" if lo >= 0'),
    ("verdict when the interval touches zero", "contextbench/board.py", "paired(a, b)[1] > 0", "paired(a, b)[1] >= 0"),
    ("group ignores the source bytes", "contextbench/board.py", 'tuple(f["sha256"] for f in s["files"]), run',
     '(), run'),
    ("group ignores the budget", "contextbench/board.py", 'g["by"].setdefault(run["budget"], [])',
     'g["by"].setdefault(0, [])'),
    ("the older run wins", "contextbench/board.py", 'rec["runner"]["date"] > out[k][0]["runner"]["date"]',
     'rec["runner"]["date"] < out[k][0]["runner"]["date"]'),
    ("source: another hash passes", "contextbench/sources.py", 'if got != entry["sha256"]:',
     'if got == entry["sha256"]:'),
    ("source: another size passes", "contextbench/sources.py", 'if size != entry["bytes"]:',
     'if size == entry["bytes"]:'),
    ("engine: another version passes", "contextbench/engine.py", 'if have != self.manifest["version"]:',
     'if have == self.manifest["version"]:'),
    ("engine: another commit passes", "contextbench/engine.py", "if commit != pin:", "if commit == pin:"),
    ("agent requests: run of tool results", "contextbench/chats.py", 'msgs[i + 1].get("role") != "tool"',
     'msgs[i + 1].get("role") == "tool"'),
    ("chat_of: the first speaker is the user", "contextbench/chats.py",
     'role = "user" if speaker == first else "assistant"', 'role = "assistant" if speaker == first else "user"'),
    ("longmemeval: abstention questions stay", "sources/longmemeval-s/loader.py", '"_abs" not in x["question_id"]',
     '"_abs" in x["question_id"]'),
    ("longmemeval: gold in any session", "sources/longmemeval-s/loader.py", '"answer" in sid and',
     '"answer" not in sid and'),
    ("locomo: category 5 has an answer", "sources/locomo/loader.py", 'answer = None if cat == 5 else q.get("answer", "")',
     'answer = q.get("answer", "")'),
]


def main():
    py = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    alive, bad = [], []
    with tempfile.TemporaryDirectory(prefix="cb-mutants-") as scratch:
        control = os.path.join(scratch, "control")  # the copy with no change must pass, or a failure means nothing
        shutil.copytree(ROOT, control, ignore=shutil.ignore_patterns(".git", "results", "__pycache__"))
        r = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=control, env=env,
                           capture_output=True, text=True, timeout=300)
        print("control (no change): %s" % ("passes" if r.returncode == 0 else "FAILS"))
        if r.returncode != 0:
            print(r.stderr[-2000:])
            return 1
        shutil.rmtree(control)
        for i, (label, rel, old, new) in enumerate(MUTANTS):
            tree = os.path.join(scratch, "m%d" % i)
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "results", "__pycache__"))
            path = os.path.join(tree, rel)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if src.count(old) != 1:
                bad.append(label)
                print("NOT UNIQUE (%d): %s" % (src.count(old), label))
                shutil.rmtree(tree)
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new))
            r = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=tree, env=env,
                               capture_output=True, text=True, timeout=300)
            killed = r.returncode != 0
            print("%-6s %s" % ("killed" if killed else "LIVES", label), flush=True)
            if not killed:
                alive.append(label)
            shutil.rmtree(tree)
    total = len(MUTANTS)
    print("mutants killed %d of %d" % (total - len(alive) - len(bad), total))
    return 1 if alive or bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
