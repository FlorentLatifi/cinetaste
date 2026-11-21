"""Measure recommendation quality offline.

    python -m app.scripts.evaluate_recommender                      # synthetic
    python -m app.scripts.evaluate_recommender --dataset movielens \\
        --path ~/.cache/cinetaste/eval/ml-latest-small
    python -m app.scripts.evaluate_recommender --dataset movielens-tmdb \\
        --path ~/.cache/cinetaste/eval/ml-latest-small -k 20 --users 0

``movielens-tmdb`` needs the TMDb cache first (``app.scripts.fetch_eval_metadata``).

Weight experiments run the production configuration with some weights changed
and report paired deltas (95% bootstrap CI) against ``--baseline``:

    --sweep sparse_negative=0,0.12,0.3,0.6          # one strategy per value
    --variant proposal_a:sparse_negative=0.3,warm_popularity=0.05
    --variant caps_off:max_per_director=999,max_per_collection=999   # caps, too
    --only-variants                                  # skip the standard strategies

A ranking change is measured against another code version the same way: run
with ``--json master.json`` on ``master``, then on the branch with
``--against master.json`` for paired deltas of each strategy against its
namesake there.

Prints Markdown tables (paste-able into docs/EVALUATION.md) and optionally
writes the raw numbers, per-user rows included, as JSON. No database required.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.recommendation.eval_datasets import (
    PRODUCTION_CATALOG_SIZE,
    build_synthetic_dataset,
    load_movielens_dataset,
    load_movielens_tmdb_dataset,
)
from app.recommendation.evaluation import (
    ACCURACY_COLUMNS,
    BEYOND_COLUMNS,
    DEFAULT_K,
    DEFAULT_STRATEGIES,
    SAFETY_COLUMNS,
    Strategy,
    format_deltas,
    format_report,
    paired_deltas,
    paired_deltas_against,
    production_variant,
    rating_count_strategy,
    run_evaluation,
)
from app.recommendation.tmdb_eval_cache import DEFAULT_CACHE_FILE

DEFAULT_DELTA_METRICS = (
    "recall@{k}",
    "ndcg@{k}",
    "novelty",
    "dislike_recall@{k}",
    "like_dislike_auc",
)
# Against another code version, the launch checks matter as much as accuracy.
AGAINST_DELTA_METRICS = (
    *DEFAULT_DELTA_METRICS,
    "director_over_limit",
    "franchise_repeat",
    "gem_famous_share",
)


def _parse_assignments(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in text.split(","):
        name, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"expected weight=value, got {part!r}")
        out[name.strip()] = float(value)
    return out


def _variants(args: argparse.Namespace) -> dict[str, Strategy]:
    strategies: dict[str, Strategy] = {}
    for sweep in args.sweep or []:
        name, sep, values = sweep.partition("=")
        if not sep:
            raise ValueError(f"--sweep expects weight=v1,v2,..., got {sweep!r}")
        for raw in values.split(","):
            value = float(raw)
            strategies[f"production[{name}={value:g}]"] = production_variant(**{name: value})
    for variant in args.variant or []:
        label, sep, assignments = variant.partition(":")
        if not sep:
            raise ValueError(f"--variant expects name:weight=value,..., got {variant!r}")
        strategies[label] = production_variant(**_parse_assignments(assignments))
    return strategies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline recommender evaluation")
    parser.add_argument(
        "--dataset", choices=("synthetic", "movielens", "movielens-tmdb"), default="synthetic"
    )
    parser.add_argument("--path", type=Path, help="MovieLens directory (ml-latest-small)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_FILE, help="TMDb cache (JSONL)")
    parser.add_argument(
        "--catalog-size",
        type=int,
        default=PRODUCTION_CATALOG_SIZE,
        help="movielens-tmdb: candidates = this many most-voted titles; 0 = all",
    )
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="Slate size to score")
    parser.add_argument("--users", type=int, default=200, help="Users to sample; 0 = all")
    parser.add_argument("--titles", type=int, default=800)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sweep", action="append", help="weight=v1,v2,... on production")
    parser.add_argument("--variant", action="append", help="name:weight=v,... on production")
    parser.add_argument("--only-variants", action="store_true")
    parser.add_argument(
        "--strategies", help="Comma-separated subset of the standard strategies to run"
    )
    parser.add_argument("--baseline", default="production", help="Paired deltas against this")
    parser.add_argument("--json", type=Path, help="Write summary metrics (and deltas) here")
    parser.add_argument(
        "--against",
        type=Path,
        help="--json of another code version's run: paired deltas per same-named strategy",
    )
    args = parser.parse_args(argv)

    max_users = args.users or None
    if args.dataset in {"movielens", "movielens-tmdb"} and not args.path:
        parser.error(f"--path is required for --dataset {args.dataset}")
    if args.dataset == "movielens":
        dataset = load_movielens_dataset(args.path, max_users=max_users, seed=args.seed)
    elif args.dataset == "movielens-tmdb":
        dataset = load_movielens_tmdb_dataset(
            args.path,
            args.cache,
            catalog_size=args.catalog_size or None,
            max_users=max_users,
            seed=args.seed,
        )
    else:
        dataset = build_synthetic_dataset(
            n_titles=args.titles, n_users=args.users or 200, seed=args.seed
        )

    strategies: dict[str, Strategy] = {}
    if not args.only_variants:
        strategies.update(DEFAULT_STRATEGIES)
        if args.dataset == "movielens-tmdb":
            strategies["popular_ml"] = rating_count_strategy
        if args.strategies:
            wanted = {s.strip() for s in args.strategies.split(",")}
            strategies = {name: s for name, s in strategies.items() if name in wanted}
    variants = _variants(args)
    if variants and args.baseline not in strategies:
        strategies[args.baseline] = DEFAULT_STRATEGIES.get(args.baseline) or production_variant()
    strategies.update(variants)

    print(
        f"dataset={dataset.name} titles={len(dataset.titles)} "
        f"users_evaluated={len(dataset.users)} k={args.k} strategies={len(strategies)}",
        file=sys.stderr,
    )
    if dataset.notes:
        print(json.dumps(dataset.notes), file=sys.stderr)

    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 50 == 0 or done == total:
            print(f"  {done}/{total} users, {time.monotonic() - started:.0f}s", file=sys.stderr)

    run = run_evaluation(dataset, strategies, k=args.k, progress=progress)
    print(format_report(run.summary, k=args.k, columns=(*ACCURACY_COLUMNS, *BEYOND_COLUMNS)))
    print()
    print(format_report(run.summary, k=args.k, columns=SAFETY_COLUMNS))

    deltas = {}
    if args.baseline in run.per_user and len(run.per_user) > 1:
        metrics = [m.format(k=args.k) for m in DEFAULT_DELTA_METRICS]
        deltas = paired_deltas(run, baseline=args.baseline, metrics=metrics)
        print()
        print(format_deltas(deltas, baseline=args.baseline))

    if args.against:
        previous = json.loads(args.against.read_text(encoding="utf-8"))
        if previous.get("dataset") != dataset.name or previous.get("k") != args.k:
            parser.error("--against: the previous run used another dataset or slate size")
        metrics = [m.format(k=args.k) for m in AGAINST_DELTA_METRICS]
        against = paired_deltas_against(run, previous, metrics=metrics)
        print()
        print(format_deltas(against, baseline=f"{args.against.name} (same strategy)"))

    if args.json:
        args.json.write_text(
            json.dumps(
                {"dataset": dataset.name, "notes": dataset.notes, "k": args.k,
                 "summary": run.summary, "deltas": deltas,
                 "user_ids": run.user_ids, "per_user": run.per_user},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
