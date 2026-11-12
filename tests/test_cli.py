import contextlib
import io
import json
import os
import pathlib
import shutil
import unittest

from contextbench import cli
from support import ROOT, Fixture, read


def quiet(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(args))
    return code, out.getvalue()


class TestFetchCommand(Fixture):
    def test_an_engine_with_no_requirements_needs_no_venv(self):
        code, out = quiet("fetch", os.path.join(ROOT, "engines", "window"))
        self.assertEqual(code, 0)
        self.assertIn("no requirements", out)

    def test_an_engine_with_requirements_asks_for_a_venv_folder(self):
        with self.assertRaises(SystemExit) as cm:
            quiet("fetch", os.path.join(ROOT, "engines", "langchain-trim"))
        self.assertIn("--venv DIR", str(cm.exception))

    def test_a_source_is_downloaded_into_a_folder_and_checked(self):
        rev = "a" * 40
        pinned = os.path.join(self.dir, "mirror", rev)
        os.makedirs(pinned)
        shutil.copy(self.data, os.path.join(pinned, "toy.json"))
        path = os.path.join(self.source, "source.json")
        with open(path) as f:
            manifest = json.load(f)
        manifest["files"][0]["url"] = pathlib.Path(pinned, "toy.json").as_uri()
        with open(path, "w") as f:
            json.dump(manifest, f)
        into = os.path.join(self.dir, "got")
        code, out = quiet("fetch", self.source, "--into", into)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), os.path.join(into, "toy.json"))
        self.assertEqual(read(os.path.join(into, "toy.json"), "rb"), read(self.data, "rb"))

    def test_an_unknown_folder_stops(self):
        with self.assertRaises(SystemExit):
            quiet("fetch", "engines/no-such-engine")


if __name__ == "__main__":
    unittest.main()
