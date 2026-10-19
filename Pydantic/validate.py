"""
Validate benchmark cases inside the sandbox image.

For each case:
  1. checkout base_commit, plant the fix's test files from merge_commit
  2. run those test files          -> "before" results
  3. checkout merge_commit, rerun   -> "after" results
  4. fail_to_pass = failed before AND passed after
     pass_to_pass = passed before AND passed after
  5. write results back into the case (in place)

Usage:
    python validate.py benchmark/cases_pydantic.jsonl --image bughound-pydantic
    python validate.py benchmark/cases_pydantic.jsonl --only 13645     # one case
    python validate.py benchmark/cases_pydantic.jsonl --workers 4 --timeout 600

A case is `validated: true` only if fail_to_pass is non-empty. Cases that don't
validate are kept (scored on file identification only) with `validate_error`
explaining why.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UV_CACHE_VOLUME = "bughound-uv-cache"
CARGO_CACHE_VOLUME = "bughound-cargo-registry"
# Fields written by run_case; cleared before each re-run so a previous result never leaks into the new one.
RESULT_KEYS = ("validated", "validate_error", "validate_seconds", "fail_to_pass", "pass_to_pass",
               "tests_before", "tests_after")


# ----------------------------------------------------------------- results --
def parse_junit(path: Path) -> dict[str, str]:
    """
    Map test id -> 'passed' | 'failed' | 'error' | 'skipped'.
    Test id looks like 'tests/test_types.py::test_int' (pytest nodeid style).
    """
    results: dict[str, str] = {}
    if not path.exists() or path.stat().st_size == 0:
        return results
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")  # e.g. tests.test_types or tests.test_types.TestFoo
        name = tc.get("name", "")
        file_attr = tc.get("file")
        if file_attr:
            file_part = file_attr
        else:
            # Reconstruct path from dotted classname: tests.test_types -> tests/test_types.py
            parts = classname.split(".")
            # drop trailing class names (start with uppercase) to find the module
            while parts and parts[-1][:1].isupper():
                parts.pop()
            file_part = "/".join(parts) + ".py"
        cls_suffix = classname.split(".")[len(file_part[:-3].split("/")):]
        nodeid = "::".join([file_part, *cls_suffix, name])

        if tc.find("failure") is not None:
            status = "failed"
        elif tc.find("error") is not None:
            status = "error"
        elif tc.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        results[nodeid] = status
    return results


def diff_runs(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    fail_to_pass = sorted(t for t, s in after.items() if s == "passed" and before.get(t) in ("failed", "error"))
    pass_to_pass = sorted(t for t, s in after.items() if s == "passed" and before.get(t) == "passed")
    return fail_to_pass, pass_to_pass


# ----------------------------------------------------------------- sandbox --
def container_script(case: dict) -> str:
    """Bash run inside the container. Writes before.xml / after.xml to /out."""
    tests = " ".join(shlex.quote(t) for t in case["test_files"])
    base, merge = case["base_commit"], case["merge_commit"]
    pytest = f"uv run --no-sync pytest {tests} -p no:cacheprovider -q --no-header --junitxml"
    return f"""
set -u
cd /repo
git checkout -q {base} || {{ echo "checkout base failed" > /out/error; exit 0; }}
git checkout -q {merge} -- {tests} || {{ echo "plant tests failed" > /out/error; exit 0; }}
uv sync --all-extras --no-progress --offline -q || {{ echo "uv sync (base) failed" > /out/error; exit 0; }}
{pytest}=/out/before.xml > /out/before.log 2>&1
git checkout -q -- . && git clean -qfd
git checkout -q {merge} || {{ echo "checkout merge failed" > /out/error; exit 0; }}
uv sync --all-extras --no-progress --offline -q || {{ echo "uv sync (merge) failed" > /out/error; exit 0; }}
{pytest}=/out/after.xml > /out/after.log 2>&1
exit 0
"""


def run_case(case: dict, image: str, timeout: int, keep_logs: Path | None) -> dict:
    start = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "-v", f"{out}:/out",
            "-v", f"{UV_CACHE_VOLUME}:/root/.cache/uv",
            "-v", f"{CARGO_CACHE_VOLUME}:/usr/local/cargo/registry",
            image, "bash", "-c", container_script(case),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"validated": False, "validate_error": f"timeout after {timeout}s",
                    "validate_seconds": round(time.time() - start)}

        if keep_logs:
            dest = keep_logs / str(case["issue_number"])
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            for f in out.iterdir():
                (dest / f.name).write_bytes(f.read_bytes())

        if (out / "error").exists():
            return {"validated": False, "validate_error": (out / "error").read_text().strip(),
                    "validate_seconds": round(time.time() - start)}

        before = parse_junit(out / "before.xml")
        after = parse_junit(out / "after.xml")

    fail_to_pass, pass_to_pass = diff_runs(before, after)
    result = {
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "validated": bool(fail_to_pass),
        "validate_seconds": round(time.time() - start),
        "tests_before": {"failed": sum(s in ("failed", "error") for s in before.values()), "total": len(before)},
        "tests_after": {"failed": sum(s in ("failed", "error") for s in after.values()), "total": len(after)},
    }
    if not fail_to_pass:
        if not before and not after:
            result["validate_error"] = "no test results (collection error?)"
        elif not after:
            result["validate_error"] = "no results after fix"
        elif result["tests_after"]["failed"]:
            result["validate_error"] = "tests still failing after fix"
        else:
            result["validate_error"] = "no test flipped fail->pass"
    return result


# --------------------------------------------------------------------- cli --
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases")
    ap.add_argument("--image", default="bughound-pydantic")
    ap.add_argument("--only", type=int, action="append", help="issue number(s) to validate")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=900, help="seconds per case")
    ap.add_argument("--force", action="store_true", help="re-validate cases that already have results")
    ap.add_argument("--keep-logs", default="benchmark/validate_logs", help="dir for pytest logs ('' to disable)")
    args = ap.parse_args()

    path = Path(args.cases)
    cases = [json.loads(line) for line in path.open()]
    todo = [c for c in cases
            if (not args.only or c["issue_number"] in args.only)
            and (args.force or "validated" not in c)]
    print(f"{len(todo)} of {len(cases)} cases to validate", file=sys.stderr)
    keep = Path(args.keep_logs) if args.keep_logs else None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, c, args.image, args.timeout, keep): c for c in todo}
        for fut in as_completed(futures):
            c = futures[fut]
            for k in RESULT_KEYS:
                c.pop(k, None)
            c.update(fut.result())
            tag = "✓" if c["validated"] else "✗"
            detail = f"{len(c['fail_to_pass'])} f2p, {len(c['pass_to_pass'])} p2p" if c["validated"] else c["validate_error"]
            print(f"  {tag} #{c['issue_number']:<6} {c['validate_seconds']:>4}s  {detail}", file=sys.stderr)
            # Save after every case so a crash doesn't lose progress.
            with path.open("w") as f:
                for row in cases:
                    f.write(json.dumps(row) + "\n")

    ok = sum(c.get("validated", False) for c in cases)
    print(f"\n{ok}/{len(cases)} cases validated", file=sys.stderr)


if __name__ == "__main__":
    main()