"""Reading code from the project: path containment, ranges, binary files, git diffs and git safety."""

from __future__ import annotations

import os
import subprocess

import pytest

from showntell import sources
from showntell.sources import SourceError

# -- paths ------------------------------------------------------------------------------------


def test_resolve_inside_root(project):
    assert sources.resolve(project, "src/cache.py") == (project / "src" / "cache.py").resolve()


def test_resolve_absolute_path_inside_root(project):
    assert sources.resolve(project, str(project / "src" / "cache.py")).name == "cache.py"


def test_resolve_refuses_parent_escape(project, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    with pytest.raises(SourceError, match="outside the project"):
        sources.resolve(project, "../secret.txt")


def test_resolve_refuses_absolute_path_outside(project, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    with pytest.raises(SourceError, match="outside the project"):
        sources.resolve(project, str(tmp_path / "secret.txt"))


def test_resolve_refuses_symlink_pointing_outside(project, tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    os.symlink(tmp_path / "secret.txt", project / "link.txt")
    with pytest.raises(SourceError, match="outside the project"):
        sources.resolve(project, "link.txt")


def test_resolve_allows_symlink_staying_inside(project):
    os.symlink(project / "src" / "cache.py", project / "alias.py")
    assert sources.resolve(project, "alias.py").name == "cache.py"


def test_resolve_missing_file(project):
    with pytest.raises(SourceError, match="not found"):
        sources.resolve(project, "src/missing.py")


def test_resolve_without_root_explains_project_dir():
    with pytest.raises(SourceError, match="project_dir"):
        sources.resolve(None, "a.py")


# -- ranges -----------------------------------------------------------------------------------


@pytest.mark.parametrize("spec,expected", [
    ("10-24", (10, 24)),
    ("10", (10, 10)),
    ("10-", (10, None)),
    ("24-10", (10, 24)),
    ("10:24", (10, 24)),
    (" 3 - 5 ", (3, 5)),
    (None, (None, None)),
    ("", (None, None)),
])
def test_parse_range(spec, expected):
    assert sources.parse_range(spec) == expected


def test_parse_range_rejects_words():
    with pytest.raises(SourceError, match="not a range"):
        sources.parse_range("ten")


def test_excerpt_range_keeps_real_line_numbers():
    text = "\n".join(f"l{i}" for i in range(1, 31))
    ex = sources.excerpt(text, "10-12")
    assert (ex.code, ex.start, ex.total) == ("l10\nl11\nl12", 10, 30)


def test_excerpt_open_range_runs_to_the_end():
    text = "\n".join(f"l{i}" for i in range(1, 6))
    assert sources.excerpt(text, "4-").code == "l4\nl5"


def test_excerpt_single_line():
    assert sources.excerpt("a\nb\nc", "2").code == "b"


def test_excerpt_range_end_is_clamped():
    assert sources.excerpt("a\nb\nc", "2-99").code == "b\nc"


def test_excerpt_past_the_end_is_refused():
    with pytest.raises(SourceError, match="past the end"):
        sources.excerpt("a\nb", "5-6")


def test_read_excerpt_from_disk(project):
    ex, path = sources.read_excerpt(project, "src/cache.py", "10-12")
    assert ex.start == 10 and ex.code.splitlines()[2] == "    if key in self._data:" and path.name == "cache.py"


def test_binary_file_is_refused(project):
    with pytest.raises(SourceError, match="binary"):
        sources.read_excerpt(project, "blob.bin", None)


# -- unified diff bodies ----------------------------------------------------------------------


def test_split_unified_numbers_from_the_hunk_header():
    old, new = sources.split_unified("@@ -10,3 +12,2 @@\n keep\n-total = 0\n-for x in xs: total += x\n+total = sum(xs)\n")
    assert (old.start, new.start) == (10, 12)
    assert old.code == "keep\ntotal = 0\nfor x in xs: total += x"
    assert new.code == "keep\ntotal = sum(xs)"


def test_split_unified_without_header_starts_at_one():
    old, new = sources.split_unified("-a\n+b\n")
    assert (old.start, new.start, old.code, new.code) == (1, 1, "a", "b")


def test_split_unified_skips_file_headers():
    body = "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    old, new = sources.split_unified(body)
    assert (old.code, new.code) == ("a", "b")


def test_split_unified_ignores_no_newline_marker():
    old, new = sources.split_unified("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n")
    assert (old.code, new.code) == ("a", "b")


def test_split_unified_keeps_removed_sql_comment():
    old, new = sources.split_unified("@@ -1,2 +1,1 @@\n--- old comment\n select 1;\n")
    assert old.code == "-- old comment\nselect 1;" and new.code == "select 1;"


# -- change regions ---------------------------------------------------------------------------


def test_change_region_adds_context_on_both_sides():
    old = [f"l{i}" for i in range(1, 21)]
    new = old[:9] + ["NEW"] + old[10:]
    assert sources.change_region(old, new, 3) == ((7, 13), (7, 13))


def test_change_region_with_no_changes_is_everything():
    assert sources.change_region(["a", "b"], ["a", "b"]) == ((1, 2), (1, 2))


# -- git --------------------------------------------------------------------------------------


TEN = "".join(f"line {i}\n" for i in range(1, 11))


def test_git_diff_against_head(repo):
    repo.write("a.py", TEN)
    repo.commit("one")
    repo.write("a.py", TEN.replace("line 5\n", "line five\n"))
    d = sources.git_diff(repo.root, "a.py", "HEAD", None)
    assert d.old.start == 2 and d.new.start == 2
    assert "line 5" in d.old.code and "line five" in d.new.code
    assert (d.old_title, d.new_title) == ("a.py @ HEAD", "a.py")


def test_git_diff_between_two_commits(repo):
    repo.write("a.py", TEN)
    a = repo.commit("one")
    repo.write("a.py", TEN.replace("line 9\n", "line nine\n"))
    b = repo.commit("two")
    repo.write("a.py", "unrelated working tree\n")
    d = sources.git_diff(repo.root, "a.py", f"{a[:10]}..{b[:10]}", None)
    assert "line 9" in d.old.code and "line nine" in d.new.code
    assert "unrelated" not in d.new.code
    assert d.new_title.endswith(f"@ {b[:10]}")


def test_git_diff_range_with_head_default(repo):
    repo.write("a.py", TEN)
    a = repo.commit("one")
    repo.write("a.py", TEN + "line 11\n")
    repo.commit("two")
    d = sources.git_diff(repo.root, "a.py", f"{a}..", None)
    assert "line 11" in d.new.code and d.new_title.endswith("@ HEAD")


def test_git_diff_three_dots_compares_with_where_the_branch_left(repo):
    """A...B, as a pull request shows it: changes made on main after the branch left are not
    part of the diff."""
    repo.write("a.py", TEN)
    repo.commit("one")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("a.py", TEN.replace("line 2\n", "line two\n"))
    repo.commit("on the branch")
    repo.git("checkout", "-q", "main")
    repo.write("a.py", TEN.replace("line 9\n", "line nine\n"))
    repo.commit("on main")
    d = sources.git_diff(repo.root, "a.py", "main...feature", None)
    assert "line 2" in d.old.code and "line two" in d.new.code
    assert "line nine" not in d.old.code + d.new.code
    assert d.old_title.endswith("@ main") and d.new_title.endswith("@ feature")


def test_git_diff_file_added_after_rev(repo):
    repo.write("a.py", TEN)
    repo.commit("one")
    repo.write("b.py", "brand new\n")
    d = sources.git_diff(repo.root, "b.py", "HEAD", None)
    assert d.old.code == "" and d.new.code == "brand new"


def test_git_diff_without_changes_is_an_error(repo):
    repo.write("a.py", TEN)
    repo.commit("one")
    with pytest.raises(SourceError, match="no changes"):
        sources.git_diff(repo.root, "a.py", "HEAD", None)


def test_git_diff_lines_maps_new_range_to_old_side(repo):
    repo.write("a.py", TEN)
    repo.commit("one")
    lines = TEN.splitlines(keepends=True)
    repo.write("a.py", "".join(lines[:3] + ["inserted 1\n", "inserted 2\n"] + lines[3:]))
    d = sources.git_diff(repo.root, "a.py", "HEAD", "5-8")
    assert d.new.start == 5 and d.new.code.splitlines() == ["inserted 2", "line 4", "line 5", "line 6"]
    assert d.old.start == 4 and d.old.code.splitlines() == ["line 4", "line 5", "line 6"]


def test_git_diff_refuses_path_outside_root(repo, tmp_path):
    repo.write("a.py", TEN)
    repo.commit("one")
    with pytest.raises(SourceError, match="outside"):
        sources.git_diff(repo.root, "../elsewhere.py", "HEAD", None)


@pytest.mark.parametrize("rev", ["nosuchbranch", "HEAD~5"])
def test_git_diff_unknown_revision_is_an_error(repo, rev):
    repo.write("a.py", TEN)
    repo.commit("one")
    repo.write("a.py", TEN + "line 11\n")
    with pytest.raises(SourceError):
        sources.git_diff(repo.root, "a.py", rev, None)


@pytest.mark.parametrize("rev", ["--output=/tmp/x", "-p", "HEAD..--output=x", "..--help", "HEAD;rm -rf", "a b"])
def test_git_diff_rejects_option_like_revisions(repo, rev):
    repo.write("a.py", TEN)
    repo.commit("one")
    with pytest.raises(SourceError, match="not a git revision"):
        sources.git_diff(repo.root, "a.py", rev, None)


def test_head_sha(repo):
    repo.write("a.py", TEN)
    sha = repo.commit("one")
    assert sources.head_sha(repo.root) == sha


def test_head_sha_outside_git_is_none(tmp_path):
    assert sources.head_sha(tmp_path) is None and sources.head_sha(None) is None


# -- git safety: repository config must never run commands -------------------------------------


@pytest.fixture
def armed_repo(repo, tmp_path):
    """A repo whose config runs a marker-writing script from fsmonitor, diff.external and textconv."""
    marker = tmp_path / "marker"
    script = tmp_path / "evil.sh"
    script.write_text(f'#!/bin/sh\necho "$0 $*" >> "{marker}"\ncat "$1" 2>/dev/null\nexit 0\n')
    script.chmod(0o755)
    repo.write("a.py", TEN)
    repo.commit("one")
    repo.git("config", "core.fsmonitor", str(script))
    repo.git("config", "diff.external", str(script))
    repo.git("config", "diff.evil.textconv", str(script))
    repo.write(".gitattributes", "*.py diff=evil\n")
    repo.write("a.py", TEN.replace("line 5\n", "line five\n"))
    return repo, marker


def test_armed_repo_really_runs_the_script_with_plain_git(armed_repo):
    repo, marker = armed_repo
    subprocess.run(["git", "diff"], cwd=repo.root, capture_output=True)
    assert marker.exists()  # the trap works, so the next tests mean something


def test_git_diff_never_runs_repository_commands(armed_repo):
    repo, marker = armed_repo
    d = sources.git_diff(repo.root, "a.py", "HEAD", None)
    assert "line five" in d.new.code
    assert not marker.exists()


def test_git_diff_between_commits_never_runs_repository_commands(armed_repo):
    repo, marker = armed_repo
    repo.commit("two")  # plain git add runs the fsmonitor script; start clean
    marker.unlink(missing_ok=True)
    sources.git_diff(repo.root, "a.py", "HEAD~1..HEAD", None)
    assert not marker.exists()


def test_head_sha_never_runs_repository_commands(armed_repo):
    repo, marker = armed_repo
    assert sources.head_sha(repo.root)
    assert not marker.exists()
