"""
Unit tests for mine_benchmark.py. No network required.

Run:  pytest test_mine_benchmark.py -v
"""

import pytest

from mine_benchmark import CLOSING_KEYWORDS, TEST_PATH, Fix, classify_files, find_fix, fix_files


# ----------------------------------------------------------------- regexes --
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Fixes #482", {482}),
        ("fixes #482", {482}),
        ("FIXED: #7", {7}),
        ("closes #1 and resolves #2", {1, 2}),
        ("Closes https://github.com/psf/requests/issues/490", {490}),
        ("See #501 for background", set()),  # not a closing keyword
        ("prefix #9", set()),  # 'fix' inside another word
        ("Fixes #12, fixes #12 again", {12}),
        ("", set()),
    ],
)
def test_closing_keywords(text, expected):
    assert {int(n) for n in CLOSING_KEYWORDS.findall(text)} == expected


@pytest.mark.parametrize(
    "path, is_test",
    [
        ("tests/test_models.py", True),
        ("test/unit/foo.py", True),
        ("testing/helpers.py", True),
        ("src/pkg/test_thing.py", True),
        ("src/pkg/thing_test.py", True),
        ("conftest.py", True),
        ("src/conftest.py", True),
        ("src/requests/models.py", False),
        ("latest/models.py", False),  # contains 'test' but not a test dir
        ("contest/run.py", False),
        ("docs/testing.md", False),  # 'testing' is a filename here, not a directory
    ],
)
def test_test_path(path, is_test):
    assert bool(TEST_PATH.search(path)) is is_test


# ---------------------------------------------------------- classify_files --
def test_classify_files_splits_and_counts():
    files = [
        {"filename": "src/requests/models.py", "additions": 4, "deletions": 1},
        {"filename": "tests/test_models.py", "additions": 12, "deletions": 0},
        {"filename": "HISTORY.md", "additions": 1, "deletions": 0},
        {"filename": "setup.cfg", "additions": 2, "deletions": 2},
    ]
    src, tests, lines = classify_files(files)
    assert src == ["src/requests/models.py"]
    assert tests == ["tests/test_models.py"]
    assert lines == 22  # every file counts toward size, even ignored ones


def test_classify_files_handles_missing_counts():
    src, tests, lines = classify_files([{"filename": "a.py"}])
    assert src == ["a.py"] and tests == [] and lines == 0


# ---------------------------------------------------------------- find_fix --
class FakeGitHub:
    """Minimal stand-in for GitHub client: serves canned responses by path."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    def get(self, path, **params):
        self.calls.append(path)
        if path not in self.responses:
            raise KeyError(path)
        return self.responses[path]


def _xref(n, repo="o/r"):
    return {
        "event": "cross-referenced",
        "source": {"issue": {"number": n, "pull_request": {"url": f"x/{n}"}, "repository": {"full_name": repo}}},
    }


def _closed_by(sha):
    return {"event": "closed", "commit_id": sha}


def _pr(n, body, merged_at="2026-01-01T00:00:00Z"):
    return {"number": n, "merged_at": merged_at, "body": body, "title": f"PR {n}",
            "html_url": f"https://github.com/o/r/pull/{n}", "merge_commit_sha": f"merge{n}"}


ISSUE = {"number": 10, "closed_at": "2026-01-01T12:00:00Z"}


def test_closing_keyword_wins():
    gh = FakeGitHub({
        "/repos/o/r/issues/10/timeline": [_xref(20), _xref(21)],
        "/repos/o/r/pulls/20": _pr(20, "Fixes #10", merged_at=None),  # not merged
        "/repos/o/r/pulls/21": _pr(21, "Closes #10"),
    })
    fix = find_fix(gh, "o/r", ISSUE)
    assert fix.number == 21 and fix.evidence == "closing-keyword" and fix.sha == "merge21"


def test_closed_by_commit_uses_its_pr():
    gh = FakeGitHub({
        "/repos/o/r/issues/10/timeline": [_closed_by("abc123")],
        "/repos/o/r/commits/abc123/pulls": [_pr(40, "no keyword here")],
    })
    fix = find_fix(gh, "o/r", ISSUE)
    assert fix.kind == "pr" and fix.number == 40 and fix.evidence == "close-commit"


def test_closed_by_bare_commit():
    gh = FakeGitHub({
        "/repos/o/r/issues/10/timeline": [_closed_by("abc123")],
        "/repos/o/r/commits/abc123/pulls": [],
        "/repos/o/r/commits/abc123": {
            "html_url": "https://github.com/o/r/commit/abc123",
            "commit": {"message": "Fix the thing\n\nlong body", "committer": {"date": "2026-01-01T00:00:00Z"}},
        },
    })
    fix = find_fix(gh, "o/r", ISSUE)
    assert fix.kind == "commit" and fix.number is None and fix.sha == "abc123" and fix.title == "Fix the thing"


def test_merge_proximity_fallback():
    gh = FakeGitHub({
        "/repos/o/r/issues/10/timeline": [_xref(30)],
        "/repos/o/r/pulls/30": _pr(30, "Related to #10", merged_at="2026-01-02T00:00:00Z"),  # 12h after close
    })
    fix = find_fix(gh, "o/r", ISSUE)
    assert fix.number == 30 and fix.evidence == "merge-proximity"


def test_distant_mention_is_not_a_fix():
    gh = FakeGitHub({
        "/repos/o/r/issues/10/timeline": [_xref(30)],
        "/repos/o/r/pulls/30": _pr(30, "Related to #10", merged_at="2025-06-01T00:00:00Z"),  # months earlier
    })
    assert find_fix(gh, "o/r", ISSUE) is None


def test_deleted_pr_is_skipped():
    gh = FakeGitHub({"/repos/o/r/issues/10/timeline": [_xref(99)]})  # /pulls/99 -> 404
    assert find_fix(gh, "o/r", ISSUE) is None


def test_fix_files_dispatches_on_kind():
    gh = FakeGitHub({
        "/repos/o/r/pulls/5/files": [{"filename": "a.py"}],
        "/repos/o/r/commits/sha1": {"files": [{"filename": "b.py"}]},
    })
    pr_fix = Fix("pr", "m", "u", "t", 5, None, "e")
    commit_fix = Fix("commit", "sha1", "u", "t", None, None, "e")
    assert fix_files(gh, "o/r", pr_fix) == [{"filename": "a.py"}]
    assert fix_files(gh, "o/r", commit_fix) == [{"filename": "b.py"}]
