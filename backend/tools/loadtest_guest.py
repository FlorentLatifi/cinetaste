"""Load test for the public guest endpoints. LOCAL STACKS ONLY -- never production.

    pip install locust            # optional dev tool, deliberately not in requirements*.txt

    # 1) A local stack that looks like production: ONE worker, no Redis, a seeded catalog.
    #    Rate limits off, otherwise the per-IP guest budget (30/min) stops the test at once.
    RATE_LIMIT_ENABLED=false REDIS_URL= \
      uvicorn app.main:app --port 8000          # ...or: docker compose up (set the same env)
    python tools/profile_guest_slate.py --seed 4700   # a catalog big enough to matter

    # 2) Run it (web UI on :8089, or headless):
    locust -f tools/loadtest_guest.py --host http://localhost:8000 \
           --headless -u 20 -r 5 -t 60s

Two kinds of visitor, because they hit different code:

* ``NewVisitor``      answers differently every time  -> every slate is ranked (cache miss).
* ``ReturningVisitor`` repeats one fixed set of answers -> served from the slate cache.

``-u`` splits roughly 4:1 new:returning. Run only one with ``NewVisitor`` / ``ReturningVisitor``
as the last argument. 503 ``guest_busy`` is the concurrency cap working, not a failure of the
test: it is reported as its own row (and the visitor waits out ``Retry-After``), so the
latency of served slates and the share of load shed are read separately.

What to look at: p95 of ``POST slate (ranked)`` against the 800 ms target, the 503 share, and
that ``GET cards`` / ``GET /health`` stay fast while guests are being shed.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gevent  # noqa: E402  (ships with locust)
from local_only import assert_local_target  # noqa: E402
from locust import HttpUser, between, events, task  # noqa: E402

API = os.environ.get("LOADTEST_API_PREFIX", "/api/v1")
# Seconds a simulated visitor pauses between actions. Real people take a few seconds per
# card; for a stress run set LOADTEST_THINK_MIN=0 LOADTEST_THINK_MAX=0.05 (closed loop).
THINK = (float(os.environ.get("LOADTEST_THINK_MIN", "0.5")), float(os.environ.get("LOADTEST_THINK_MAX", "2")))
RATINGS = ["rate_4", "rate_4", "rate_3", "rate_1", "rate_2", "haven't_seen"]


@events.test_start.add_listener
def _local_only(environment, **_kwargs) -> None:  # noqa: ANN001, ANN003
    assert_local_target(environment.host or "")


def _deck(client) -> list[str]:  # noqa: ANN001
    with client.get(f"{API}/guest/cards?limit=40", name="GET cards", catch_response=True) as res:
        if res.status_code != 200:
            res.failure(f"cards {res.status_code}")
            return []
        res.success()
        return [item["id"] for item in res.json()["items"]]


def _answers(deck: list[str], rng: random.Random) -> list[dict[str, str]]:
    picks = rng.sample(deck, min(8, len(deck)))
    answers = [{"title_id": t, "action": rng.choice(RATINGS)} for t in picks]
    answers[0]["action"] = "rate_4"  # the API needs at least one positive
    return answers


def _post_slate(client, answers: list[dict[str, str]], name: str) -> None:  # noqa: ANN001
    with client.post(
        f"{API}/guest/recommendations",
        json={"reactions": answers, "limit": 12},
        name=name,
        catch_response=True,
    ) as res:
        if res.status_code == 200:
            res.success()
            return
        retry_after = res.headers.get("Retry-After")
        if res.status_code == 503 and retry_after:
            # The cap shedding load is the designed behaviour. Keep it out of the latency
            # of requests that were served, and behave like a client that obeys the header.
            res.request_meta["name"] = "POST slate (shed: 503 guest_busy)"
            res.success()
            gevent.sleep(min(float(retry_after), 10))
        else:
            res.failure(f"{res.status_code} {res.text[:120]}")


class NewVisitor(HttpUser):
    weight = 4
    wait_time = between(*THINK)

    def on_start(self) -> None:
        self.rng = random.Random()
        self.deck = _deck(self.client)

    @task
    def ranked_slate(self) -> None:
        if len(self.deck) >= 3:
            _post_slate(self.client, _answers(self.deck, self.rng), "POST slate (ranked)")


class ReturningVisitor(HttpUser):
    weight = 1
    wait_time = between(*THINK)

    def on_start(self) -> None:
        deck = _deck(self.client)
        # Same answers for every returning visitor: a reload, or the SPA replaying them.
        self.answers = _answers(sorted(deck), random.Random(1234)) if len(deck) >= 3 else []

    @task
    def cached_slate(self) -> None:
        if self.answers:
            _post_slate(self.client, self.answers, "POST slate (repeat answers)")

    @task(2)
    def cards(self) -> None:
        self.client.get(f"{API}/guest/cards?limit=15", name="GET cards")
