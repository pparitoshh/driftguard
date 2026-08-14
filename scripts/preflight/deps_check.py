#!/usr/bin/env python3
"""Hallucinated-dependency check: every *new* import added by the diff is
classified as stdlib / local / declared-in-project / verified-on-PyPI /
NOT-FOUND (error).

Python only (JS left to the review subagents). Offline -> check skipped.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (added_lines_by_file, base_argparser, emit, finding,  # noqa: E402
                    resolve_range, stdlib_modules)

IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z0-9_]+)|import\s+([A-Za-z0-9_]+))")

IMPORT_TO_PYPI = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "Pillow",
    "yaml": "PyYAML", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "psycopg2": "psycopg2-binary", "Crypto": "pycryptodome",
    "dateutil": "python-dateutil", "jose": "python-jose",
}


def norm(name: str) -> str:
    return name.lower().replace("_", "-")


def declared_deps(repo: Path) -> set[str]:
    names: set[str] = set()
    for req in repo.glob("requirements*.txt"):
        for line in req.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                names.add(norm(re.split(r"[<>=!~;\[]", line)[0].strip()))
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(errors="replace"))
            proj = data.get("project", {})
            deps = list(proj.get("dependencies", []))
            for group in proj.get("optional-dependencies", {}).values():
                deps.extend(group)
            for d in deps:
                names.add(norm(re.split(r"[<>=!~;\[]", str(d))[0].strip()))
        except Exception:
            pass
    return names


def local_modules(repo: Path) -> set[str]:
    names = set()
    for p in repo.rglob("*.py"):
        if any(part.startswith((".", "venv")) for part in p.parts):
            continue
        names.add(p.stem)
        if p.name == "__init__.py":
            names.add(p.parent.name)
    return names


def pypi_exists(name: str, timeout: int = 4) -> bool | None:
    """True/False, or None when the registry can't be reached."""
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except (urllib.error.URLError, OSError):
        return None


def new_imports(repo: Path, base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    """file -> [(lineno, top_module)] from added lines of changed .py files."""
    out: dict[str, list[tuple[int, str]]] = {}
    for path, linenos in added_lines_by_file(repo, base, head).items():
        if not path.endswith(".py"):
            continue
        lines = (repo / path).read_text(errors="replace").splitlines()
        for n in linenos:
            if n <= len(lines):
                m = IMPORT_RE.match(lines[n - 1])
                if m:
                    out.setdefault(path, []).append((n, m.group(1) or m.group(2)))
    return out


def main() -> None:
    ap = base_argparser("hallucinated-dependency pre-flight")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    findings: list[dict] = []
    skipped: list[dict] = []
    stdlib = stdlib_modules()
    declared = declared_deps(repo)
    local = local_modules(repo)

    candidates: dict[str, list[tuple[str, int]]] = {}
    for path, entries in new_imports(repo, base, head).items():
        for lineno, mod in entries:
            if mod in stdlib or mod in local or mod.startswith(("_", "test")):
                continue
            pkg = IMPORT_TO_PYPI.get(mod, mod)
            if norm(pkg) in declared:
                continue  # declared in project deps: treated as intentional
            candidates.setdefault(pkg, []).append((path, lineno))

    offline = False
    for pkg, sites in sorted(candidates.items()):
        status = pypi_exists(pkg)
        if status is None:
            offline = True
            continue
        if status is False:
            for path, lineno in sites:
                findings.append(finding(
                    "tier0:deps_check", "error", path, str(lineno),
                    f"new import '{pkg}' does not exist on PyPI and is not declared "
                    f"— likely hallucinated package (slopsquatting risk)",
                    [f"GET https://pypi.org/pypi/{pkg}/json -> 404",
                     f"not in declared deps, not a local module, not stdlib"],
                    f"remove the import or replace with the intended real package; "
                    f"if real, add it to project dependencies"))

    if offline:
        skipped.append({"check": "deps_check(pypi)", "reason": "registry unreachable — "
                        f"{len(candidates)} undeclared candidate imports not verified"})
    emit({"check": "deps_check", "findings": findings, "skipped": skipped,
          "candidates_checked": len(candidates) - (len(candidates) if offline else 0)})


if __name__ == "__main__":
    main()
