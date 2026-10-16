"""Reading code from the project: files, line ranges, git revisions and change regions.

Paths must stay inside the project root after symbolic links are resolved. Git runs only
plumbing commands (`rev-parse`, `cat-file`) with fsmonitor disabled and no pager, and the
diff itself is computed here, so a repository's diff drivers and filters never run.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REV_RE = re.compile(r"^[\w][\w./~^@{}:-]*$")


class SourceError(Exception):
    pass


@dataclass
class Excerpt:
    code: str
    start: int  # first line number shown
    total: int  # lines in the whole file


def resolve(root: Path | None, rel: str) -> Path:
    if root is None:
        raise SourceError(
            f"cannot read {rel}: no project folder is known. Pass project_dir to start_walkthrough."
        )
    base = root.resolve()
    p = Path(rel).expanduser()
    p = (p if p.is_absolute() else base / p).resolve()
    if not p.is_relative_to(base):
        raise SourceError(f"{rel} is outside the project folder {base}")
    if not p.is_file():
        raise SourceError(f"{rel} not found under {base}")
    return p


def parse_range(spec: str | None) -> tuple[int | None, int | None]:
    if not spec:
        return None, None
    m = re.fullmatch(r"\s*(\d+)\s*(?:[-:–]\s*(\d*)\s*)?", spec)
    if not m:
        raise SourceError(f"lines={spec} is not a range like 10-24")
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else (None if "-" in spec or ":" in spec else a)
    if b is not None and b < a:
        a, b = b, a
    return a, b


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        raise SourceError(f"{path.name} looks like a binary file")
    return data.decode("utf-8", errors="replace")


def excerpt(text: str, spec: str | None) -> Excerpt:
    lines = text.splitlines()
    total = len(lines)
    a, b = parse_range(spec)
    a = max(1, a or 1)
    b = min(total, b or total)
    if a > total:
        raise SourceError(f"lines={spec} is past the end of the file ({total} lines)")
    return Excerpt("\n".join(lines[a - 1 : b]), a, total)


def read_excerpt(root: Path | None, rel: str, spec: str | None) -> tuple[Excerpt, Path]:
    path = resolve(root, rel)
    return excerpt(read_text(path), spec), path


# -- git ----------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat", PAGER="cat")
    cmd = ["git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "--no-pager", *args]
    try:
        r = subprocess.run(cmd, cwd=root, env=env, capture_output=True, timeout=15)
    except FileNotFoundError:
        raise SourceError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise SourceError("git took too long") from None
    if r.returncode != 0:
        msg = r.stderr.decode(errors="replace").strip().splitlines()
        raise SourceError(msg[-1] if msg else f"git {args[0]} failed")
    return r.stdout.decode("utf-8", errors="replace")


def head_sha(root: Path | None) -> str | None:
    if root is None:
        return None
    try:
        return _git(root, "rev-parse", "--verify", "--quiet", "HEAD").strip() or None
    except SourceError:
        return None


def file_at(root: Path, rev: str, path: Path) -> str:
    if not REV_RE.match(rev) or rev.startswith("-"):
        raise SourceError(f"rev={rev} is not a git revision")
    rel = os.path.relpath(path, root.resolve())
    try:
        return _git(root, "cat-file", "-p", f"{rev}:./{rel}")
    except SourceError as e:
        if "does not exist" in str(e) or "exists on disk, but not in" in str(e) or "invalid object" in str(e):
            return ""  # file added after that revision
        raise


@dataclass
class DiffSides:
    old: Excerpt
    new: Excerpt
    old_title: str
    new_title: str


def git_diff(root: Path | None, rel: str, rev: str, spec: str | None, context: int = 3) -> DiffSides:
    """Old and new sides of a file between `rev` and the working tree, or `A..B`."""
    if root is None:
        raise SourceError("cannot run git: no project folder is known. Pass project_dir to start_walkthrough.")
    base = root.resolve()
    p = Path(rel)
    path = (p if p.is_absolute() else base / p).resolve()
    if not path.is_relative_to(base):
        raise SourceError(f"{rel} is outside the project folder {base}")
    if ".." in rev:
        a, b = rev.split("..", 1)
        old_text = file_at(root, a or "HEAD", path)
        new_text = file_at(root, b or "HEAD", path)
        old_title, new_title = f"{path.name} @ {a or 'HEAD'}", f"{path.name} @ {b or 'HEAD'}"
    else:
        old_text = file_at(root, rev, path)
        new_text = read_text(path) if path.is_file() else ""
        old_title, new_title = f"{path.name} @ {rev}", path.name
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    if old_lines == new_lines:
        raise SourceError(f"{rel} has no changes for rev={rev}")
    if spec:
        a, b = parse_range(spec)
        n0, n1 = max(1, a or 1), min(len(new_lines), b or len(new_lines))
        o0, o1 = _map_range(old_lines, new_lines, n0, n1)
    else:
        (o0, o1), (n0, n1) = change_region(old_lines, new_lines, context)
    return DiffSides(
        Excerpt("\n".join(old_lines[o0 - 1 : o1]), o0, len(old_lines)),
        Excerpt("\n".join(new_lines[n0 - 1 : n1]), n0, len(new_lines)),
        old_title,
        new_title,
    )


def change_region(old: list[str], new: list[str], context: int = 3) -> tuple[tuple[int, int], tuple[int, int]]:
    """1-based inclusive line ranges covering every change plus context, on both sides."""
    ops = [op for op in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return (1, len(old)), (1, len(new))
    i1, j1 = ops[0][1], ops[0][3]
    i2, j2 = ops[-1][2], ops[-1][4]
    o0, n0 = max(1, i1 + 1 - context), max(1, j1 + 1 - context)
    o1, n1 = min(len(old), i2 + context), min(len(new), j2 + context)
    return (o0, max(o0, o1)), (n0, max(n0, n1))


def _map_range(old: list[str], new: list[str], n0: int, n1: int) -> tuple[int, int]:
    """Old-side range that corresponds to new lines n0..n1."""
    o0 = o1 = None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if j2 < n0 or j1 >= n1:
            if tag != "equal" or j2 < n0:
                continue
        if tag == "equal":
            lo, hi = max(j1, n0 - 1), min(j2, n1)
            if lo < hi:
                a, b = i1 + (lo - j1) + 1, i1 + (hi - j1)
                o0 = a if o0 is None else min(o0, a)
                o1 = b if o1 is None else max(o1, b)
        else:
            if i2 > i1:
                o0 = i1 + 1 if o0 is None else min(o0, i1 + 1)
                o1 = i2 if o1 is None else max(o1, i2)
    if o0 is None:
        return 1, min(len(old), max(1, n1 - n0 + 1))
    return o0, o1


def split_unified(body: str) -> tuple[Excerpt, Excerpt]:
    """Old and new sides from a unified diff body (`-`, `+`, ` ` lines; `@@` sets numbering)."""
    old, new = [], []
    o_start = n_start = None
    for line in body.split("\n"):
        if line.startswith(("--- ", "+++ ", "diff ", "index ")):
            continue
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            if o_start is None:
                o_start, n_start = int(m.group(1)), int(m.group(2))
            continue
        if line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("+"):
            new.append(line[1:])
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
        elif line == "":
            old.append("")
            new.append("")
        else:
            old.append(line)
            new.append(line)
    while old and new and old[-1] == "" and new[-1] == "":
        old.pop()
        new.pop()
    return Excerpt("\n".join(old), o_start or 1, len(old)), Excerpt("\n".join(new), n_start or 1, len(new))
