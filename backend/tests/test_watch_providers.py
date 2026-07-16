from app.application.watch_providers import (
    normalize_region,
    parse_watch_providers_payload,
)


def test_normalize_region_defaults_and_validates() -> None:
    assert normalize_region(None) == "US"
    assert normalize_region("us") == "US"
    assert normalize_region("  de ") == "DE"
    assert normalize_region("usa") == "US"  # invalid → default
    assert normalize_region("xx", default="GB") == "XX"
    assert normalize_region("bad", default="nope") == "US"


def test_parse_empty_region() -> None:
    out = parse_watch_providers_payload({"results": {}}, region="US")
    assert out.region == "US"
    assert out.available is False
    assert out.flatrate == []


def test_parse_providers_sorted_and_deduped() -> None:
    payload = {
        "results": {
            "US": {
                "link": "https://www.themoviedb.org/movie/550/watch?locale=US",
                "flatrate": [
                    {
                        "logo_path": "/netflix.jpg",
                        "provider_id": 8,
                        "provider_name": "Netflix",
                        "display_priority": 1,
                    },
                    {
                        "logo_path": "/disney.jpg",
                        "provider_id": 337,
                        "provider_name": "Disney Plus",
                        "display_priority": 0,
                    },
                    {
                        "logo_path": "/netflix.jpg",
                        "provider_id": 8,
                        "provider_name": "Netflix",
                        "display_priority": 1,
                    },
                ],
                "rent": [
                    {
                        "logo_path": "/apple.jpg",
                        "provider_id": 2,
                        "provider_name": "Apple TV",
                        "display_priority": 3,
                    }
                ],
                "buy": [],
            }
        }
    }
    out = parse_watch_providers_payload(payload, region="us")
    assert out.available is True
    assert out.link and "watch" in out.link
    assert [p.name for p in out.flatrate] == ["Disney Plus", "Netflix"]
    assert out.flatrate[0].logo_url == "https://image.tmdb.org/t/p/w92/disney.jpg"
    assert len(out.flatrate) == 2
    assert out.rent[0].name == "Apple TV"
    assert out.buy == []
    assert "JustWatch" in out.attribution


def test_parse_other_region_missing() -> None:
    payload = {
        "results": {
            "US": {
                "flatrate": [
                    {
                        "provider_id": 8,
                        "provider_name": "Netflix",
                        "logo_path": "/n.jpg",
                        "display_priority": 0,
                    }
                ]
            }
        }
    }
    out = parse_watch_providers_payload(payload, region="GB")
    assert out.region == "GB"
    assert out.available is False
