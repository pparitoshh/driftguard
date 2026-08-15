"""Tests for the network kill switch: driftguard makes no outbound request
unless DRIFTGUARD_ALLOW_NETWORK is explicitly set."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "preflight"))

import common  # noqa: E402
import deps_check  # noqa: E402
import osv_check  # noqa: E402
from test_preflight import run_main  # noqa: E402
from test_scripts import RepoTestCase  # noqa: E402


def env(value: str | None) -> mock._patch_dict:
    e = {k: v for k, v in os.environ.items() if k != common.NETWORK_ENV_VAR}
    if value is not None:
        e[common.NETWORK_ENV_VAR] = value
    return mock.patch.dict(os.environ, e, clear=True)


class TestNetworkAllowed(unittest.TestCase):
    def test_disabled_by_default(self):
        with env(None):
            self.assertFalse(common.network_allowed())

    def test_opt_in_values(self):
        for v in ("1", "true", "TRUE", "yes", " Yes "):
            with env(v):
                self.assertTrue(common.network_allowed(), v)

    def test_other_values_stay_disabled(self):
        for v in ("", "0", "false", "no", "off"):
            with env(v):
                self.assertFalse(common.network_allowed(), v)


class TestNoRequestIsMade(unittest.TestCase):
    """The guard must short-circuit before urlopen, not merely discard results."""

    def test_http_json_does_not_call_urlopen(self):
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(common.http_json("https://pypi.org/pypi/x/json"),
                             (None, None))
        urlopen.assert_not_called()

    def test_http_post_json_does_not_call_urlopen(self):
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(
                common.http_post_json("https://api.osv.dev/v1/query", {"a": 1}))
        urlopen.assert_not_called()

    def test_pypi_exists_does_not_call_urlopen(self):
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(deps_check.pypi_exists("requests"))
        urlopen.assert_not_called()

    def test_npm_exists_does_not_call_urlopen(self):
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(deps_check.npm_exists("left-pad"))
        urlopen.assert_not_called()

    def test_urlopen_is_reached_when_opted_in(self):
        with env("1"), mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = OSError("blocked in test")
            self.assertIsNone(deps_check.pypi_exists("requests"))
        urlopen.assert_called_once()


class TestSkipReason(RepoTestCase):
    """A blocked run must say 'disabled', never look like a transient outage."""

    def test_reason_names_the_env_var(self):
        self.assertIn(common.NETWORK_ENV_VAR, common.NETWORK_DISABLED_REASON)

    def test_osv_reports_disabled_and_sends_nothing(self):
        self.commit_file("requirements.txt", "", "base")
        self.commit_file("requirements.txt", "requests==2.19.0\n", "pin dep",
                         "2026-08-02T10:00:00+00:00")
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            out = run_main(osv_check, ["--repo", str(self.repo),
                                       "--base", "HEAD~1", "--head", "HEAD"])
        urlopen.assert_not_called()
        self.assertEqual(out["findings"], [])
        self.assertIn("network disabled", out["skipped"][0]["reason"])

    def test_deps_check_reports_disabled_and_sends_nothing(self):
        self.commit_file("app.py", "x = 1\n", "base")
        self.commit_file("app.py", "import flarghblarghe\nx = 1\n", "add import",
                         "2026-08-02T10:00:00+00:00")
        with env(None), mock.patch("urllib.request.urlopen") as urlopen:
            out = run_main(deps_check, ["--repo", str(self.repo),
                                        "--base", "HEAD~1", "--head", "HEAD"])
        urlopen.assert_not_called()
        self.assertEqual(out["findings"], [])
        self.assertTrue(any("network disabled" in s["reason"]
                            for s in out["skipped"]))


if __name__ == "__main__":
    unittest.main()
