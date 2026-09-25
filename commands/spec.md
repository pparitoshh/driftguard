---
description: Interview the user and write SPEC.md (goal, non-goals, constraints, acceptance criteria)
argument-hint: "\"what to build\""
allowed-tools: Read, Grep, Glob, Write, AskUserQuestion
---

# /driftguard:spec — intent capture

Write `SPEC.md` at the repo root — the source of truth for what the harness
is allowed to build. A good spec is short and decisive; the planner and the
reviewers both judge code against it.

## 0. Parse

From `$ARGUMENTS`: quoted text → `IDEA`. If empty, ask the user what to build
— do not invent one. If `SPEC.md` already exists, ask: **rewrite / refine
existing / abort**.

## 1. Ground yourself (cheap)

Skim just enough repo context to ask informed questions: README, the modules
the idea touches, existing test layout. Minutes, not a survey.

## 2. Clarify — one question at a time, max ~5

Use AskUserQuestion. Ask only what changes what gets built, in this order of
value:

1. **Trigger / user-visible behavior** — what does it do when done, in one
   sentence a user could verify?
2. **Scope edges** — the 2–3 most likely scope creep candidates ("also
   refresh related endpoint X?") → make them explicit non-goals.
3. **Constraints** — no new dependencies? must reuse module Y? performance
   ceiling? backwards compatibility?
4. **Acceptance** — the observable checks that mean "done" (commands, outputs,
   behaviors), phrased so each can become a test.
5. **Failure handling / data shape** — only if the idea leaves it genuinely
   ambiguous.

Stop early once the answers are enough to write the spec. Do not interrogate.

## 3. Draft SPEC.md

```markdown
# Spec: <title>

## Goal
<one paragraph — the idea, grounded in the answers>

## Non-goals
- <explicit exclusion, one per line>

## Constraints
- <technical/process limits: deps, reuse, perf, compat>

## Acceptance criteria
- [ ] <observable, testable statement>
- [ ] ...
```

Every acceptance criterion must be checkable by running something. If one
isn't, rewrite it or drop it.

## 4. Section-by-section approval (human gate)

Show **Goal** first, alone; then Non-goals; then Constraints; then Acceptance
criteria — each with AskUserQuestion: **ok / change**. Fold in corrections as
you go. Do not dump the whole document at once; do not write the file before
all four sections are approved.

## 5. Write and stop

Only after approval: Write `SPEC.md` at the repo root. Report the path and
one line per section. Suggest `/driftguard:run` to plan and build it. Do not
start planning yourself.
