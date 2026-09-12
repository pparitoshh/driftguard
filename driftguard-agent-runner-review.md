## driftguard review — drift_guard main...agent-runner

**Checked against:** plan-doc (`driftguard-agent-plan.md`, untracked — plan_resolve.py fell back to commits since the file isn't in git, but it was read directly since the user pointed to it)
**Verdict:** review_needed
**Risk:** medium (score 6) · **Review effort:** 1 hour+ · 14 files, 937 lines

### Walkthrough
| File | +/- | Kind | What changed |
|---|---|---|---|
| `agent/tools.py` | +117/-0 | code | New: dispatch + enforcement (read_file/write_file/run_tests/done) |
| `agent/contract.py` | +153/-0 | code | New: contract build/validate/propose CLI |
| `agent/run.py` | +113/-0 | code | New: ReAct loop controller, argparse CLI |
| `agent/gate.py` | +64/-0 | code | New: post-loop gate (tests, TDD order, budget, circuit breaker) |
| `agent/backend.py` | +50/-0 | code | New: `claude -p` subprocess backend, JSON parsing |
| `agent/loc.py` | +28/-0 | code | New: pure-LOC counting via difflib |
| `agent/__init__.py` | +1/-0 | code | New: empty package marker |
| `tests/test_agent_loop.py` | +153/-0 | test | Phase 1 acceptance tests, FakeBackend |
| `tests/test_contract.py` | +126/-0 | test | Phase 2 acceptance tests |
| `tests/test_gate.py` | +99/-0 | test | Phase 3 acceptance tests |
| `README.md` | +25/-2 | docs | Agent runner section + dogfood LOC record |
| `agents/intent-scope.md`, `agents/slop-redundancy.md` | +2/-0 each | docs | "contract.json is authoritative scope" line |
| `commands/learn.md` | +2/-0 | docs | `[harness]` tag documented |

### Summary
This implements the 4-phase agent-runner plan almost exactly as specified: a ReAct loop where an LLM emits JSON actions, a tool layer enforces path/TDD/forbidden-pattern/LOC-budget rules, and a post-loop gate checks tests, TDD ordering, and budget before handing off to the existing `/driftguard:review`. All 98 tests pass, file scope matches the plan's per-phase lists, LOC budgets are respected, and the ground rules (stdlib-only, no new deps, no unnamed try/except, no logging) held up under inspection. The one real gap is that the plan's own "enforced tools, no shell access" security model isn't fully realized: `write_file` skips the path-containment check that `read_file` has, and `test_cmd` is executed via `shell=True` with no content restriction beyond requiring the literal `{tests}` — both of which matter precisely because the whole design's premise is that an LLM's untrusted output drives file writes and command execution.

### Findings
1. **[error][security]** `agent/tools.py:64-99` — `write_file` has no path-containment check, unlike `read_file`; a `files_allowed` entry with `..` segments (or a symlink) lets the agent write outside the repo.
   - Evidence: `read_file` (tools.py:56-58) resolves the path and checks `repo not in target.parents`; `write_file` only does string comparisons (`normalized.startswith(PROTECTED_PREFIXES)`, `normalized not in contract["files_allowed"]`) then writes directly with no `.resolve()`/containment check. `contract.py validate()` (lines 33-53) never rejects `..` or absolute paths in `files_allowed`/`files_create`.
   - Action: Mirror `read_file`'s resolve-and-contain check in `write_file`; add a `validate()` rule rejecting `..` segments or absolute paths in `files_allowed`/`files_create`.

2. **[warning][security]** `agent/tools.py:102-112`, `agent/gate.py:18-23` — `test_cmd` runs via `subprocess.run(..., shell=True)` and is validated only for containing the literal `{tests}`; it can arrive from an LLM's `--propose` draft or a raw `--json` CLI arg with no shell-metacharacter restriction.
   - Evidence: `contract.py validate()` line 47-48 checks only `"{tests}" not in contract["test_cmd"]`. `propose()` (lines 18-30, 71-83) feeds LLM output into this field; `main()`'s `--json` path (lines 133-138) accepts raw JSON directly before `validate()`.
   - Action: Constrain `test_cmd` to a small allowlist of known runner templates, or reject shell metacharacters (`;`, `&&`, `|`, backticks, `$(`) outside the `{tests}` placeholder before executing. The human-approval step in `--propose` partially mitigates this but isn't enforced in code.

3. **[warning][slop-redundancy]** `agent/contract.py:44` — `validate()` re-derives the test-path parsing rule (`entry.split("::")[0]` / dotted-id → `.py`) instead of reusing `agent/tools.test_file`, duplicating logic across two modules.
   - Evidence: same split logic exists at `agent/tools.py:47-48` inside `test_file()`, which already has 2 external callers (`gate.py`, `run.py`).
   - Action: Import and call `agent.tools.test_file(contract)` from `contract.py` instead of re-deriving the parsing rule inline.

4. **[info][test-integrity]** `agent/run.py:99-107` — the CLI entry `main()` (argparse for `--contract`/`--repo`) is never exercised by any test; all tests call `runner.run()` directly.
   - Evidence: no `run.main(` / `runner.main(` calls found in `tests/*.py`.
   - Action: Add a thin test invoking `agent.run.main([...])` with a monkeypatched backend, or accept as a low-risk CLI wrapper.

5. **[info][test-integrity]** `agent/tools.py:64-67` — the protected-path denial branch (writes to `agent/`, `.driftguard/`, `.claude/`) has no dedicated test; only the "outside files_allowed" branch is covered.
   - Evidence: `tests/test_agent_loop.py:79-89`'s out-of-scope test targets `evil.py`, which trips "outside contract," not "protected path."
   - Action: Add a targeted test for a protected-prefix write, or note it's effectively unreachable given `contract.py` validation and accept as-is.

6. **[info][intent-scope]** `agent/backend.py:35-42` — Phase 1's spec lists only `chat()` and `parse_action()` for `backend.py`, but a third function `loads_json()` was factored out and is imported by Phase 2's `contract.py`.
   - Evidence: plan's Phase 1 backend.py section names only two functions; `agent/contract.py:76,126,135` calls `backend.loads_json(...)`.
   - Action: No action needed — it's a genuine shared helper (2+ callers), not a "helper used once" violation. Flagged only for awareness of the minor phase-boundary crossing.

### Not checked
- **Dogfood real-CLI run**: not re-executed in this review; taken on faith from the Phase 4 commit message ("dogfood passed, budget 40 LOC, actual 11"). Not independently verified against a live `claude -p` invocation.
- **`.driftguard/rules.md` / `.driftguard/learnings.md` / CLAUDE.md-style guideline files**: none exist in this repo, so no custom team rules or path-scoped constraints were available to apply.
- **Ruff/linters**: skipped — no ruff config found in repo (t0_linters.json).
- **OSV vulnerability scan**: skipped — no pinned dependency changes in this diff to check against.
- **PR conversation / linked issue**: not applicable — this is a local branch review, no PR exists.
- **Symlink-based exploitation of the write_file gap (finding 1)**: reasoned about statically, not actually reproduced by writing a symlink and invoking the tool end-to-end.

> Findings are evidence-linked above. Say **"fix findings 1-3"** and this session will address them; correct a finding and say **"/driftguard:learn ..."** to make the correction permanent for future reviews.
