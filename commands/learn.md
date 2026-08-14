---
description: Record a review preference/learning that future driftguard reviews will apply
argument-hint: "\"the preference, with the why\""
allowed-tools: Bash, Read
---

# /driftguard:learn — persistent review memory (plain file, no database)

The lightweight answer to CodeRabbit/Greptile learnings: an append-only markdown
file at `.driftguard/learnings.md` in the target repo, loaded into every future
review by `scripts/team_context.py`.

## Steps

1. Take `$ARGUMENTS` as the learning text. If empty, ask the user what preference
   to record — do not invent one.
2. If the text contains anything that looks like a credential (tokens, passwords,
   keys), redact it to `<redacted>` before writing — learnings are committed to
   the repo. (CodeRabbit does the same redaction before storage.)
3. Append to `.driftguard/learnings.md` (create with `mkdir -p .driftguard`):

   ```markdown
   - [YYYY-MM-DD] <learning text>  (scope: repo | glob if the user gave one)
   ```

   One line per learning, dated, including **the why** when the user gave it —
   "prefer early returns over nested try/except in auth middleware because nested
   blocks are harder to debug in production" beats "don't use nested try/except".
4. If the learning is clearly a *standing rule* rather than a preference
   (e.g. "never flag X", "always require tests under src/auth/**"), suggest also
   adding it to `.driftguard/rules.md` — rules are enforced more strictly than
   learnings. Offer to do it; don't do it unprompted.
5. Confirm with one line: what was recorded and where. No other output.

## Notes

- Learnings steer findings; they never suppress Tier 0 `error` findings (a real
  secret or a hallucinated dep is reported regardless).
- If `.driftguard/learnings.md` grows past ~100 entries, suggest a cleanup pass —
  stale learnings contradicting current practice hurt more than help (same
  maintenance advice CodeRabbit gives).
