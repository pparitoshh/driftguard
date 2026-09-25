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

    def test_spec_md_in_chain_after_plan_md(self):
        # harness writes SPEC.md at repo root; review must resolve it as plan-doc
        self.commit_file("SPEC.md", "# Spec\nAdd caching to the menu API.\n", "add spec")
        self.commit_file("app.py", "x = 1\n", "add app", "2026-08-02T10:00:00+00:00")
        out = run_script("plan_resolve.py", "--repo", str(self.repo), "--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(out["source"], "plan-doc")
        self.assertIn("Add caching", out["text"])

    def test_plan_md_beats_spec_md(self):
        self.commit_file("PLAN.md", "# Plan\nplan wins.\n", "add plan")
        self.commit_file("SPEC.md", "# Spec\nspec loses.\n", "add spec")
        self.commit_file("app.py", "x = 1\n", "add app", "2026-08-02T10:00:00+00:00")
        out = run_script("plan_resolve.py", "--repo", str(self.repo), "--base", "HEAD~1", "--head", "HEAD")
        self.assertIn("plan wins", out["text"])

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


class TestRiskScore(RepoTestCase):
    def test_sensitive_path_plus_test_gap_elevates_risk(self):
        self.commit_file("auth/login.py", "def login():\n    return True\n", "base")
        self.commit_file("auth/login.py",
                         "def login(u, p):\n    return check(u, p)\n" * 30,
                         "rework login", "2026-08-02T10:00:00+00:00")
        out = run_script("risk_score.py", "--repo", str(self.repo),
                         "--base", "HEAD~1", "--head", "HEAD")
        self.assertIn(out["risk_level"], ("medium", "high"))
        self.assertTrue(out["totals"]["test_gap"])
        self.assertEqual(out["totals"]["sensitive_files"], 1)
        self.assertEqual(out["suggested_review_order"][0], "auth/login.py")
        self.assertTrue(any("sensitive" in b for b in out["score_breakdown"]))

    def test_docs_only_change_is_low_risk(self):
        self.commit_file("README.md", "# t\n", "base")
        self.commit_file("README.md", "# t\n\nmore docs\n", "docs",
                         "2026-08-02T10:00:00+00:00")
        out = run_script("risk_score.py", "--repo", str(self.repo),
                         "--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(out["risk_level"], "low")
        self.assertFalse(out["totals"]["test_gap"])
        self.assertEqual(out["files"][0]["kind"], "docs")

    def test_hotspot_churn_adds_points(self):
        for i in range(9):
            self.commit_file("hot.py", f"x = {i}\n", f"v{i}",
                             f"2026-08-0{1 + i % 8}T10:00:00+00:00")
        self.commit_file("hot.py", "x = 99\n", "change hot file",
                         "2026-08-12T10:00:00+00:00")
        out = run_script("risk_score.py", "--repo", str(self.repo),
                         "--base", "HEAD~1", "--head", "HEAD")
        self.assertTrue(any("hotspot" in b for b in out["score_breakdown"]),
                        out["score_breakdown"])


class TestTeamContext(RepoTestCase):
    def test_empty_repo_reports_none(self):
        out = run_script("team_context.py", "--repo", str(self.repo))
        self.assertEqual(out["sources"], [])
        self.assertIn("No team context", out["markdown"])

    def test_rules_learnings_guidelines_collected(self):
        (self.repo / ".driftguard").mkdir()
        (self.repo / ".driftguard" / "rules.md").write_text(
            "# Rules\nAlways require tests.\n\n## path: src/**\nNever flag shell=True.\n")
        (self.repo / ".driftguard" / "learnings.md").write_text(
            "- [2026-08-01] prefer early returns, the why: debuggability\n")
        (self.repo / "CLAUDE.md").write_text("# Guidelines\nUse stdlib only.\n")
        out = run_script("team_context.py", "--repo", str(self.repo),
                         "--files", "src/auth/login.py")
        kinds = {s["kind"] for s in out["sources"]}
        self.assertEqual(kinds, {"custom-rules", "learnings", "guidelines"})
        self.assertIn("Always require tests", out["markdown"])
        self.assertIn("Never flag shell=True", out["markdown"])   # path matches src/**
        self.assertIn("prefer early returns", out["markdown"])
        self.assertIn("stdlib only", out["markdown"])

    def test_path_scoped_rules_excluded_when_no_match(self):
        (self.repo / ".driftguard").mkdir()
        (self.repo / ".driftguard" / "rules.md").write_text(
            "# Rules\nGlobal rule.\n\n## path: src/**\nSrc-only rule.\n")
        out = run_script("team_context.py", "--repo", str(self.repo),
                         "--files", "docs/guide.md")
        self.assertIn("Global rule", out["markdown"])
        self.assertNotIn("Src-only rule", out["markdown"])


if __name__ == "__main__":
    unittest.main()
