"""
Unit tests for mine_benchmark.py. No network required.

Run:  pytest test_mine_benchmark.py -v
"""

import pytest

from mine_benchmark import CLOSING_KEYWORDS, TEST_PATH, classify_files, linked_issues


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


# ----------------------------------------------------------- linked_issues --
def test_linked_issues_from_body_and_title():
    pr = {"title": "Fix decimal schema (closes #12)", "body": "Also fixes #34.\nSee #56 for context."}
    assert linked_issues(pr) == {12, 34}


def test_linked_issues_handles_missing_body():
    assert linked_issues({"title": "chore", "body": None}) == set()
