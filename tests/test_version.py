import re
import subprocess
import sys
import unittest
from pathlib import Path

from jukebox import __version__
from tests.test_engine import make_engine

ROOT = Path(__file__).resolve().parent.parent


class VersionTest(unittest.TestCase):
    def test_version_looks_like_major_minor_patch(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_page_and_jukebox_versions_match(self):
        page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        match = re.search(r"var PAGE_VERSION = '([^']+)'", page)
        self.assertIsNotNone(match, "the page has no PAGE_VERSION")
        self.assertEqual(match.group(1), __version__, "static/index.html and jukebox/__init__.py disagree")

    def test_readme_and_changelog_mention_the_current_version(self):
        self.assertIn(f"Version {__version__}", (ROOT / "README.md").read_text(encoding="utf-8"))
        first = next(line for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines() if line.startswith("## "))
        self.assertIn(__version__, first)

    def test_run_py_prints_the_version(self):
        out = subprocess.run([sys.executable, str(ROOT / "run.py"), "--version"], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn(__version__, out.stdout)

    def test_only_the_host_is_told_the_version(self):
        engine, _, _ = make_engine()
        self.assertEqual(engine.state_for("guest-aaaa", True)["version"], __version__)
        self.assertNotIn("version", engine.state_for("guest-aaaa", False))


if __name__ == "__main__":
    unittest.main()
