"""Fail the build when a package's coverage drops below its floor.

``--cov-fail-under`` only guards the total, and a total is easy to hold up with
well-covered trivia while the code that decides what a user sees quietly rots.
The floors below are per package, set a little under where each one actually
sits so that a real regression trips them and ordinary churn does not.

Run after pytest has written coverage.json:

    pytest --cov=app --cov-report=json:coverage.json
    python tools/coverage_gate.py coverage.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Package prefix -> minimum percent of statements covered.
#
# Business logic is held higher than plumbing on purpose: a bug in ranking or
# taste is a bad product, a bug in an optional Sentry hook is a missing log
# line. `app/scripts` is absent deliberately — those are operator CLIs whose
# behaviour is exercised through the services they call.
FLOORS: dict[str, int] = {
    "app/domain": 90,
    "app/recommendation": 85,
    "app/application": 80,
    "app/api": 85,
    "app/core": 80,
    "app/infrastructure": 70,
}

# Not production code: operator CLIs and the offline-evaluation loader, which
# reads MovieLens files and never runs inside the API. Excluded so the floors
# measure what serves users — not moved out of the way because they were
# inconvenient. The overall --cov-fail-under still counts them.
EXCLUDE_PREFIXES = (
    "app/scripts/",
    "app/recommendation/eval_datasets.py",
)


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def main(report_path: str) -> int:
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))

    totals: dict[str, list[int]] = {prefix: [0, 0] for prefix in FLOORS}
    for raw_name, entry in data["files"].items():
        name = raw_name.replace("\\", "/")
        if name.startswith(EXCLUDE_PREFIXES):
            continue
        summary = entry["summary"]
        for prefix in FLOORS:
            if name.startswith(prefix + "/"):
                totals[prefix][0] += summary["covered_lines"]
                totals[prefix][1] += summary["num_statements"]
                break

    failures: list[str] = []
    print(f"{'package':<22} {'covered':>9} {'floor':>6}  result")
    for prefix, floor in sorted(FLOORS.items()):
        covered, statements = totals[prefix]
        if statements == 0:
            print(f"{prefix:<22} {'—':>9} {floor:>5}%  no files matched")
            continue
        pct = _percent(covered, statements)
        ok = pct >= floor
        print(f"{prefix:<22} {pct:>8.1f}% {floor:>5}%  {'ok' if ok else 'BELOW FLOOR'}")
        if not ok:
            failures.append(
                f"{prefix} is at {pct:.1f}%, floor is {floor}% "
                f"({covered}/{statements} statements)"
            )

    if failures:
        print("\nCoverage gate failed:")
        for line in failures:
            print(f"  - {line}")
        print(
            "\nEither cover the new code or, if the floor is genuinely wrong, "
            "change it in tools/coverage_gate.py in the same commit and say why."
        )
        return 1

    print("\nCoverage gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
