"""The sources: what an engine is tested with, each pinned to one revision and to the SHA-256 of each file.

The repository holds no source text. fetch() downloads each file from its URL at the pinned revision, and refuses a
file whose size or SHA-256 is not the pinned one, so every run reads the same bytes. A source folder holds
source.json and loader.py. The loader has one function, chats(files, limit=0): files maps each file name of the
manifest to a local path, and the function yields contextbench.Chat objects in a fixed order.
"""

import hashlib
import importlib.util
import json
import os
import re
import urllib.request

FIELDS = {"name": str, "license": str, "homepage": str, "revision": str, "files": list, "size": int, "about": str}
FILE_FIELDS = {"file": str, "url": str, "sha256": str, "bytes": int}


def check_manifest(m, where="source.json"):
    for key, kind in FIELDS.items():
        if not isinstance(m.get(key), kind):
            raise ValueError("%s: %r must be a %s" % (where, key, kind.__name__))
    if not m["files"]:
        raise ValueError("%s: files is empty" % where)
    total = 0
    for f in m["files"]:
        for key, kind in FILE_FIELDS.items():
            if not isinstance(f.get(key), kind):
                raise ValueError("%s: file entry needs %r as a %s" % (where, key, kind.__name__))
        if not re.fullmatch(r"[0-9a-f]{64}", f["sha256"]):
            raise ValueError("%s: sha256 of %s must be 64 hex digits" % (where, f["file"]))
        if os.path.basename(f["file"]) != f["file"]:
            raise ValueError("%s: file %r must be a plain file name" % (where, f["file"]))
        if m["revision"] not in f["url"]:
            raise ValueError("%s: the url of %s must name the pinned revision %s" % (where, f["file"], m["revision"]))
        total += f["bytes"]
    if total != m["size"]:
        raise ValueError("%s: size %d is not the sum of the files (%d)" % (where, m["size"], total))


def load_manifest(folder):
    with open(os.path.join(folder, "source.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    check_manifest(manifest, os.path.join(folder, "source.json"))
    return manifest


def load_source(folder):
    """(manifest, loader module, SHA-256 of loader.py) of a source folder."""
    manifest = load_manifest(folder)
    if manifest["name"] != os.path.basename(os.path.normpath(folder)):
        raise ValueError("%s: name %r is not the folder name" % (folder, manifest["name"]))
    path = os.path.join(folder, "loader.py")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    spec = importlib.util.spec_from_file_location("contextbench_source_" + manifest["name"].replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "chats", None)):
        raise TypeError("%s: loader.py must define chats(files, limit=0)" % path)
    return manifest, module, digest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path, entry):
    """Raise ValueError when the file at path is not the pinned one (size first, then SHA-256)."""
    size = os.path.getsize(path)
    if size != entry["bytes"]:
        raise ValueError("%s: %d bytes, but the pin says %d (%s)" % (path, size, entry["bytes"], entry["file"]))
    got = sha256_file(path)
    if got != entry["sha256"]:
        raise ValueError("%s: SHA-256 %s is not the pinned %s (%s)" % (path, got, entry["sha256"], entry["file"]))


def cache_dir(name):
    if os.environ.get("CONTEXTBENCH_DATA"):
        return os.path.join(os.environ["CONTEXTBENCH_DATA"], name)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "context-bench", name)


def fetch(manifest, into):
    """Download each file of the manifest into the folder 'into', checked against its pin. A file that is there
    must already be the pinned one. Returns {file name: path}."""
    os.makedirs(into, exist_ok=True)
    out = {}
    for entry in manifest["files"]:
        path = os.path.join(into, entry["file"])
        if os.path.exists(path):
            verify(path, entry)
            out[entry["file"]] = path
            continue
        part = path + ".part"
        h, n = hashlib.sha256(), 0
        try:
            with urllib.request.urlopen(entry["url"], timeout=60) as r, open(part, "wb") as f:
                for block in iter(lambda: r.read(1 << 20), b""):
                    h.update(block)
                    n += len(block)
                    f.write(block)
            if n != entry["bytes"] or h.hexdigest() != entry["sha256"]:
                raise ValueError("%s: %d bytes with SHA-256 %s are not the pin (%d bytes, %s)" % (
                    entry["url"], n, h.hexdigest(), entry["bytes"], entry["sha256"]))
            os.replace(part, path)
        finally:
            if os.path.exists(part):
                os.remove(part)
        out[entry["file"]] = path
    return out


def locate(manifest, data=None):
    """{file name: path} of the pinned files: a local copy (a file, or a folder with the files), else the cache,
    where fetch() puts them. Every file is checked against its pin."""
    if not data:
        return fetch(manifest, cache_dir(manifest["name"]))
    out = {}
    if os.path.isdir(data):
        for entry in manifest["files"]:
            out[entry["file"]] = os.path.join(data, entry["file"])
    elif len(manifest["files"]) == 1:
        out[manifest["files"][0]["file"]] = data
    else:
        raise ValueError("%s has %d files: --data must be a folder" % (manifest["name"], len(manifest["files"])))
    for entry in manifest["files"]:
        verify(out[entry["file"]], entry)
    return out
