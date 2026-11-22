"""Tests for human-readable recommendation explanations."""

from uuid import uuid4

from app.recommendation.embeddings import PersonSignal, features_from_title
from app.recommendation.explanations import (
    EXPLAIN_MEMORY_KEY,
    build_anchor_from_title,
    build_reasons,
    merge_explain_memory,
    strip_explain_memory,
)
from app.recommendation.pipeline import explain


def test_strip_and_merge_explain_memory() -> None:
    features = {"genre:thriller": 1.2, "person:director:nolan": 2.0}
    anchors = [
        build_anchor_from_title(
            title_id=uuid4(),
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={"person:director:christopher nolan": 2.2, "tone:cerebral": 1.0},
            year=2010,
        )
    ]
    merged = merge_explain_memory(features, anchors)
    assert EXPLAIN_MEMORY_KEY in merged
    scoring, memory = strip_explain_memory(merged)
    assert "genre:thriller" in scoring
    assert EXPLAIN_MEMORY_KEY not in scoring
    assert len(memory["anchors"]) == 1
    assert memory["anchors"][0]["name"] == "Inception"


def test_because_you_liked_same_director() -> None:
    inception_id = uuid4()
    prestige_id = uuid4()
    anchors = [
        build_anchor_from_title(
            title_id=inception_id,
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={
                "person:director:christopher nolan": 2.2,
                "kw:mind-bending": 0.9,
                "tone:cerebral": 1.0,
                "genre:science fiction": 1.0,
            },
        ),
        build_anchor_from_title(
            title_id=prestige_id,
            name="The Prestige",
            event_type="rate_3",
            weight=1.0,
            feature_snapshot={
                "person:director:christopher nolan": 2.2,
                "kw:mind-bending": 0.9,
                "tone:cerebral": 1.0,
                "genre:drama": 1.0,
            },
        ),
    ]
    candidate = features_from_title(
        genres=["Science Fiction", "Thriller"],
        keywords=["mind-bending", "time travel"],
        people=[PersonSignal("Christopher Nolan", "director")],
        release_year=2014,
        runtime=169,
        media_type="movie",
    )
    user_features = {
        "person:director:christopher nolan": 2.5,
        "tone:cerebral": 1.5,
        "genre:science fiction": 1.2,
        "kw:mind-bending": 1.0,
    }
    reasons = build_reasons(
        user_features=user_features,
        explain_memory={"version": 1, "anchors": anchors},
        title_name="Interstellar",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Science Fiction", "Drama"],
        similarity=0.72,
    )
    assert reasons
    primary = reasons[0]
    assert primary.code == "because_you_liked"
    assert "Inception" in primary.message or "Prestige" in primary.message
    assert "director" in primary.message.lower() or "Nolan" in primary.message


def test_taste_blend_is_specific() -> None:
    candidate = features_from_title(
        genres=["Thriller"],
        keywords=["psychological thriller", "moral dilemma"],
        people=[],
        release_year=2019,
        runtime=120,
        media_type="movie",
    )
    # Force tone via keyword rules
    candidate["tone:tense"] = 1.0
    candidate["kw:psychological thriller"] = 0.9
    user_features = {
        "tone:tense": 2.0,
        "genre:thriller": 1.8,
        "kw:psychological thriller": 1.2,
    }
    reasons = build_reasons(
        user_features=user_features,
        explain_memory={"anchors": []},
        title_name="Gone Girl",
        title_extra={"feature_snapshot": candidate},
        title_genres=["Thriller"],
        similarity=0.6,
    )
    assert any(r.code == "taste_blend" for r in reasons)
    blend = next(r for r in reasons if r.code == "taste_blend")
    assert "Strong match" in blend.message
    assert "thriller" in blend.message.lower()


