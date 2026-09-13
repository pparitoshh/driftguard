"""Pure LOC counting and diff-based added-LOC accounting (no git needed)."""

import difflib

_COMMENT_PREFIXES = ("#", "//")


def pure_loc(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(_COMMENT_PREFIXES)
    )


def deleted_loc(baseline: dict[str, str], current: dict[str, str]) -> int:
    """Sum of pure baseline lines removed across files."""
    total = 0
    for path, new_text in current.items():
        diff = difflib.unified_diff(
            baseline.get(path, "").splitlines(), new_text.splitlines(), lineterm=""
        )
        total += sum(
            pure_loc(line[1:])
            for line in diff
            if line.startswith("-") and not line.startswith("---")
        )
    return total


def added_loc(baseline: dict[str, str], current: dict[str, str]) -> int:
    """Sum of pure added lines across files: current snapshots vs baseline."""
    total = 0
    for path, new_text in current.items():
        diff = difflib.unified_diff(
            baseline.get(path, "").splitlines(), new_text.splitlines(), lineterm=""
        )
        total += sum(
            pure_loc(line[1:])
            for line in diff
            if line.startswith("+") and not line.startswith("+++")
        )
    return total
