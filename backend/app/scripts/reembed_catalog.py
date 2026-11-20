"""Rebuild title embeddings + feature_snapshot from DB relations (no TMDb).

Use after FEATURE_SCHEMA_VERSION changes so ranking uses richer people/lang/
country/tone signals without a full re-ingest. Always ends by refreshing the
catalog-wide popularity / vote-count percentiles in ``Title.extra``.

    python -m app.scripts.reembed_catalog
    python -m app.scripts.reembed_catalog --limit 100
    python -m app.scripts.reembed_catalog --percentiles-only   # backfill percentiles only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.application.catalog_percentiles import refresh_catalog_percentiles
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.models.catalog import Credit, Title
from app.infrastructure.db.session import async_session_factory
from app.recommendation.embeddings import (
    FEATURE_SCHEMA_VERSION,
    PersonSignal,
    build_title_signals,
)


def _people_from_title(title: Title) -> list[PersonSignal]:
    people: list[PersonSignal] = []
    for credit in title.credits or []:
        person = credit.person
        if person is None or not person.name:
            continue
        if credit.credit_type == "cast":
            people.append(
                PersonSignal(
                    name=person.name,
                    role="cast",
                    billing_order=credit.billing_order,
                )
            )
        else:
            job = (credit.job or "").strip()
            role = "director" if job == "Director" else "writer"
            people.append(PersonSignal(name=person.name, role=role))
    return people


async def main(*, limit: int | None, force: bool, percentiles_only: bool = False) -> int:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    log = logging.getLogger("reembed")

    async with async_session_factory() as session:
        if percentiles_only:
            changed = await refresh_catalog_percentiles(session)
            await session.commit()
            stats_only = {"catalog_percentiles_changed": changed}
            log.info("Catalog percentiles refreshed: %s", stats_only)
            print(stats_only)
            return 0

        q = (
            select(Title)
            .options(
                selectinload(Title.genres),
                selectinload(Title.keywords),
                selectinload(Title.credits).selectinload(Credit.person),
            )
            .order_by(Title.popularity.desc())
        )
        if limit:
            q = q.limit(limit)
        titles = list((await session.scalars(q)).all())

        updated = skipped = 0
        for title in titles:
            extra = title.extra or {}
            version = int(extra.get("feature_schema_version") or 0)
            if not force and version >= FEATURE_SCHEMA_VERSION and title.embedding is not None:
                skipped += 1
                continue

            countries = list(extra.get("origin_countries") or [])
            year = title.release_date.year if title.release_date else None
            embedding, _features, meta = build_title_signals(
                name=title.name,
                overview=title.overview,
                genres=[g.name for g in title.genres],
                keywords=[k.name for k in title.keywords],
                people=_people_from_title(title),
                media_type=title.media_type,
                release_year=year,
                runtime=title.runtime,
                original_language=title.original_language,
                countries=countries,
            )
            # Preserve non-feature keys (demo flag, catalog percentiles, the TMDb
            # collection used by the franchise cap): they come from ingest, not here.
            merged = {k: v for k, v in extra.items() if k not in meta}
            merged.update(meta)
            title.embedding = embedding
            title.extra = merged
            updated += 1

        await session.commit()
        # Whole catalog, regardless of --limit: percentiles are relative to all titles.
        percentiles_changed = await refresh_catalog_percentiles(session)
        await session.commit()
        stats = {
            "scanned": len(titles),
            "updated": updated,
            "skipped_current": skipped,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "catalog_percentiles_changed": percentiles_changed,
        }
        log.info("Re-embed complete: %s", stats)
        print(stats)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild title embeddings from DB relations")
    parser.add_argument("--limit", type=int, default=None, help="Max titles to process")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if feature_schema_version is current",
    )
    parser.add_argument(
        "--percentiles-only",
        action="store_true",
        help="Only refresh catalog popularity / vote-count percentiles (no re-embed)",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(
            asyncio.run(
                main(limit=args.limit, force=args.force, percentiles_only=args.percentiles_only)
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
