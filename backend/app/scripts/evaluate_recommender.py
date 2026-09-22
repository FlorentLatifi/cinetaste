"""Measure recommendation quality offline.

    python -m app.scripts.evaluate_recommender                      # synthetic
    python -m app.scripts.evaluate_recommender --dataset movielens \
        --path ../data/ml-latest-small

Prints a Markdown table (paste-able into docs/EVALUATION.md) and optionally
writes the raw numbers as JSON. No database required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.recommendation.eval_datasets import build_synthetic_dataset, load_movielens_dataset
from app.recommendation.evaluation import DEFAULT_K, evaluate, format_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline recommender evaluation")
    parser.add_argument("--dataset", choices=("synthetic", "movielens"), default="synthetic")
    parser.add_argument("--path", type=Path, help="MovieLens directory (ml-latest-small)")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="Slate size to score")
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--titles", type=int, default=800)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", type=Path, help="Write raw metrics here")
    args = parser.parse_args(argv)

    if args.dataset == "movielens":
        if not args.path:
            parser.error("--path is required for --dataset movielens")
        dataset = load_movielens_dataset(args.path, max_users=args.users, seed=args.seed)
    else:
        dataset = build_synthetic_dataset(
            n_titles=args.titles, n_users=args.users, seed=args.seed
        )

    print(
        f"dataset={dataset.name} titles={len(dataset.titles)} "
        f"users_evaluated={len(dataset.users)} k={args.k}",
        file=sys.stderr,
    )
    results = evaluate(dataset, k=args.k)
    print(format_report(results, k=args.k))

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
