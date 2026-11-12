import importlib.metadata
import json
import os
import tempfile
import unittest
from unittest import mock

from contextbench import engine
from support import manifest

SHA = "5" * 40


class TestSourcePins(unittest.TestCase):
    def test_exact_pins(self):
        self.assertEqual(engine.parse_source("langchain-core==1.8.0"), ("pip", "langchain-core", "1.8.0"))
        self.assertEqual(engine.parse_source("breadcrumbs @ git+https://github.com/x/y@" + SHA),
                         ("git", "breadcrumbs", SHA))
        self.assertEqual(engine.parse_source("in-repo: engines/window/adapter.py"), ("repo", None, None))

    def test_loose_pins_are_refused(self):
        for bad in ("langchain-core", "langchain-core>=1.8", "langchain-core~=1.8.0", "x @ git+https://h/y@main",
                    "x @ git+https://h/y@" + "5" * 39, "x @ git+https://h/y@" + "A" * 40, "x==1 y==2", ""):
            with self.assertRaises(ValueError, msg=bad):
                engine.parse_source(bad)


class TestManifest(unittest.TestCase):
    def test_a_good_manifest_passes(self):
        engine.check_manifest(manifest("x"))
        engine.check_manifest(manifest("x", source="lib==2.0", version="2.0"))

    def test_bad_manifests_are_refused(self):
        for bad in ({"access": "closed"}, {"runs_on": "cloud"}, {"models": "none"}, {"version": 1}, {"source": "lib"},
                    {"source": "lib==2.0", "version": "2.1"},  # the pin and the version must agree
                    {"access": "licensed", "publication": {"granted_by": "Vendor"}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                engine.check_manifest(manifest("x", **bad))
        m = manifest("x")
        del m["about"]
        with self.assertRaises(ValueError):
            engine.check_manifest(m)

    def test_a_licensed_engine_may_carry_a_grant(self):
        engine.check_manifest(manifest("x", access="licensed", publication={
            "granted_by": "Vendor Inc.", "date": "2026-11-11", "evidence": "https://example.org/grant"}))

    def test_the_folder_name_is_the_engine_name(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "other")
            os.makedirs(folder)
            with open(os.path.join(folder, "engine.json"), "w") as f:
                json.dump(manifest("x"), f)
            with open(os.path.join(folder, "adapter.py"), "w") as f:
                f.write("import contextbench\nclass Adapter(contextbench.Engine):\n    pass\n")
            with self.assertRaises(ValueError):
                engine.load_engine(folder)
            os.rename(folder, os.path.join(d, "x"))
            self.assertEqual(engine.load_engine(os.path.join(d, "x"))[0]["name"], "x")

    def test_the_adapter_must_be_an_engine(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "x")
            os.makedirs(folder)
            with open(os.path.join(folder, "engine.json"), "w") as f:
                json.dump(manifest("x"), f)
            with open(os.path.join(folder, "adapter.py"), "w") as f:
                f.write("class Adapter:\n    pass\n")
            with self.assertRaises(TypeError):
                engine.load_engine(folder)


class FakeDist:
    def __init__(self, direct_url):
        self.direct_url = direct_url

    def read_text(self, name):
        return self.direct_url if name == "direct_url.json" else None


class TestVersionCheck(unittest.TestCase):
    """At init an adapter compares the installed version with engine.json, and raises on a mismatch."""

    def make(self, **kw):
        return engine.Engine("work", manifest("lib-engine", **kw), 1000)

    def test_the_installed_version_must_be_the_pinned_one(self):
        eng = self.make(source="demo-lib==2.0", version="2.0")
        with mock.patch("importlib.metadata.version", return_value="2.0") as v:
            self.assertEqual(eng.require(), "2.0")
            v.assert_called_with("demo-lib")
        with mock.patch("importlib.metadata.version", return_value="2.0.1"):
            with self.assertRaises(RuntimeError) as cm:
                eng.require()
            self.assertIn("2.0.1", str(cm.exception))
            self.assertIn("pins 2.0", str(cm.exception))

    def test_a_missing_package_says_how_to_install_it(self):
        eng = self.make(source="demo-lib==2.0", version="2.0")
        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            with self.assertRaises(RuntimeError) as cm:
                eng.require()
        self.assertIn("fetch engines/lib-engine", str(cm.exception))

    def test_a_git_pin_needs_the_pinned_commit(self):
        eng = self.make(source="demo @ git+https://example.org/r@" + SHA, version="0.4.1")
        good = FakeDist(json.dumps({"vcs_info": {"commit_id": SHA, "vcs": "git"}}))
        other = FakeDist(json.dumps({"vcs_info": {"commit_id": "6" * 40, "vcs": "git"}}))
        not_git = FakeDist(json.dumps({"url": "file:///x"}))
        with mock.patch("importlib.metadata.version", return_value="0.4.1"):
            with mock.patch("importlib.metadata.distribution", return_value=good):
                self.assertEqual(eng.require(), "0.4.1")
            for dist in (other, not_git, FakeDist(None)):
                with mock.patch("importlib.metadata.distribution", return_value=dist):
                    with self.assertRaises(RuntimeError):
                        eng.require()
        with mock.patch("importlib.metadata.version", return_value="0.4.2"):  # the same commit, another version
            with mock.patch("importlib.metadata.distribution", return_value=good):
                with self.assertRaises(RuntimeError):
                    eng.require()

    def test_an_in_repo_engine_has_nothing_to_check(self):
        with mock.patch("importlib.metadata.version", side_effect=AssertionError):
            self.assertIsNone(self.make().require())


if __name__ == "__main__":
    unittest.main()
