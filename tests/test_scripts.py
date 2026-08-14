"""Tests for driftguard context scripts (stdlib unittest, synthetic repos)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from session_extract import repo_slug  # noqa: E402


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout


def run_script(name: str, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"{name} failed: {proc.stderr}"
    return json.loads(proc.stdout)


def run_script_text(name: str, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"{name} failed: {proc.stderr}"
    return proc.stdout


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "my_project"  # underscore: slug test fodder
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def commit_file(self, rel: str, content: str, msg: str, date: str = "2026-08-01T10:00:00+00:00") -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        git(self.repo, "add", rel)
        git(self.repo, "commit", "-m", msg, date=date)


class TestPlanResolve(RepoTestCase):
    def test_plan_doc_wins(self):
        self.commit_file("PLAN.md", "# Plan\nBuild exactly one flag.\n", "add plan")
        self.commit_file("app.py", "x = 1\n", "add app", "2026-08-02T10:00:00+00:00")
        out = run_script("plan_resolve.py", "--repo", str(self.repo), "--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(out["source"], "plan-doc")
        self.assertIn("Build exactly one flag", out["text"])

    def test_fallback_to_commits(self):
        self.commit_file("a.py", "x = 1\n", "first")
        self.commit_file("b.py", "y = 2\n", "feat: add the b module", "2026-08-02T10:00:00+00:00")
        out = run_script("plan_resolve.py", "--repo", str(self.repo), "--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(out["source"], "commits")
        self.assertIn("add the b module", out["text"])

    def test_fallback_to_task_flag(self):
        self.commit_file("a.py", "x = 1\n", "first")
        # empty range: no commits, and detached HEAD kills the branch-name source
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "--detach", sha)
        out = run_script("plan_resolve.py", "--repo", str(self.repo),
                         "--base", "HEAD", "--head", "HEAD", "--task", "do the thing")
        self.assertEqual(out["source"], "task-flag")
        self.assertIn("do the thing", out["text"])

    def test_tried_chain_recorded(self):
        self.commit_file("a.py", "x = 1\n", "first")
        self.commit_file("b.py", "y = 2\n", "second", "2026-08-02T10:00:00+00:00")
        out = run_script("plan_resolve.py", "--repo", str(self.repo), "--base", "HEAD~1", "--head", "HEAD")
        sources = [t["source"] for t in out["tried"]]
        self.assertIn("plan-doc", sources)  # was tried before commits won


class TestDevHistory(RepoTestCase):
    def test_changed_files_and_churn(self):
        self.commit_file("app.py", "x = 1\n", "v1", "2026-08-01T10:00:00+00:00")
        self.commit_file("app.py", "x = 2\n", "v2", "2026-08-02T10:00:00+00:00")
        git(self.repo, "checkout", "-b", "feature-x")
        self.commit_file("app.py", "x = 3\n", "change app on branch", "2026-08-05T10:00:00+00:00")
        self.commit_file("new.py", "z = 1\n", "add new file", "2026-08-05T11:00:00+00:00")
        out = run_script("dev_history.py", "--repo", str(self.repo), "--base", "main", "--head", "HEAD")
        paths = {f["path"]: f for f in out["changed_files"]}
        self.assertIn("app.py", paths)
        self.assertIn("new.py", paths)
        self.assertEqual(paths["new.py"]["status"], "A")
        self.assertIn("churn", paths["app.py"])          # pre-existing file gets churn
        self.assertNotIn("churn", paths["new.py"])       # added file does not
        self.assertEqual(paths["app.py"]["churn"]["total_commits_before_range"], 2)
        self.assertEqual(out["range_start"], "2026-08-05T10:00:00+00:00")
        self.assertEqual(out["range_end"], "2026-08-05T11:00:00+00:00")
        self.assertEqual(len(out["branch_commits"]), 2)


def make_transcript(path: Path, events: list[dict]) -> None:
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def user_msg(text: str, ts: str, branch: str = "main", cwd: str = "/x/my_project") -> dict:
    return {"type": "user", "timestamp": ts, "cwd": cwd, "gitBranch": branch,
            "isSidechain": False, "message": {"role": "user", "content": text}}


def assistant_edit(file_path: str, ts: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": file_path}}]}}


def assistant_text(text: str, ts: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class TestSessionExtract(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.sessions = Path(self.tmp.name) / "sessions"
        self.sessions.mkdir()

    def extract(self, *extra: str) -> str:
        return run_script_text("session_extract.py", "--repo", str(self.repo),
                               "--sessions-dir", str(self.sessions), *extra)

    def test_slug_replaces_non_alnum(self):
        self.assertEqual(repo_slug(Path("/a/b_c/d e")), "-a-b-c-d-e")

    def test_time_window_and_file_overlap_matching(self):
        make_transcript(self.sessions / "s1.jsonl", [
            user_msg("add the prefect orchestration please", "2026-08-05T10:05:00Z"),
            assistant_text("I will add a Prefect flow that wires ingest and embed.", "2026-08-05T10:06:00Z"),
            assistant_edit("/x/my_project/pipeline/flow.py", "2026-08-05T10:07:00Z"),
        ])
        make_transcript(self.sessions / "s2.jsonl", [
            user_msg("unrelated old request about docs", "2026-07-01T09:00:00Z"),
        ])
        out = self.extract("--since", "2026-08-05T10:00:00+00:00",
                           "--until", "2026-08-05T11:00:00+00:00",
                           "--files", "pipeline/flow.py")
        self.assertIn("add the prefect orchestration", out)
        self.assertIn("pipeline/flow.py", out)
        self.assertIn("Prefect flow", out)
        self.assertNotIn("unrelated old request", out)
        self.assertIn("sessions_matched=1", out)

    def test_no_match_reports_cleanly(self):
        make_transcript(self.sessions / "s1.jsonl", [
            user_msg("old stuff", "2026-07-01T09:00:00Z"),
        ])
        out = self.extract("--since", "2026-08-05T10:00:00+00:00",
                           "--until", "2026-08-05T11:00:00+00:00")
        self.assertIn("No session overlapped", out)

    def test_missing_sessions_dir(self):
        gone = self.sessions / "nope"
        out = run_script_text("session_extract.py", "--repo", str(self.repo),
                              "--sessions-dir", str(gone))
        self.assertIn("No Claude sessions found", out)

    def test_budget_truncates(self):
        make_transcript(self.sessions / "s1.jsonl", [
            user_msg(f"request number {i} " + "x" * 200, "2026-08-05T10:%02d:00Z" % min(i, 59))
            for i in range(15)
        ])
        out = self.extract("--budget", "600")
        self.assertIn("truncated", out)


if __name__ == "__main__":
    unittest.main()