def test_explain_wrapper_reads_memory_from_features() -> None:
    feats = features_from_title(
        genres=["Drama"],
        keywords=[],
        people=[PersonSignal("Nolan", "director")],
        release_year=2010,
        runtime=120,
        media_type="movie",
    )
    anchors = [
        build_anchor_from_title(
            title_id=uuid4(),
            name="Inception",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={"person:director:nolan": 2.2},
        )
    ]
    user = merge_explain_memory(
        {"person:director:nolan": 2.0, "genre:drama": 1.0},
        anchors,
    )
    reasons = explain(
        user_features=user,
        title_extra={"feature_snapshot": feats},
        title_genres=["Drama"],
        similarity=0.7,
    )
    assert reasons
    assert all(r.message for r in reasons)
    # Should not dump mechanical "Matches genres you like" as only line when director exists
    assert reasons[0].code in {"because_you_liked", "same_director", "taste_blend", "similar_style"}


# --- Copy regressions (live bugs) -------------------------------------------------

import re  # noqa: E402

from app.recommendation.embeddings import build_title_signals  # noqa: E402
from app.recommendation.explanations import _genre_phrase, _pretty_person  # noqa: E402

# Strings seen live that must never be produced again.
BANNED = [
    "mysterys", "science fictions", "crimes", "Dicaprio", "Gordon-levitt", "Mcconaughey",
    "US cinema", "(en)", "language lane", "1950s threads", "action heavy action",
    # Seen live after the first copy pass (22 Nov 2026).
    "same director +", "a shared italy theme", "(same director —",
]


def _reasons(user, cand, *, anchors=None, genres=("Drama",), extra=None, name="Candidate"):
    title_extra = {"feature_snapshot": cand, **(extra or {})}
    return build_reasons(
        user_features=user,
        explain_memory={"anchors": anchors or []},
        title_name=name,
        title_extra=title_extra,
        title_genres=list(genres),
        similarity=0.3,
        max_reasons=6,
    )


def _all_text(reasons) -> str:
    return " | ".join(r.message for r in reasons)


def test_person_names_fallback_casing() -> None:
    assert _pretty_person("leonardo dicaprio") == "Leonardo DiCaprio"
    assert _pretty_person("joseph gordon-levitt") == "Joseph Gordon-Levitt"
    assert _pretty_person("matthew mcconaughey") == "Matthew McConaughey"
    assert _pretty_person("conan o'brien") == "Conan O'Brien"
    assert _pretty_person("ludwig van beethoven") == "Ludwig van Beethoven"
    # A stored display name always wins over guessing.
    assert _pretty_person("jj abrams", "J.J. Abrams") == "J.J. Abrams"


def test_title_signals_carry_display_names() -> None:
    _emb, features, meta = build_title_signals(
        name="Inception",
        overview=None,
        genres=["Science Fiction"],
        keywords=[],
        people=[PersonSignal("Leonardo DiCaprio", "cast", 0), PersonSignal("Christopher Nolan", "director")],
        media_type="movie",
        release_year=2010,
        runtime=148,
    )
    assert meta["display_names"]["person:cast:leonardo dicaprio"] == "Leonardo DiCaprio"
    assert set(meta["display_names"]) <= set(features)


def test_display_names_from_title_and_anchor_are_used() -> None:
    cand = {"person:cast:jj abrams": 1.5, "person:director:jj abrams": 2.2, "genre:drama": 1.0}
    anchor = build_anchor_from_title(
        title_id=uuid4(),
        name="Super 8",
        event_type="rate_4",
        weight=1.55,
        feature_snapshot={"person:director:jj abrams": 2.2},
        display_names={"person:director:jj abrams": "J.J. Abrams", "person:cast:unused": "X"},
    )
    assert anchor["display_names"] == {"person:director:jj abrams": "J.J. Abrams"}
    text = _all_text(
        _reasons({"person:director:jj abrams": 2.0}, cand, anchors=[anchor])
    )
    assert "J.J. Abrams" in text
    # Title-level names cover people the anchors do not mention.
    text = _all_text(
        _reasons(
            {"person:cast:jj abrams": 2.0},
            cand,
            extra={"display_names": {"person:cast:jj abrams": "J.J. Abrams"}},
        )
    )
    assert "J.J. Abrams" in text


