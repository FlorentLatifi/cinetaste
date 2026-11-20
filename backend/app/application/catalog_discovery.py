"""Which TMDb discover queries feed the catalog, and how they are walked.

Kept separate from ``catalog_ingest`` because planning needs only TMDb (no
database): ``--dry-run`` uses it to report what a run *would* fetch.

Passes, in priority order (a run stopped halfway keeps the most valuable ones):

1. ``mainstream`` -- popularity.desc and vote_count.desc, the original catalog.
2. ``rated``      -- vote_average.desc above a vote floor: well-rated films that
                     are not currently popular.
3. ``language``   -- top-rated films per original language (non-English cinema).
4. ``decade``     -- top-rated films per decade, so older films are reachable.
5. TV gets the ``rated`` and ``language`` passes only, at a fraction of the
   movie depth: the catalog stays movie-first.

Vote floors matter: TMDb's vote_average.desc without one is dominated by titles
with a handful of 10/10 votes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.infrastructure.tmdb.client import MAX_DISCOVER_PAGE

logger = logging.getLogger(__name__)

DISCOVER_SORTS = ("popularity.desc", "vote_count.desc")
DISCOVER_PAGE_SIZE = 20

DEFAULT_LANGUAGES = ("ko", "ja", "fr", "es", "it", "de", "hi", "pt", "zh", "da", "sv", "fa")
DEFAULT_DECADES = tuple(range(1940, 2030, 10))

_LANGUAGE_RE = re.compile(r"^[a-z]{2}$")


class DiscoverApi(Protocol):
    async def discover(self, media_type: str, **kwargs: Any) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DiscoveryPlan:
    """Depth of the discovery passes beyond the mainstream ones.

    Pages are 20 titles each; 0 switches a pass off. Defaults target roughly
    3,500-4,500 unique movies and under a quarter as many TV shows.
    """

    rated_pages: int = 30
    rated_min_votes: int = 250
    decades: tuple[int, ...] = DEFAULT_DECADES
    decade_pages: int = 10
    decade_min_votes: int = 200
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    language_pages: int = 6
    language_min_votes: int = 150
    tv_rated_pages: int = 8
    tv_rated_min_votes: int = 200
    tv_language_pages: int = 2
    tv_language_min_votes: int = 100

    def __post_init__(self) -> None:
        for name in (
            "rated_pages",
            "decade_pages",
            "language_pages",
            "tv_rated_pages",
            "tv_language_pages",
        ):
            value = getattr(self, name)
            if not 0 <= value <= MAX_DISCOVER_PAGE:
                raise ValueError(f"{name} must be 0..{MAX_DISCOVER_PAGE}, got {value}")
        for name in (
            "rated_min_votes",
            "decade_min_votes",
            "language_min_votes",
            "tv_rated_min_votes",
            "tv_language_min_votes",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        for code in self.languages:
            if not _LANGUAGE_RE.match(code):
                raise ValueError(f"language must be a lowercase ISO 639-1 code, got {code!r}")
        this_year = date.today().year
        for decade in self.decades:
            if decade % 10 or not 1880 <= decade <= this_year:
                raise ValueError(f"decade must be a multiple of 10 in 1880..{this_year}, got {decade}")

    @classmethod
    def disabled(cls) -> DiscoveryPlan:
        """Mainstream passes only (the pre-discovery behaviour)."""
        return cls(rated_pages=0, decade_pages=0, language_pages=0, tv_rated_pages=0, tv_language_pages=0)


@dataclass(frozen=True)
class DiscoverPass:
    label: str
    media_type: str
    pages: int
    # Mainstream passes refresh titles already in the catalog (popularity moves);
    # discovery passes do not, so a re-run or resume costs no detail requests.
    refresh: bool
    kwargs: dict[str, Any] = field(default_factory=dict)


def build_passes(
    *, pages: int, include_tv: bool, plan: DiscoveryPlan | None = None
) -> list[DiscoverPass]:
    """All passes in priority order. ``plan=None`` means mainstream only."""
    plan = plan or DiscoveryPlan.disabled()
    media_types = ("movie", "tv") if include_tv else ("movie",)
    passes: list[DiscoverPass] = []

    # Mainstream: kwargs stay exactly page + sort_by (client default vote floor).
    for media_type in media_types:
        for sort_by in DISCOVER_SORTS:
            passes.append(
                DiscoverPass(
                    f"{media_type}:{sort_by}", media_type, pages, True, {"sort_by": sort_by}
                )
            )

    def add(label: str, media_type: str, n_pages: int, **kwargs: Any) -> None:
        if n_pages > 0:
            passes.append(DiscoverPass(label, media_type, n_pages, False, kwargs))

    add(
        "movie:rated",
        "movie",
        plan.rated_pages,
        sort_by="vote_average.desc",
        vote_count_gte=plan.rated_min_votes,
    )
    for lang in plan.languages:
        add(
            f"movie:lang:{lang}",
            "movie",
            plan.language_pages,
            sort_by="vote_average.desc",
            vote_count_gte=plan.language_min_votes,
            original_language=lang,
        )
    for decade in plan.decades:
        add(
            f"movie:decade:{decade}s",
            "movie",
            plan.decade_pages,
            sort_by="vote_average.desc",
            vote_count_gte=plan.decade_min_votes,
            released_from=f"{decade}-01-01",
            released_to=f"{decade + 9}-12-31",
        )
    if include_tv:
        add(
            "tv:rated",
            "tv",
            plan.tv_rated_pages,
            sort_by="vote_average.desc",
            vote_count_gte=plan.tv_rated_min_votes,
        )
        for lang in plan.languages:
            add(
                f"tv:lang:{lang}",
                "tv",
                plan.tv_language_pages,
                sort_by="vote_average.desc",
                vote_count_gte=plan.tv_language_min_votes,
                original_language=lang,
            )
    return passes


class Throttle:
    """Spaces request starts at least ``1 / max_rps`` seconds apart.

    TMDb allows roughly 50 requests/second per IP; the default of 25 leaves
    headroom. Shared by discover and detail requests. ``max_rps <= 0`` disables.
    """

    def __init__(self, max_rps: float = 0.0) -> None:
        self._interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if not self._interval:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            start = max(now, self._next)
            self._next = start + self._interval
        if start > now:
            await asyncio.sleep(start - now)


@dataclass
class PassReport:
    label: str
    media_type: str
    pages_requested: int
    pages_fetched: int = 0
    titles_seen: int = 0
    new_unique: int = 0
    failed: bool = False


@dataclass
class Targets:
    """Unique (media_type, tmdb_id) pairs in first-seen order, plus per-pass reports."""

    items: dict[tuple[str, int], bool] = field(default_factory=dict)  # value: refresh?
    reports: list[PassReport] = field(default_factory=list)
    discover_requests: int = 0

    @property
    def failed_passes(self) -> list[str]:
        return [r.label for r in self.reports if r.failed]


async def collect_targets(
    tmdb: DiscoverApi,
    passes: list[DiscoverPass],
    *,
    throttle: Throttle | None = None,
) -> Targets:
    """Walk every pass page by page.

    A failing page ends *that pass* (logged and reported) rather than the run:
    discover is cheap to repeat and the passes are independent. An empty page
    also ends a pass early -- TMDb has nothing deeper for that query.
    """
    throttle = throttle or Throttle()
    out = Targets()
    for p in passes:
        report = PassReport(p.label, p.media_type, p.pages)
        out.reports.append(report)
        for page in range(1, p.pages + 1):
            await throttle.wait()
            out.discover_requests += 1
            try:
                results = await tmdb.discover(p.media_type, page=page, **p.kwargs)
            except Exception:
                logger.exception("tmdb_discover_failed pass=%s page=%s", p.label, page)
                report.failed = True
                break
            if not results:
                break
            report.pages_fetched += 1
            for item in results:
                if not item.get("id"):
                    continue
                key = (p.media_type, int(item["id"]))
                report.titles_seen += 1
                if key not in out.items:
                    out.items[key] = p.refresh
                    report.new_unique += 1
                elif p.refresh:
                    out.items[key] = True
        logger.info(
            "discover_pass pass=%s pages=%s/%s seen=%s new=%s failed=%s",
            p.label,
            report.pages_fetched,
            p.pages,
            report.titles_seen,
            report.new_unique,
            report.failed,
        )
    return out


def max_discover_requests(passes: list[DiscoverPass]) -> int:
    return sum(p.pages for p in passes)
