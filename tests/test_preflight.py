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
import osv_check  # noqa: E402
import run_linters  # noqa: E402
import secrets_scan  # noqa: E402
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

    def test_eslint_skips_without_config(self):
        self.commit_file("app.js", "const x = 1;\n", "base")
        self.commit_file("app.js", "const x = 2;\n", "change",
                         "2026-08-02T10:00:00+00:00")
        out = run_main(run_linters, ["--repo", str(self.repo),
                                     "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertTrue(any("no eslint config" in r for r in reasons), reasons)

    def test_eslint_skips_without_local_install(self):
        self.commit_file("app.js", "const x = 1;\n", "base")
        self.commit_file("eslint.config.js", "export default [];\n", "add config",
                         "2026-08-02T09:00:00+00:00")
        self.commit_file("app.js", "const x = 2;\n", "change",
                         "2026-08-02T10:00:00+00:00")
        out = run_main(run_linters, ["--repo", str(self.repo),
                                     "--base", "HEAD~1", "--head", "HEAD"])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertTrue(any("no local install" in r for r in reasons), reasons)


class TestSecretsScan(RepoTestCase):
    def test_planted_aws_key_is_error_and_redacted(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file(
            "config.py",
            'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
            "add config", "2026-08-02T10:00:00+00:00")
        out = run_main(secrets_scan, ["--repo", str(self.repo),
                                      "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "error")
        self.assertEqual(f["category"], "security")
        self.assertEqual(f["file"], "config.py")
        evidence = " ".join(f["evidence"])
        self.assertIn("AKIA…", evidence)                       # redacted prefix shown
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", evidence)     # full secret never shown

    def test_placeholder_and_env_ref_are_clean(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file(
            "config.py",
            'import os\n'
            'password = "<your-password-here>"\n'
            'token = os.environ["API_TOKEN"]\n'
            'api_key = "example12345"\n',
            "add safe config", "2026-08-02T10:00:00+00:00")
        out = run_main(secrets_scan, ["--repo", str(self.repo),
                                      "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])

    def test_generic_hardcoded_secret_is_warning(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file(
            "config.py", 'db_password = "S3cure!Pazzw0rd9"\n',
            "add config", "2026-08-02T10:00:00+00:00")
        out = run_main(secrets_scan, ["--repo", str(self.repo),
                                      "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["severity"], "warning")


class TestOsvCheck(RepoTestCase):
    def _setup_manifest(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file("requirements.txt", "requests==2.19.0\n",
                         "pin requests", "2026-08-02T10:00:00+00:00")

    def test_vulnerable_pin_is_error(self):
        self._setup_manifest()
        resp = {"vulns": [{"id": "CVE-2018-18074", "summary": "redirect creds leak"}]}
        with mock.patch.object(osv_check, "http_post_json", return_value=resp) as m:
            out = run_main(osv_check, ["--repo", str(self.repo),
                                       "--base", "HEAD~1", "--head", "HEAD"])
        m.assert_called_once()
        payload = m.call_args[0][1]
        self.assertEqual(payload["package"]["ecosystem"], "PyPI")
        self.assertEqual(payload["package"]["name"], "requests")
        self.assertEqual(payload["version"], "2.19.0")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "error")
        self.assertEqual(f["category"], "security")
        self.assertIn("CVE-2018-18074", f["evidence"][0])

    def test_clean_dep_no_findings(self):
        self._setup_manifest()
        with mock.patch.object(osv_check, "http_post_json", return_value={}):
            out = run_main(osv_check, ["--repo", str(self.repo),
                                       "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["deps_checked"], 1)

    def test_offline_skips_cleanly(self):
        self._setup_manifest()
        with mock.patch.object(osv_check, "http_post_json", return_value=None):
            out = run_main(osv_check, ["--repo", str(self.repo),
                                       "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])
        self.assertTrue(out["skipped"])

    def test_no_manifest_skips(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file("app.py", "x = 2\n", "change", "2026-08-02T10:00:00+00:00")
        out = run_main(osv_check, ["--repo", str(self.repo),
                                   "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["deps_checked"], 0)
        self.assertIn("no pinned dependencies", out["skipped"][0]["reason"])

    def test_package_json_pin_uses_npm(self):
        self.commit_file("app.js", "const x = 1;\n", "base")
        self.commit_file(
            "package.json",
            '{\n  "name": "t",\n  "dependencies": {\n    "axios": "1.6.0"\n  }\n}\n',
            "add dep", "2026-08-02T10:00:00+00:00")
        with mock.patch.object(osv_check, "http_post_json", return_value={}) as m:
            out = run_main(osv_check, ["--repo", str(self.repo),
                                       "--base", "HEAD~1", "--head", "HEAD"])
        payload = m.call_args[0][1]
        self.assertEqual(payload["package"], {"name": "axios", "ecosystem": "npm"})
        self.assertEqual(payload["version"], "1.6.0")
        self.assertEqual(out["deps_checked"], 1)


class TestDepsCheckJs(RepoTestCase):
    def _setup_js_repo(self):
        self.commit_file("app.js", "const x = 1;\n", "base")
        self.commit_file(
            "app.js",
            "import axios from 'axios';\n"
            "import { helper } from './local';\n"
            "const fs = require('fs');\n"
            "import flarghblarghe from 'flarghblarghe';\n"
            "const x = 1;\n",
            "add imports", "2026-08-02T10:00:00+00:00")
        (self.repo / "local.js").write_text("export const helper = 1;\n")
        (self.repo / "package.json").write_text(
            '{"dependencies": {"axios": "^1.6.0"}}\n')

    def test_js_classification(self):
        self._setup_js_repo()
        with mock.patch.object(deps_check, "npm_exists", return_value=False) as m:
            out = run_main(deps_check, ["--repo", str(self.repo),
                                        "--base", "HEAD~1", "--head", "HEAD"])
        # declared (axios), relative (./local), builtin (fs) never reach the registry
        m.assert_called_once_with("flarghblarghe")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "error")
        self.assertIn("flarghblarghe", f["claim"])
        self.assertIn("npm", f["claim"])

    def test_scoped_package_name(self):
        self.assertEqual(deps_check.js_package_name("@scope/pkg/sub"), "@scope/pkg")
        self.assertEqual(deps_check.js_package_name("lodash/fp"), "lodash")
        self.assertIsNone(deps_check.js_package_name("./local"))
        self.assertIsNone(deps_check.js_package_name("@/components/Button"))
        self.assertIsNone(deps_check.js_package_name("node:fs"))


if __name__ == "__main__":
    unittest.main()
