"""
Mine a benchmark of issues, where an issue along with its fixing PR are extracted from a GitHub repo. 

For each issue, the information extracted includes:
- Issue title
- The commit before the fix landed, 
- The files, excluding the test, that the fix edited, 
- The test files the fix edited,
"""



from __future__ import annotations


from importlib.metadata import files
import json 
import argparse
import os
import re
import time 
import requests
from dataclasses import dataclass, asdict, field
from pathlib import Path
import sys


API = "https://api.github.com"

CLOSING_KEYWORDS = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*(?:#|https?://github\.com/[\w.-]+/[\w.-]+/issues/)(\d+)",
    re.IGNORECASE,
)
TEST_PATH = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
 
 

class GitHub:
    def __init__(self, token: str |None):
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bughound-benchmark-miner",
            }
        )
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
 
        
    def get(self, path: str, **params) -> dict | list:
        url = path if path.startswith("http") else f"{API}{path}"
        for attempt in range(5):
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            # Rate limited: wait until reset, then retry.
            if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(1, reset - int(time.time())) + 1
                print(f"  rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                raise KeyError(path)
            if r.status_code >= 500:
                time.sleep(2**attempt)
                continue
            r.raise_for_status()
        raise RuntimeError(f"gave up on {url}")
    
    
    def paginate(self, path: str, limit: int, **params):
        page = 1
        seen = 0
        while seen < limit:
            batch = self.get(path, per_page=min(100, limit - seen), page=page, **params)
            if not batch:
                return
            for item in batch:
                yield item
                seen += 1
                if seen >= limit:
                    return
            page += 1
 
 
 
@dataclass
class Case:
    repo: str
    issue_number: int
    issue_url: str
    title: str
    body: str
    labels: list[str]
    fix_pr: int
    fix_pr_url: str
    merge_commit: str
    base_commit: str  
    expected_files: list[str] 
    test_files: list[str]  
    patch_lines: int
    notes: dict = field(default_factory=dict)
    
    
def find_fixing_pr(gh: GitHub, repo: str, issue_number: int) -> dict | None:
    """
    Walk the issue timeline looking for a merged PR that references this issue
    with a closing keyword. Returns the PR object or None.
    """
    events = gh.get(f"/repos/{repo}/issues/{issue_number}/timeline", per_page=100)
    candidates: list[int] = []
    for ev in events:
        if ev.get("event") == "cross-referenced":
            src = ev.get("source", {}).get("issue", {})
            if src.get("pull_request") and src.get("repository", {}).get("full_name", repo) == repo:
                candidates.append(src["number"])
        elif ev.get("event") == "closed" and ev.get("commit_id"):
            # Closed directly by a commit; we skip these because there's no PR
            # to attach test files to. Could be extended later.
            pass
 
    for pr_number in candidates:
        try:
            pr = gh.get(f"/repos/{repo}/pulls/{pr_number}")
        except KeyError:
            continue
        if not pr.get("merged_at"):
            continue
        text = f"{pr.get('title', '')}\n{pr.get('body') or ''}"
        referenced = {int(m) for m in CLOSING_KEYWORDS.findall(text)}
        if issue_number in referenced:
            return pr
    return None
 
 
def classify_files(files: list[dict]) -> tuple[list[str], list[str], int]:
    src, tests, lines = [], [], 0
    for f in files:
        path = f["filename"]
        lines += f.get("additions", 0) + f.get("deletions", 0)
        if TEST_PATH.search(path):
            tests.append(path)
        elif path.endswith((".py",)):  # extend for other languages
            src.append(path)
        # non-code files (docs, changelog) are ignored for ground truth
    return src, tests, lines
 
 
def mine(
    repo: str,
    max_cases: int,
    scan_limit: int,
    labels: list[str],
    max_src_files: int,
    max_patch_lines: int,
    require_tests: bool,
    token: str | None,
) -> list[Case]:
    gh = GitHub(token)
    cases: list[Case] = []
    scanned = 0
 
    params = {"state": "closed", "sort": "updated", "direction": "desc"}
    if labels:
        params["labels"] = ",".join(labels)
 
    for issue in gh.paginate(f"/repos/{repo}/issues", limit=scan_limit, **params):
        if len(cases) >= max_cases:
            break
        if "pull_request" in issue:  # the issues endpoint also returns PRs
            continue
        scanned += 1
        n = issue["number"]
        print(f"[{scanned}] issue #{n}: {issue['title'][:60]}", file=sys.stderr)
 
        pr = find_fixing_pr(gh, repo, n)
        if not pr:
            print("    no merged PR with closing keyword", file=sys.stderr)
            continue
 
        files = gh.get(f"/repos/{repo}/pulls/{pr['number']}/files", per_page=100)
        src, tests, lines = classify_files(files)
 
        if not (1 <= len(src) <= max_src_files):
            print(f"    skip: {len(src)} source files", file=sys.stderr)
            continue
        if require_tests and not tests:
            print("    skip: no test files touched", file=sys.stderr)
            continue
        if lines > max_patch_lines:
            print(f"    skip: patch too large ({lines} lines)", file=sys.stderr)
            continue
 
        merge_sha = pr["merge_commit_sha"]
        commit = gh.get(f"/repos/{repo}/commits/{merge_sha}")
        parents = commit.get("parents", [])
        if not parents:
            continue
        base_sha = parents[0]["sha"]
 
        case = Case(
            repo=repo,
            issue_number=n,
            issue_url=issue["html_url"],
            title=issue["title"],
            body=issue.get("body") or "",
            labels=[l["name"] for l in issue.get("labels", [])],
            fix_pr=pr["number"],
            fix_pr_url=pr["html_url"],
            merge_commit=merge_sha,
            base_commit=base_sha,
            expected_files=sorted(src),
            test_files=sorted(tests),
            patch_lines=lines,
            notes={"merged_at": pr["merged_at"], "pr_title": pr["title"]},
        )
        cases.append(case)
        print(f"    ✓ case {len(cases)}: PR #{pr['number']} -> {src}", file=sys.stderr)
 
    print(f"\nscanned {scanned} issues, kept {len(cases)} cases", file=sys.stderr)
    return cases
 
 
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--out", default="benchmark/cases.jsonl")
    ap.add_argument("--max-cases", type=int, default=50)
    ap.add_argument("--scan-limit", type=int, default=400, help="max closed issues to inspect")
    ap.add_argument("--label", action="append", default=[], help="filter by label (repeatable), e.g. --label bug")
    ap.add_argument("--max-src-files", type=int, default=2)
    ap.add_argument("--max-patch-lines", type=int, default=200)
    ap.add_argument("--no-require-tests", action="store_true", help="keep cases whose fix touched no test file")
    args = ap.parse_args()
 
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("warning: no GITHUB_TOKEN set; unauthenticated limit is 60 req/hr", file=sys.stderr)
 
    cases = mine(
        repo=args.repo,
        max_cases=args.max_cases,
        scan_limit=args.scan_limit,
        labels=args.label,
        max_src_files=args.max_src_files,
        max_patch_lines=args.max_patch_lines,
        require_tests=not args.no_require_tests,
        token=token,
    )
 
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for c in cases:
            f.write(json.dumps(asdict(c)) + "\n")
    print(f"wrote {len(cases)} cases to {out}", file=sys.stderr)
 
 
if __name__ == "__main__":
    main()