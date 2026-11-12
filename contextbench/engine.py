"""The contract between the harness and a context engine.

A donor writes one file, engines/<name>/adapter.py, with one class named Adapter: a subclass of Engine.
For each chat (and each budget) the harness makes a new Adapter in an empty folder and calls select() once for each
request of the chat, in order, as the chat is sent. When the chat has questions, they come after its last request:
the harness takes state() once, then for each question it calls select() with the question as the new user
message, and restore()s the state. The engine never gets an answer, a gold turn or a score.
"""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re

FIELDS = {"name": str, "version": str, "license": str, "access": str, "homepage": str, "source": str, "about": str,
          "models": list, "runs_on": str, "tuned_on": list}
ACCESS = ("open-source", "licensed")
RUNS_ON = ("mac", "kali")
GRANT = ("granted_by", "date", "evidence")
PIN_PIP = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")
PIN_GIT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*) @ git\+https://\S+@([0-9a-f]{40})")
IN_REPO = "in-repo: "


def parse_source(source):
    """The exact pin in engine.json 'source': ('pip', dist, version), ('git', dist, commit) or ('repo', None, None)."""
    if source.startswith(IN_REPO):
        return "repo", None, None
    m = PIN_PIP.fullmatch(source)
    if m:
        return "pip", m.group(1), m.group(2)
    m = PIN_GIT.fullmatch(source)
    if m:
        return "git", m.group(1), m.group(2)
    raise ValueError("source %r is not an exact pin: use 'dist==version', 'dist @ git+https://...@<40 hex>' "
                     "or '%s<path>'" % (source, IN_REPO))


def check_manifest(m, where="engine.json"):
    for key, kind in FIELDS.items():
        if not isinstance(m.get(key), kind):
            raise ValueError("%s: %r must be a %s" % (where, key, kind.__name__))
    if m["access"] not in ACCESS:
        raise ValueError("%s: access must be one of %s" % (where, ", ".join(ACCESS)))
    if m["runs_on"] not in RUNS_ON:
        raise ValueError("%s: runs_on must be one of %s" % (where, ", ".join(RUNS_ON)))
    grant = m.get("publication")
    if grant is not None and not (isinstance(grant, dict) and all(isinstance(grant.get(k), str) and grant[k]
                                                                  for k in GRANT)):
        raise ValueError("%s: publication must be null or an object with %s" % (where, ", ".join(GRANT)))
    kind, _dist, pin = parse_source(m["source"])
    if kind == "pip" and pin != m["version"]:
        raise ValueError("%s: source pins %s, but version is %s" % (where, pin, m["version"]))


def load_manifest(folder):
    with open(os.path.join(folder, "engine.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    check_manifest(manifest, os.path.join(folder, "engine.json"))
    return manifest


def load_engine(folder):
    """(manifest, Adapter class, SHA-256 of adapter.py) of an engine folder."""
    manifest = load_manifest(folder)
    if manifest["name"] != os.path.basename(os.path.normpath(folder)):
        raise ValueError("%s: name %r is not the folder name" % (folder, manifest["name"]))
    path = os.path.join(folder, "adapter.py")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    spec = importlib.util.spec_from_file_location("contextbench_engine_" + manifest["name"].replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = getattr(module, "Adapter", None)
    if not (isinstance(adapter, type) and issubclass(adapter, Engine)):
        raise TypeError("%s: Adapter must be a subclass of contextbench.Engine" % path)
    return manifest, adapter, digest


class Engine:
    """The base class of an adapter. A subclass implements select; state, restore, stats and close are optional."""

    ceiling = False  # True: the engine has no budget, and the board shows it as the ceiling row, with no rank

    def __init__(self, workdir, manifest, budget):
        self.workdir = workdir  # empty; keep every file of the engine here, the harness deletes it
        self.manifest = manifest  # the parsed engine.json
        self.budget = budget  # tokens, as the harness counts them; None for a ceiling engine

    def require(self):
        """Compare the installed package with the exact pin of engine.json, and raise on a mismatch, so that a record
        cannot name a version that did not run. A git pin also needs the installed commit to be the pinned one."""
        kind, dist, pin = parse_source(self.manifest["source"])
        if kind == "repo":
            return None
        try:
            have = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeError("%s is not installed: run 'python -m contextbench fetch engines/%s --venv DIR' and "
                               "run with DIR/bin/python" % (dist, self.manifest["name"])) from None
        if have != self.manifest["version"]:
            raise RuntimeError("%s %s is installed, but engine.json pins %s" % (dist, have, self.manifest["version"]))
        if kind == "git":
            raw = importlib.metadata.distribution(dist).read_text("direct_url.json")
            commit = ((json.loads(raw).get("vcs_info") or {}).get("commit_id") if raw else None)
            if commit != pin:
                raise RuntimeError("%s was installed from commit %s, but engine.json pins %s" % (dist, commit, pin))
        return have

    def select(self, request_messages, budget):
        """The messages to send for one request. request_messages is the whole chat so far, the system message
        first and the newest message last; return a list of message dicts. The harness does not cut the list: it
        records the largest request, the requests over the budget and the tokens over it."""
        raise NotImplementedError

    def state(self) -> "dict | None":
        """The state to restore after each question, or None for an engine with no state between requests. The
        harness takes it once, after the last request of the chat."""
        return None

    def restore(self, state):
        """Put back what state() returned, so that each question sees the end-of-chat state."""

    def stats(self):
        """Counters of the engine for the record, for example {'folds': 3}. Numbers only."""
        return {}

    def close(self):
        """Release files and connections. The harness then deletes workdir."""