def test_genre_phrases_use_proper_labels() -> None:
    assert _genre_phrase(["mystery"]) == "mysteries"
    assert _genre_phrase(["science fiction"]) == "sci-fi"
    assert _genre_phrase(["crime"]) == "crime films"
    assert _genre_phrase(["documentary"]) == "documentaries"
    assert _genre_phrase(["mystery", "thriller"]) == "mysteries and thrillers"
    assert _genre_phrase(["unknown genre"]) == "unknown genre films"
    assert _genre_phrase([]) == "films"


def test_taste_blend_has_no_malformed_plurals_or_echoed_tones() -> None:
    for genre, tone, expect in (
        ("mystery", "tense", "tense mysteries"),
        ("science fiction", "bleak", "bleak sci-fi"),
        ("crime", "tense", "tense crime films"),
    ):
        cand = {f"genre:{genre}": 1.0, f"tone:{tone}": 1.0}
        reasons = _reasons({f"genre:{genre}": 1.5, f"tone:{tone}": 1.5}, cand)
        blend = next(r for r in reasons if r.code == "taste_blend")
        assert expect in blend.message
    # "action_heavy" on an action film only restates the genre: no blend at all.
    cand = {"genre:action": 1.0, "genre:drama": 0.7, "tone:action_heavy": 0.85}
    reasons = _reasons({"genre:action": 1.5, "genre:drama": 1.0, "tone:action_heavy": 1.5}, cand)
    assert not any(r.code == "taste_blend" for r in reasons)
    assert "action heavy" not in _all_text(reasons)


def test_origin_and_language_use_names_not_codes() -> None:
    cand = {"country:US": 0.8, "country:KR": 0.8, "lang:ko": 0.85, "lang:en": 0.85}
    user = {"country:KR": 1.5, "country:US": 1.0, "lang:ko": 1.5, "lang:en": 1.0}
    text = _all_text(_reasons(user, cand))
    assert "South Korean" in text or "American" in text
    assert "Korean-language" in text
    assert "US cinema" not in text and "(ko)" not in text and "(en)" not in text
    # English is nearly every title, and an unmapped code has no name: neither is cited.
    text = _all_text(_reasons({"lang:en": 1.5, "country:ZZ": 1.5}, {"lang:en": 0.85, "country:ZZ": 0.8}))
    assert "English" not in text and "ZZ" not in text


def test_year_and_decade_keywords_are_not_themes() -> None:
    for kw in ("1950s", "1990s", "21st century", "2015", "'80s"):
        cand = {f"kw:{kw}": 0.9, "genre:drama": 1.0}
        anchor = build_anchor_from_title(
            title_id=uuid4(),
            name="Old Film",
            event_type="rate_4",
            weight=1.55,
            feature_snapshot={"person:director:ann lee": 2.2, f"kw:{kw}": 0.9},
        )
        assert kw not in anchor["keywords"]
        cand["person:director:ann lee"] = 2.2
        text = _all_text(
            _reasons({"person:director:ann lee": 2.0, f"kw:{kw}": 1.0}, cand, anchors=[anchor])
        )
        assert kw not in text and "threads" not in text
        assert "same director" in text.lower() or "Ann Lee" in text


def test_keyword_with_words_and_digits_is_still_a_theme() -> None:
    reasons = _reasons({"kw:1984 dystopia": 1.0}, {"kw:1984 dystopia": 0.9})
    assert "1984 dystopia" in _all_text(reasons)


def test_anchor_requires_non_genre_overlap() -> None:
    anchor = build_anchor_from_title(
        title_id=uuid4(),
        name="Same Genre Only",
        event_type="rate_5",
        weight=2.0,
        feature_snapshot={"genre:thriller": 1.0, "person:director:someone else": 2.2},
    )
    cand = {"genre:thriller": 1.0, "person:director:another person": 2.2}
    reasons = _reasons({"genre:thriller": 1.5}, cand, anchors=[anchor], genres=("Thriller",))
    assert reasons
    # A shared genre can still be named by the genre reason, never as a "because" lead.
    assert not any(r.code == "because_you_liked" for r in reasons)
    # Add a shared keyword and the favorite may be cited again.
    anchor["keywords"] = ["heist"]
    cand["kw:heist"] = 0.9
    reasons = _reasons({"genre:thriller": 1.5, "kw:heist": 1.0}, cand, anchors=[anchor])
    assert reasons[0].code == "because_you_liked"
    assert "Same Genre Only" in reasons[0].message
    assert reasons[0].evidence["overlap"]["keywords"] == ["heist"]


