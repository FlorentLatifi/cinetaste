"""Seed a demo catalog without TMDb (offline / no API key).

Creates diverse synthetic titles with embeddings so onboarding + recs work locally.

    python -m app.scripts.seed_demo_catalog
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.models.catalog import Genre, Title, TitleGenre
from app.infrastructure.db.session import async_session_factory
from app.recommendation.embeddings import PersonSignal, build_title_signals

DEMO_TITLES = [
    ("Neon Harbor", "movie", ["Thriller", "Crime"], 2019, 118, 8.1, "A detective hunts a ghost in a rain-soaked port city."),
    ("Quiet Orbit", "movie", ["Science Fiction", "Drama"], 2021, 132, 7.9, "Astronauts confront isolation on a failing station."),
    ("Kitchen Wars", "movie", ["Comedy", "Drama"], 2018, 104, 7.2, "Rival chefs clash in a family restaurant empire."),
    ("Last Lantern", "movie", ["Fantasy", "Adventure"], 2016, 126, 7.6, "A cartographer maps a kingdom that refuses to stay still."),
    ("Paper Crown", "movie", ["Drama", "History"], 2014, 141, 8.0, "A young ruler learns the cost of mercy."),
    ("Static Bloom", "movie", ["Horror", "Mystery"], 2020, 97, 6.9, "A radio signal grows flowers that whisper secrets."),
    ("Mile Marker 9", "movie", ["Action", "Thriller"], 2022, 112, 7.1, "A courier uncovers a convoy conspiracy."),
    ("Soft Revolution", "movie", ["Romance", "Drama"], 2017, 109, 7.4, "Two organizers fall in love during a city-wide strike."),
    ("Glass Choir", "movie", ["Music", "Drama"], 2015, 121, 7.8, "A deaf composer rebuilds a shattered ensemble."),
    ("Copper Sky", "movie", ["Western", "Drama"], 2013, 128, 7.5, "A land surveyor redraws a dying frontier town."),
    ("Byte & Bone", "movie", ["Science Fiction", "Action"], 2023, 119, 7.0, "Hackers weaponize fossilized code from the deep past."),
    ("Harbor Hymn", "tv", ["Drama", "Mystery"], 2019, 52, 8.2, "A coastal town hides a multi-generational secret."),
    ("Signal Lost", "tv", ["Science Fiction", "Thriller"], 2021, 48, 7.7, "A network outage reveals parallel cities."),
    ("Family Recipe", "tv", ["Comedy"], 2020, 30, 7.3, "Siblings inherit a chaotic food truck."),
    ("Iron Garden", "tv", ["Fantasy", "Adventure"], 2018, 55, 7.9, "Gardeners tend living metal forests."),
    ("Courtroom 12", "tv", ["Crime", "Drama"], 2016, 44, 8.0, "Public defenders fight impossible cases."),
    ("Midnight Circuit", "movie", ["Action", "Science Fiction"], 2011, 115, 7.2, "Street racers pilot electric beasts under a neon ban."),
    ("The Long Table", "movie", ["Drama"], 2009, 98, 7.6, "Strangers share one dinner that rewrites their lives."),
    ("Winter Protocol", "movie", ["Thriller", "Mystery"], 2024, 123, 7.4, "A climate scientist vanishes with a map of future storms."),
    ("Ember Academy", "tv", ["Fantasy", "Drama"], 2022, 50, 7.5, "Students learn to shape fire without burning the world."),
    ("Cargo Saints", "movie", ["Action", "Crime"], 2015, 110, 6.8, "Smugglers turn protectors on a doomed freighter."),
    ("Violet Frequency", "movie", ["Romance", "Science Fiction"], 2018, 106, 7.3, "Lovers communicate only through a banned radio band."),
    ("Dust Court", "movie", ["Western", "Thriller"], 2020, 117, 7.1, "A circuit judge rides into a drought war."),
    ("North of Ordinary", "tv", ["Comedy", "Drama"], 2023, 32, 7.8, "A failed novelist runs a polar research canteen."),
    ("Atlas Unfolding", "movie", ["Adventure", "Drama"], 2012, 130, 7.7, "Cartographers chase a continent that moves overnight."),
    ("Blackboard Orbit", "movie", ["Drama", "Science Fiction"], 2017, 108, 7.0, "A teacher prepares kids for evacuation off-world."),
    ("River Without Maps", "movie", ["Mystery", "Drama"], 2014, 113, 7.9, "A translator decodes a murder in three languages."),
    ("Golden Fault Line", "movie", ["Thriller"], 2021, 101, 6.9, "An insurance adjuster uncovers engineered earthquakes."),
    ("Sparks & Silk", "movie", ["Romance", "Comedy"], 2019, 99, 7.2, "Rival fashion houses collaborate after a PR disaster."),
    ("The Quiet Brigade", "tv", ["Action", "Drama"], 2017, 46, 7.6, "Elite responders handle disasters before they are public."),
    ("Marrow City", "movie", ["Horror"], 2016, 94, 6.7, "A city feeds on the memories of its night shift."),
    ("Lattice", "movie", ["Science Fiction", "Mystery"], 2024, 125, 8.1, "A mathematician finds reality is a revision-controlled document."),
    ("Salt & Sirens", "movie", ["Fantasy", "Romance"], 2013, 111, 7.4, "Dockworkers bargain with sea spirits for safe passage."),
    ("Parallel PTA", "tv", ["Comedy"], 2021, 28, 7.1, "Parents discover the school exists in two timelines."),
    ("Crown of Ash", "movie", ["Fantasy", "Action"], 2010, 134, 7.8, "A disgraced knight guards a volcano that dreams."),
    ("Ledger of Light", "movie", ["Drama", "Crime"], 2022, 116, 7.5, "An auditor tracks stolen sunlight futures."),
]


async def main() -> None:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    log = logging.getLogger("seed_demo")

    async with async_session_factory() as session:
        existing = await session.scalar(select(func.count()).select_from(Title)) or 0
        if existing >= 20:
            log.info("Catalog already has %s titles; skipping demo seed", existing)
            print({"skipped": True, "title_count": int(existing)})
            return

        genre_cache: dict[str, Genre] = {}
        created = 0
        for idx, (name, media, genres, year, runtime, rating, overview) in enumerate(DEMO_TITLES):
            media_type = "tv" if media == "tv" else "movie"
            for gname in genres:
                if gname not in genre_cache:
                    genre = await session.scalar(select(Genre).where(Genre.name == gname))
                    if genre is None:
                        genre = Genre(id=uuid4(), name=gname)
                        session.add(genre)
                        await session.flush()
                    genre_cache[gname] = genre

            title = Title(
                id=uuid4(),
                media_type=media_type,
                name=name,
                original_name=name,
                overview=overview,
                release_date=date(year, 6, 1),
                runtime=runtime,
                popularity=float(80 - idx + rating),
                vote_average=float(rating),
                vote_count=1000 + idx * 17,
                poster_path=f"https://placehold.co/500x750/1a1c24/e8c27a/png?text={name.replace(' ', '+')}",
                backdrop_path=None,
                original_language="en",
                external_tmdb_id=900000 + idx,
            )
            people = [
                PersonSignal(name=f"Director {genres[0]}", role="director"),
                PersonSignal(name=f"Star {idx}", role="cast", billing_order=0),
            ]
            # Synthetic keywords carry light tone signal for local demos.
            demo_keywords = list(genres) + (
                ["feel-good"] if "Comedy" in genres or "Romance" in genres else ["suspense"]
            )
            embedding, _features, meta = build_title_signals(
                name=name,
                overview=overview,
                genres=genres,
                keywords=demo_keywords,
                people=people,
                media_type=media_type,
                release_year=year,
                runtime=runtime,
                popularity=title.popularity,
                vote_average=title.vote_average,
                original_language="en",
                countries=["US"],
            )
            title.embedding = embedding
            title.extra = {**meta, "demo": True}
            session.add(title)
            await session.flush()
            for gname in genres:
                session.add(TitleGenre(title_id=title.id, genre_id=genre_cache[gname].id))
            created += 1

        await session.commit()
        log.info("Seeded %s demo titles", created)
        print({"created": created})


if __name__ == "__main__":
    asyncio.run(main())
