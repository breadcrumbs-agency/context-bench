import copy
import hashlib
import json
import os
import pathlib
import tempfile
import unittest

from contextbench import sources
from support import ROOT, read


def entry(path, name="f.bin", rev="r" * 40):
    data = read(path, "rb")
    return {"file": name, "url": pathlib.Path(path).as_uri(), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)}


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.rev = "c" * 40
        os.makedirs(os.path.join(self.dir, self.rev))
        self.src = os.path.join(self.dir, self.rev, "f.bin")  # the url names the revision, as a pinned url does
        with open(self.src, "wb") as f:
            f.write(b"pinned bytes" * 1000)
        self.entry = entry(self.src)
        self.manifest = {"name": "toy", "license": "MIT", "homepage": "https://example.org", "revision": self.rev,
                         "files": [self.entry], "size": self.entry["bytes"], "about": "a toy"}

    def tamper(self):
        with open(self.src, "ab") as f:
            f.write(b"!")


class TestVerify(Files):
    def test_the_pinned_bytes_pass(self):
        sources.verify(self.src, self.entry)

    def test_other_bytes_of_the_same_size_are_refused(self):
        with open(self.src, "r+b") as f:
            f.write(b"X")
        with self.assertRaises(ValueError) as cm:
            sources.verify(self.src, self.entry)
        self.assertIn("SHA-256", str(cm.exception))

    def test_another_size_is_refused_first(self):
        self.tamper()
        with self.assertRaises(ValueError) as cm:
            sources.verify(self.src, self.entry)
        self.assertIn("bytes", str(cm.exception))


class TestFetch(Files):
    def test_fetch_downloads_and_checks(self):
        into = os.path.join(self.dir, "cache")
        got = sources.fetch(self.manifest, into)
        self.assertEqual(got, {"f.bin": os.path.join(into, "f.bin")})
        self.assertEqual(read(got["f.bin"], "rb"), read(self.src, "rb"))
        self.assertEqual(os.listdir(into), ["f.bin"])  # no .part file is left

    def test_a_download_with_another_hash_is_refused_and_removed(self):
        into = os.path.join(self.dir, "cache")
        with open(self.src, "r+b") as f:  # the server's file changed after the pin: same size, other bytes
            f.write(b"X")
        with self.assertRaises(ValueError):
            sources.fetch(self.manifest, into)
        self.assertEqual(os.listdir(into), [])

    def test_a_file_that_is_there_must_be_the_pinned_one(self):
        into = os.path.join(self.dir, "cache")
        os.makedirs(into)
        with open(os.path.join(into, "f.bin"), "wb") as f:
            f.write(b"wrong")
        with self.assertRaises(ValueError):
            sources.fetch(self.manifest, into)
        self.assertEqual(read(os.path.join(into, "f.bin"), "rb"), b"wrong")  # never overwritten
        os.replace(self.src, os.path.join(into, "f.bin"))
        self.assertEqual(sources.fetch(self.manifest, into)["f.bin"], os.path.join(into, "f.bin"))  # no download

    def test_locate_checks_a_local_copy_against_the_pin(self):
        self.assertEqual(sources.locate(self.manifest, self.src), {"f.bin": self.src})
        self.assertEqual(sources.locate(self.manifest, os.path.dirname(self.src)), {"f.bin": self.src})
        self.tamper()
        with self.assertRaises(ValueError):
            sources.locate(self.manifest, self.src)

    def test_a_source_with_two_files_needs_a_folder(self):
        two = copy.deepcopy(self.manifest)
        two["files"].append(dict(self.entry, file="g.bin"))
        two["size"] *= 2
        with self.assertRaises(ValueError):
            sources.locate(two, self.src)

    def test_the_cache_folder_follows_the_environment(self):
        old = os.environ.get("CONTEXTBENCH_DATA")
        os.environ["CONTEXTBENCH_DATA"] = self.dir
        try:
            self.assertEqual(sources.cache_dir("toy"), os.path.join(self.dir, "toy"))
        finally:
            if old is None:
                del os.environ["CONTEXTBENCH_DATA"]
            else:
                os.environ["CONTEXTBENCH_DATA"] = old


class TestManifest(Files):
    def test_a_good_manifest_passes(self):
        sources.check_manifest(self.manifest)

    def test_bad_manifests_are_refused(self):
        def bad(**kw):
            m = copy.deepcopy(self.manifest)
            m.update(kw)
            return m
        e = self.entry
        cases = [bad(files=[]), bad(size=1), bad(revision="d" * 40), bad(license=3),
                 bad(files=[dict(e, sha256="abc")]), bad(files=[dict(e, file="a/b.bin")]),
                 bad(files=[dict(e, bytes="1")])]
        for m in cases:
            with self.assertRaises(ValueError):
                sources.check_manifest(m)


class TestShippedSources(unittest.TestCase):
    PINS = {
        "locomo": ("cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc", "locomo10.json", 2805274,
                   "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4", "CC-BY-NC-4.0"),
        "longmemeval-s": ("98d7416c24c778c2fee6e6f3006e7a073259d48f", "longmemeval_s_cleaned.json", 277383467,
                          "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442", "MIT"),
        "codex-sessions": ("69019b2c717fe7be8f4fab16b317e4b8bbcf831b", "trajectories.train.jsonl", 41158544,
                           "62c62865676427a0b3cf373741fedad384fdd6ca3da8f652205b1e9483b2dc2e", "MIT"),
    }

    def test_each_source_is_pinned_to_its_revision_and_bytes(self):
        self.assertEqual(sorted(os.listdir(os.path.join(ROOT, "sources"))), sorted(self.PINS))
        for name, (rev, file, size, sha, lic) in self.PINS.items():
            m, loader, digest = sources.load_source(os.path.join(ROOT, "sources", name))
            self.assertEqual((m["revision"], m["size"], m["license"]), (rev, size, lic), name)
            self.assertEqual([(f["file"], f["bytes"], f["sha256"]) for f in m["files"]], [(file, size, sha)])
            self.assertEqual(loader.FILE, file)
            self.assertEqual(len(digest), 64)

    def test_the_repository_holds_no_source_text(self):
        for name in self.PINS:
            for file in os.listdir(os.path.join(ROOT, "sources", name)):
                self.assertLess(os.path.getsize(os.path.join(ROOT, "sources", name, file)), 20000, file)
        with open(os.path.join(ROOT, "sources", "locomo", "source.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["about", "files", "homepage", "license", "name", "revision", "size"])


if __name__ == "__main__":
    unittest.main()