def test_lead_wording_varies_but_keeps_the_evidence() -> None:
    anchor = build_anchor_from_title(
        title_id=uuid4(),
        name="Inception",
        event_type="rate_4",
        weight=1.55,
        feature_snapshot={"person:director:christopher nolan": 2.2, "tone:cerebral": 1.0},
        display_names={"person:director:christopher nolan": "Christopher Nolan"},
    )
    leads = set()
    for i in range(30):
        cand = {"person:director:christopher nolan": 2.2, "tone:cerebral": 1.0}
        msg = _reasons(
            {"person:director:christopher nolan": 2.0}, cand, anchors=[anchor], name=f"Film {i}"
        )[0].message
        assert "you rated inception highly" in msg.lower() and "Christopher Nolan" in msg
        leads.add(re.split(r"[ ,:(]", msg, maxsplit=1)[0])
    assert len(leads) >= 2, f"every card opens the same way: {leads}"
    # Same card, same wording on every call.
    assert _reasons({"person:director:christopher nolan": 2.0}, cand, anchors=[anchor], name="Film 3")[
        0
    ].message == _reasons(
        {"person:director:christopher nolan": 2.0}, cand, anchors=[anchor], name="Film 3"
    )[0].message


def test_discovery_copy_keeps_its_facts() -> None:
    from app.recommendation.pipeline import annotate_discovery_reasons

    out = annotate_discovery_reasons(
        [], is_hidden_gem=True, is_exploration=True, vote_average=7.84, popularity=9.0
    )
    gem = next(r for r in out if r.code == "hidden_gem")
    assert "★7.8" in gem.message and "hidden gem" in gem.message.lower()
    assert any(r.code == "discovery" for r in out)


def test_no_banned_string_across_a_wide_sweep() -> None:
    """Sweep genres x tones x countries x languages x keywords and scan every message."""
    genres = ["mystery", "science fiction", "crime", "documentary", "action", "tv movie", "kids"]
    tones = ["tense", "bleak", "action_heavy", "coming_of_age", "horror", "war", "cerebral"]
    people = [
        ("leonardo dicaprio", "cast"),
        ("joseph gordon-levitt", "cast"),
        ("matthew mcconaughey", "cast"),
        ("christopher nolan", "director"),
    ]
    for g in genres:
        for t in tones:
            for ccode, lcode in (("US", "en"), ("KR", "ko"), ("GB", "en")):
                cand = {
                    f"genre:{g}": 1.0, f"tone:{t}": 1.0, f"country:{ccode}": 0.8,
                    f"lang:{lcode}": 0.85, "decade:1950": 0.9, "kw:1950s": 0.9, "kw:heist": 0.9,
                }
                for role_name, role in people:
                    cand[f"person:{role}:{role_name}"] = 1.5
                user = {k: 1.5 for k in cand}
                anchor = build_anchor_from_title(
                    title_id=uuid4(), name="Fav", event_type="rate_4", weight=1.55,
                    feature_snapshot=cand,
                )
                for r in _reasons(user, cand, anchors=[anchor], genres=(g,)):
                    for bad in BANNED:
                        assert bad not in r.message, (bad, r.message)


def test_the_genre_fallback_names_tmdbs_main_genre_not_the_first_alphabetically() -> None:
    """The database lists genres by name; the snapshot keeps TMDb's order."""
    reasons = _reasons(
        {},
        {"genre:thriller": 1.0, "genre:action": 0.7},  # TMDb listed Thriller first
        genres=("Action", "Thriller"),  # as the database returns them
    )
    [fallback] = [r for r in reasons if r.code == "genre_fit"]
    assert fallback.evidence["genres"] == ["Thriller", "Action"]
    assert "thriller" in fallback.message.lower() and "action" not in fallback.message.lower()


