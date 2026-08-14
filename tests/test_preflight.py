"""Tests for Tier 0 pre-flight scripts."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "preflight"))

import dead_code  # noqa: E402
import deps_check  # noqa: E402
import run_linters  # noqa: E402
import test_subversion  # noqa: E402
from test_scripts import RepoTestCase  # noqa: E402


def run_main(module, argv: list[str]) -> dict:
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", [module.__name__, *argv]):
        with contextlib.redirect_stdout(buf):
            module.main()
    return json.loads(buf.getvalue())


class TestDepsCheck(RepoTestCase):
    def _setup_repo(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file(
            "app.py",
            "import json\nimport helper\nimport requests\nimport flarghblarghe\nx = 1\n",
            "add imports", "2026-08-02T10:00:00+00:00")
        (self.repo / "helper.py").write_text("y = 2\n")
        (self.repo / "requirements.txt").write_text("requests>=2\n")

    def test_classification(self):
        self._setup_repo()
        with mock.patch.object(deps_check, "pypi_exists", return_value=False) as m:
            out = run_main(deps_check, ["--repo", str(self.repo),
                                        "--base", "HEAD~1", "--head", "HEAD"])
        # only the undeclared, non-local, non-stdlib import reaches PyPI
        m.assert_called_once_with("flarghblarghe")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "error")
        self.assertEqual(f["file"], "app.py")
        self.assertIn("flarghblarghe", f["claim"])

    def test_offline_skips_cleanly(self):
        self._setup_repo()
        with mock.patch.object(deps_check, "pypi_exists", return_value=None):
            out = run_main(deps_check, ["--repo", str(self.repo),
                                        "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])
        self.assertTrue(out["skipped"])

    def test_real_package_passes(self):
        self._setup_repo()
        (self.repo / "requirements.txt").unlink()
        with mock.patch.object(deps_check, "pypi_exists", return_value=True):
            out = run_main(deps_check, ["--repo", str(self.repo),
                                        "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])


class TestDeadCode(RepoTestCase):
    def test_unused_import_and_unreachable(self):
        self.commit_file("app.py", "def f():\n    return 1\n", "base")
        self.commit_file(
            "app.py",
            "import os\n\ndef f():\n    return 1\n    print('never')\n",
            "add dead code", "2026-08-02T10:00:00+00:00")
        out = run_main(dead_code, ["--repo", str(self.repo),
                                   "--base", "HEAD~1", "--head", "HEAD"])
        claims = [f["claim"] for f in out["findings"]]
        self.assertTrue(any("'os'" in c for c in claims), claims)
        self.assertTrue(any("unreachable" in c for c in claims), claims)

    def test_clean_diff_no_findings(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file("app.py", "import os\nx = os.sep\n", "uses os",
                         "2026-08-02T10:00:00+00:00")
        out = run_main(dead_code, ["--repo", str(self.repo),
                                   "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])


class TestSubversion(RepoTestCase):
    def test_weakened_test_flags_error_when_code_changed(self):
        self.commit_file("calc.py", "def add(a, b):\n    return a + b\n", "base code")
        self.commit_file(
            "tests/test_calc.py",
            "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
            "    assert add(0, 0) == 0\n",
            "base tests")
        self.commit_file("calc.py", "def add(a, b):\n    return a + b  # touched\n",
                         "touch code", "2026-08-02T10:00:00+00:00")
        self.commit_file(
            "tests/test_calc.py",
            "import pytest\nfrom calc import add\n\n@pytest.mark.skip\n"
            "def test_add():\n    assert add(1, 2) == 3\n",
            "weaken tests", "2026-08-02T11:00:00+00:00")
        out = run_main(test_subversion, ["--repo", str(self.repo),
                                         "--base", "HEAD~2", "--head", "HEAD"])
        kinds = {(f["severity"], f["file"]) for f in out["findings"]}
        self.assertIn(("error", "tests/test_calc.py"), kinds)   # assertions removed
        self.assertTrue(any("skip" in f["claim"] for f in out["findings"]))
        self.assertTrue(out["code_changed_in_range"])

    def test_strengthened_tests_pass(self):
        self.commit_file("tests/test_a.py", "def test_a():\n    assert True\n", "base")
        self.commit_file("tests/test_a.py",
                         "def test_a():\n    assert True\n    assert 1 + 1 == 2\n",
                         "more assertions", "2026-08-02T10:00:00+00:00")
        out = run_main(test_subversion, ["--repo", str(self.repo),
                                         "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])


class TestRunLinters(RepoTestCase):
    def test_skips_when_not_configured(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file("app.py", "x = 2\n", "change", "2026-08-02T10:00:00+00:00")
        out = run_main(run_linters, ["--repo", str(self.repo),
                                     "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["skipped"][0]["reason"], "no ruff config found in repo")


if __name__ == "__main__":
    unittest.main()
