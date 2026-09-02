"""
Tag each mined case with a `category` and optional `flags`, in place.

Categories (first match wins):
  mypy     - fix lives in the mypy plugin; tests shell out to mypy
  feature  - labelled as a feature request rather than a bug
  bug      - everything else

Flags (informational, never exclude on their own):
  thin_body  - issue body under 300 chars
  large      - patch over 120 lines

Usage:  python categorize.py benchmark/cases_pydantic.jsonl
"""

import json
import sys
from collections import Counter
from pathlib import Path


def categorize(case: dict) -> tuple[str, list[str]]:
    labels = set(case.get("labels", []))
    files = case.get("expected_files", [])

    if any(f.endswith("/mypy.py") for f in files) or "topic-mypy plugin" in labels:
        category = "mypy"
    elif "feature request" in labels:
        category = "feature"
    else:
        category = "bug"

    flags = []
    if len(case.get("body", "")) < 300:
        flags.append("thin_body")
    if case.get("patch_lines", 0) > 120:
        flags.append("large")
    return category, flags


def main(path: str) -> None:
    p = Path(path)
    cases = [json.loads(line) for line in p.open()]
    for c in cases:
        c["category"], c["flags"] = categorize(c)
    with p.open("w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")

    print(f"{len(cases)} cases -> {p}")
    print("categories:", dict(Counter(c["category"] for c in cases)))
    print("flags:", dict(Counter(fl for c in cases for fl in c["flags"])))
    for c in cases:
        if c["category"] != "bug" or c["flags"]:
            print(f"  #{c['issue_number']:<6} {c['category']:8} {','.join(c['flags']):16} {c['title'][:60]}")


if __name__ == "__main__":
    main(sys.argv[1])