def test_keyword_labels_restore_proper_nouns_only() -> None:
    from app.recommendation.explanations import _keyword_label

    assert _keyword_label("italy") == "Italy"
    assert _keyword_label("nasa") == "NASA"
    assert _keyword_label("new york city") == "New York City"
    assert _keyword_label("brooklyn, new york city") == "Brooklyn, New York City"
    assert _keyword_label("ancient rome") == "ancient Rome"
    assert _keyword_label("world war ii") == "World War II"
    assert _keyword_label("dystopia") == "dystopia"
    assert _keyword_label("time travel") == "time travel"


def test_director_lead_is_prose_in_every_variant() -> None:
    anchor = build_anchor_from_title(
        title_id=uuid4(),
        name="The Godfather",
        event_type="rate_3",
        weight=1.0,
        feature_snapshot={"person:director:francis ford coppola": 2.2, "kw:italy": 0.9},
        display_names={"person:director:francis ford coppola": "Francis Ford Coppola"},
    )
    for i in range(30):
        cand = {"person:director:francis ford coppola": 2.2, "kw:italy": 0.9}
        msg = _reasons(
            {"person:director:francis ford coppola": 2.0, "kw:italy": 1.0},
            cand,
            anchors=[anchor],
            name=f"Film {i}",
        )[0].message
        assert "Francis Ford Coppola" in msg
        assert " + " not in msg and "(same director" not in msg, msg
        assert "italy" not in msg, msg  # a place name keeps its capital


def test_overused_themes_give_way_to_other_shared_themes() -> None:
    cand = {"kw:dystopia": 0.9, "kw:artificial intelligence": 0.9, "genre:science fiction": 1.0}
    user = {"kw:dystopia": 2.0, "kw:artificial intelligence": 1.0}
    fresh = " ".join(r.message for r in _reasons(user, cand))
    assert "dystopia" in fresh
    rotated = build_reasons(
        user_features=user,
        explain_memory={"anchors": []},
        title_name="Candidate",
        title_extra={"feature_snapshot": cand},
        title_genres=["Science Fiction"],
        similarity=0.3,
        max_reasons=6,
        overused_keywords=frozenset({"dystopia"}),
    )
    themes = next(r for r in rotated if r.code == "similar_themes")
    # Still true evidence: the other shared theme, without the overused one.
    assert "artificial intelligence" in themes.message and "dystopia" not in themes.message
    # When it is the only shared theme, it is still named.
    only = build_reasons(
        user_features={"kw:dystopia": 2.0},
        explain_memory={"anchors": []},
        title_name="Candidate",
        title_extra={"feature_snapshot": {"kw:dystopia": 0.9}},
        title_genres=["Drama"],
        similarity=0.3,
        max_reasons=6,
        overused_keywords=frozenset({"dystopia"}),
    )
    assert any("dystopia" in r.message for r in only)


def test_a_slate_does_not_explain_everything_with_one_theme() -> None:
    from app.recommendation.pipeline import THEME_REPEAT_LIMIT, rank_titles
    from tests.conftest import FakeTitle

    seed_kw = ["heist", "revenge", "spy", "satire", "survival", "time loop", "space", "robot"]
    titles = [
        FakeTitle(
            name=f"Dystopia {i}",
            genres=["Science Fiction" if i % 2 else "Thriller"],
            keywords=["dystopia", seed_kw[i % len(seed_kw)]],
            people=[(f"director {i}", "director")],
        )
        for i in range(24)
    ]
    user = {"kw:dystopia": 2.0, **{f"kw:{k}": 1.0 for k in seed_kw}}
    slate = rank_titles(
        user_vector=list(titles[0].embedding),
        user_features=user,
        titles=titles,
        exclude_ids=set(),
        slate_size=12,
        mmr_lambda=0.8,
        exploration_slots=0,
    )
    # Every card shares "dystopia" and one other theme: after a few cards the
    # other theme is named instead (was 12 of 12 cards).
    naming = sum(1 for item in slate if any("dystopia" in r.message for r in item.reasons))
    assert naming <= THEME_REPEAT_LIMIT
