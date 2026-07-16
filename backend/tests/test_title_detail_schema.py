from types import SimpleNamespace
from uuid import uuid4

from app.api.schemas.titles import TitleDetailOut


def test_title_detail_includes_ordered_credits_and_keywords() -> None:
    genre = SimpleNamespace(id=uuid4(), name="Thriller")
    keyword = SimpleNamespace(name="neo-noir")
    person_cast = SimpleNamespace(name="Lead Actor", profile_path="/p.jpg")
    person_dir = SimpleNamespace(name="Jane Director", profile_path=None)
    cast = SimpleNamespace(
        credit_type="cast",
        job=None,
        character="Hero",
        billing_order=0,
        person=person_cast,
    )
    crew = SimpleNamespace(
        credit_type="crew",
        job="Director",
        character=None,
        billing_order=None,
        person=person_dir,
    )
    title = SimpleNamespace(
        id=uuid4(),
        media_type="movie",
        name="Test Film",
        overview="A test.",
        release_date=None,
        runtime=100,
        popularity=10.0,
        vote_average=7.5,
        poster_path="/x.jpg",
        backdrop_path=None,
        original_language="en",
        genres=[genre],
        keywords=[keyword],
        credits=[crew, cast],
    )
    out = TitleDetailOut.from_title_detail(title)
    assert out.name == "Test Film"
    assert out.poster_url and out.poster_url.endswith("/x.jpg")
    assert out.keywords == ["neo-noir"]
    assert len(out.credits) == 2
    assert out.credits[0].name == "Lead Actor"
    assert out.credits[0].credit_type == "cast"
    assert out.credits[1].job == "Director"
