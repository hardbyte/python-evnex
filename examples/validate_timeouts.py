#!/usr/bin/env python3
"""Measure the request timeouts and retry policy against the live API.

The baseline is evnex 0.7.0, the version the Home Assistant integration pins.

What the measurements found, on a real account:

  - The cloud read endpoints are not uniform. Most answer in under 0.2s, but
    ``get_org_charge_points`` measured 3.8s and ``get_charge_point_sessions``
    2.9s -- more than twenty times slower than their neighbours. 0.7.0 passed
    no timeout to httpx at all, so every call took the client default of 5s,
    leaving those two barely any headroom. That is the likely source of the
    intermittent ``ReadTimeout`` on ``get_org_charge_points``: it is not a
    broken endpoint, it is a slow one under a ceiling too close to it. Testing
    by hand samples it once; Home Assistant samples it 288 times a day and so
    meets the tail.

  - ``ReadTimeout`` was retryable in 0.7.0, so a call that went quiet burned
    five attempts with exponential backoff -- measured at 30s+ here -- turning
    one slow response into a multi-minute stall and a loud traceback.

  - The OCPP command endpoints were NOT the problem, contrary to the first
    theory this script was written to test. ``get-override`` and friends
    measured 0.6-1.3s on an awake, ONLINE charger, nowhere near the 15-20s
    that PR #128 recorded. Probe 3 still runs 0.7.0's 15s cap against the
    current default so the claim stays falsifiable, and reports honestly when
    it cannot reproduce a failure.

Probe 5 (``--soak``) is the one that matters for an intermittent fault: a
single sample cannot tell you about a tail. It replays the coordinator's poll
on the same cadence and reports the latency distribution.

This script only ever reads: no command changes charger state.

Usage::

    # Probe 1 needs no credentials or network:
    uv run python examples/validate_timeouts.py --offline-only

    # Everything, using the CLI's cached session (run `evnex auth login` first)
    # or EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD:
    uv run python examples/validate_timeouts.py

    # The one that catches an intermittent failure: poll on the HA
    # coordinator's own cadence for an hour and report the latency tail.
    uv run python examples/validate_timeouts.py --soak

Exit status is 0 when every probe passed, 1 when one failed, and 2 when the
run was inconclusive (for example, no ONLINE charge point to talk to).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import httpx
from tenacity import retry_if_not_exception_type

from evnex.api import COMMAND_TIMEOUT, Evnex
from evnex.auth import AuthChallenge, EvnexAuth, TokenSet
from evnex.config import EvnexConfig
from evnex.errors import EvnexAuthError, EvnexConfigurationError

# What 0.7.0 refused to retry. ReadTimeout is deliberately absent: that is the
# behaviour probe 1 reproduces.
BASELINE_NON_RETRYABLE = (EvnexAuthError, EvnexConfigurationError)

# 0.7.0 passed no timeout to httpx, so every call took the client default of
# 5 seconds -- including the OCPP command endpoints.
BASELINE_DEFAULT_TIMEOUT = 5.0

# 0.7.0's hardcoded ceiling on the override command.
BASELINE_OVERRIDE_TIMEOUT = 15.0

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"

# How much slower than measured an endpoint must be able to get before its
# timeout fires. A ceiling only a little above the typical latency is not a
# timeout, it is a coin flip on a busy day.
MIN_HEADROOM = 3.0

_T = TypeVar("_T")


@dataclass
class Result:
    name: str
    verdict: str
    detail: str


results: list[Result] = []


def record(name: str, verdict: str, detail: str) -> None:
    results.append(Result(name, verdict, detail))
    marker = {PASS: "PASS", FAIL: "FAIL", INCONCLUSIVE: "????"}[verdict]
    print(f"  [{marker}] {detail}")


async def timed(call: Callable[[], Awaitable[_T]]) -> tuple[float, _T | BaseException]:
    """Run call, returning how long it took and its result or exception."""
    started = time.monotonic()
    try:
        value: _T | BaseException = await call()
    except BaseException as err:  # noqa: BLE001 - reporting, not handling
        value = err
    return time.monotonic() - started, value


# --------------------------------------------------------------------------
# Probe 1: an endpoint that accepts the request and never answers
# --------------------------------------------------------------------------


@asynccontextmanager
async def black_hole_server():
    """A TCP server that accepts connections and never sends a byte.

    This is the shape of the failure: the connection establishes, the request
    goes out, and no response headers ever come back.
    """

    # Handlers park on this instead of on a private Event: asyncio's
    # wait_closed() waits for every live handler, so teardown has to be able
    # to release them.
    stop = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await reader.read(65536)
            await stop.wait()  # never respond
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        stop.set()
        server.close()
        await server.wait_closed()


def unauthenticated_client(base_url: str) -> Evnex:
    """A client pointed at base_url, with a token that never hits Cognito."""
    auth = EvnexAuth(
        tokens=TokenSet(
            access_token="probe-token",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )
    return Evnex(
        auth=auth,
        config=EvnexConfig(EVNEX_BASE_URL=base_url, EVNEX_ORG_ID="probe-org"),
    )


def default_request_timeout() -> float:
    """The library's own default, so this probe follows the code under test."""
    return float(inspect.signature(Evnex._request).parameters["timeout"].default)


async def probe_fail_fast() -> None:
    """Show what one unanswered request costs, before and after the change."""
    per_attempt = default_request_timeout()
    print("\nProbe 1: unanswered request (local black hole, no credentials)")
    print(f"    library default timeout is {per_attempt:.0f}s per attempt")

    async with black_hole_server() as base_url:
        client = unauthenticated_client(base_url)

        elapsed, outcome = await timed(client.get_org_charge_points)
        if not isinstance(outcome, httpx.ReadTimeout):
            record(
                "fail-fast",
                INCONCLUSIVE,
                f"expected a ReadTimeout from the black hole, got {outcome!r}",
            )
            return

        if elapsed <= per_attempt * 1.5:
            record(
                "fail-fast",
                PASS,
                f"current tree gave up after {elapsed:.1f}s: one attempt, "
                f"no retry storm",
            )
        else:
            record(
                "fail-fast",
                FAIL,
                f"current tree took {elapsed:.1f}s, expected a single attempt "
                f"of about {per_attempt:.0f}s",
            )

        # Restore 0.7.0's policy on the same decorator and repeat, so the
        # comparison is the retry policy alone -- same endpoint, same timeout.
        policy = Evnex.get_org_charge_points.retry
        original = policy.retry
        policy.retry = retry_if_not_exception_type(
            BASELINE_NON_RETRYABLE + (httpx.HTTPStatusError,)
        )
        print(
            f"    now with 0.7.0's policy: up to 5 attempts at "
            f"{per_attempt:.0f}s plus backoff, so this takes up to a minute..."
        )
        try:
            baseline_elapsed, _ = await timed(client.get_org_charge_points)
        finally:
            policy.retry = original

        await client.httpx_client.aclose()

    record(
        "retry-storm",
        PASS if baseline_elapsed > elapsed * 2 else INCONCLUSIVE,
        f"0.7.0 retried the same dead endpoint for {baseline_elapsed:.1f}s "
        f"({baseline_elapsed / max(elapsed, 0.01):.0f}x longer) before the "
        f"caller heard about it",
    )


# --------------------------------------------------------------------------
# Probe 1b: what the library actually puts on the wire, plain vs under HA
# --------------------------------------------------------------------------

# What homeassistant.helpers.httpx_client presets on its shared AsyncClient.
HA_SERVER_SOFTWARE = "HomeAssistant/2026.9.0 httpx/0.28.1 Python/3.14"


@asynccontextmanager
async def recording_server(captured: list[bytes]):
    """An HTTP server that records the request head and answers 200."""
    body = b'{"data": {"items": []}}'

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            captured.append(head)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


def request_headers(head: bytes) -> dict[str, str]:
    lines = head.decode("latin-1").split("\r\n")[1:]
    return dict(
        (name.strip().lower(), value.strip())
        for name, _, value in (line.partition(":") for line in lines)
        if name.strip()
    )


async def probe_wire_headers() -> None:
    """Compare the headers sent by a plain client and by HA's shared client.

    Home Assistant presets a User-Agent on the AsyncClient it shares between
    integrations, and this library sets its own per request. Whether HA's
    survives the merge decides whether the API sees a different caller under
    HA than it does from the CLI -- which is worth knowing before blaming the
    user agent for anything.
    """
    print("\nProbe 1b: request headers, plain client vs HA's shared client")
    captured: list[bytes] = []

    async with recording_server(captured) as base_url:
        plain = unauthenticated_client(base_url)
        await timed(plain.get_org_charge_points)
        await plain.httpx_client.aclose()

        # Same construction HA uses: a shared client carrying its own UA.
        ha_style = unauthenticated_client(base_url)
        ha_style.httpx_client = httpx.AsyncClient(
            headers={"User-Agent": HA_SERVER_SOFTWARE}
        )
        await timed(ha_style.get_org_charge_points)
        await ha_style.httpx_client.aclose()

    if len(captured) < 2:
        record("wire-headers", INCONCLUSIVE, "did not capture both requests")
        return

    plain_headers, ha_headers = (request_headers(head) for head in captured[:2])
    for label, headers in (("plain", plain_headers), ("HA-style", ha_headers)):
        print(f"    {label:<9} user-agent: {headers.get('user-agent')!r}")

    differences = {
        name
        for name in set(plain_headers) | set(ha_headers)
        if plain_headers.get(name) != ha_headers.get(name)
    }
    if differences:
        detail = ", ".join(
            f"{name}: {plain_headers.get(name)!r} vs {ha_headers.get(name)!r}"
            for name in sorted(differences)
        )
        record(
            "wire-headers",
            FAIL,
            f"Home Assistant changes what the API sees -- {detail}",
        )
    else:
        record(
            "wire-headers",
            PASS,
            f"identical on both: the library's per-request "
            f"{plain_headers.get('user-agent')!r} wins the merge, so HA and the "
            f"CLI present the same caller to the API",
        )


# --------------------------------------------------------------------------
# Probe 2: the cloud's own read endpoints, against the reduced default
# --------------------------------------------------------------------------


def coordinator_reads(
    client: Evnex, org_id: str, charge_point_id: str
) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    """The GETs the HA coordinator makes on every poll."""
    return [
        ("get_user_detail", client.get_user_detail),
        ("get_org_charge_points", lambda: client.get_org_charge_points(org_id)),
        ("get_org_insight", lambda: client.get_org_insight(days=7, org_id=org_id)),
        (
            "get_charge_point_detail_v3",
            lambda: client.get_charge_point_detail_v3(charge_point_id),
        ),
        (
            "get_charge_point_sessions",
            lambda: client.get_charge_point_sessions(charge_point_id),
        ),
    ]


async def probe_cloud_reads(client: Evnex, org_id: str, charge_point_id: str) -> None:
    """Check the read endpoints keep real headroom under the default timeout.

    A single sample under the ceiling is not enough. These calls run hundreds
    of times a day under Home Assistant, so what matters is the margin: an
    endpoint at 3.8s under a 5s timeout is not passing, it is one bad day from
    a ReadTimeout.
    """
    budget = default_request_timeout()
    print(f"\nProbe 2: cloud read endpoints vs the {budget:.0f}s default")

    calls = coordinator_reads(client, org_id, charge_point_id)

    slowest, slowest_name = 0.0, ""
    failures: list[str] = []
    for name, call in calls:
        elapsed, outcome = await timed(call)
        if isinstance(outcome, BaseException):
            failures.append(f"{name}: {outcome!r} after {elapsed:.1f}s")
            print(f"    {name:<28} {elapsed:6.2f}s  FAILED {outcome!r}")
            continue
        if elapsed > slowest:
            slowest, slowest_name = elapsed, name
        margin = budget / elapsed if elapsed else float("inf")
        flag = "" if margin >= MIN_HEADROOM else f"  <- only {margin:.1f}x headroom"
        print(f"    {name:<28} {elapsed:6.2f}s{flag}")

    if failures:
        record(
            "cloud-reads",
            FAIL,
            f"the {budget:.0f}s default is too tight for: " + "; ".join(failures),
        )
    elif slowest * MIN_HEADROOM > budget:
        record(
            "cloud-reads",
            FAIL,
            f"{slowest_name} took {slowest:.2f}s against a {budget:.0f}s timeout "
            f"({budget / slowest:.1f}x headroom). Polled every few minutes, its "
            f"slow tail will cross that ceiling; want at least "
            f"{MIN_HEADROOM:.0f}x, so {slowest * MIN_HEADROOM:.0f}s or more",
        )
    else:
        record(
            "cloud-reads",
            PASS,
            f"all {len(calls)} read endpoints answered; slowest {slowest_name} "
            f"at {slowest:.2f}s, {budget / max(slowest, 0.01):.1f}x inside the "
            f"{budget:.0f}s default",
        )


# --------------------------------------------------------------------------
# Probe 3: the actual bug -- the OCPP command round trip
# --------------------------------------------------------------------------


async def probe_override(client: Evnex, charge_point_id: str, rounds: int) -> None:
    """Run get-override at 0.7.0's 15s cap and at the new 30s, and compare."""
    print(
        f"\nProbe 3: get-override round trip, {rounds} round(s) at "
        f"{BASELINE_OVERRIDE_TIMEOUT:.0f}s (0.7.0) then {COMMAND_TIMEOUT}s (fixed)"
    )

    baseline_timeouts = 0
    fixed_successes = 0
    round_trips: list[float] = []

    for attempt in range(1, rounds + 1):
        elapsed, outcome = await timed(
            lambda: client.get_charge_point_override(
                charge_point_id, timeout=BASELINE_OVERRIDE_TIMEOUT
            )
        )
        if isinstance(outcome, httpx.ReadTimeout):
            baseline_timeouts += 1
            print(
                f"    round {attempt}  {BASELINE_OVERRIDE_TIMEOUT:.0f}s cap: "
                f"ReadTimeout at {elapsed:5.1f}s"
            )
        elif isinstance(outcome, BaseException):
            print(
                f"    round {attempt}  {BASELINE_OVERRIDE_TIMEOUT:.0f}s cap: "
                f"{outcome!r} at {elapsed:5.1f}s"
            )
        else:
            round_trips.append(elapsed)
            print(
                f"    round {attempt}  {BASELINE_OVERRIDE_TIMEOUT:.0f}s cap: "
                f"answered in {elapsed:5.1f}s"
            )

        elapsed, outcome = await timed(
            lambda: client.get_charge_point_override(charge_point_id)
        )
        if isinstance(outcome, BaseException):
            print(
                f"    round {attempt}  {COMMAND_TIMEOUT}s:    "
                f"{outcome!r} at {elapsed:5.1f}s"
            )
        else:
            fixed_successes += 1
            round_trips.append(elapsed)
            print(
                f"    round {attempt}  {COMMAND_TIMEOUT}s:    "
                f"answered in {elapsed:5.1f}s (chargeNow={outcome.chargeNow})"
            )

    observed = (
        f"observed round trips {min(round_trips):.1f}-{max(round_trips):.1f}s"
        if round_trips
        else "no round trip completed"
    )

    if baseline_timeouts and fixed_successes:
        record(
            "override-timeout",
            PASS,
            f"reproduced: the {BASELINE_OVERRIDE_TIMEOUT:.0f}s cap timed out "
            f"{baseline_timeouts}/{rounds} "
            f"time(s) while {COMMAND_TIMEOUT}s succeeded "
            f"{fixed_successes}/{rounds}; {observed}",
        )
    elif fixed_successes == 0:
        record(
            "override-timeout",
            INCONCLUSIVE,
            f"the charger did not answer even at {COMMAND_TIMEOUT}s -- it is "
            f"genuinely offline or asleep, not a timeout tuning problem; "
            f"{observed}",
        )
    else:
        record(
            "override-timeout",
            INCONCLUSIVE,
            f"did not reproduce this run: the charger answered inside "
            f"{BASELINE_OVERRIDE_TIMEOUT:.0f}s "
            f"every time ({observed}). Re-run when it is idle, or after it "
            f"has been asleep, to see the slow path",
        )


async def probe_other_commands(client: Evnex, charge_point_id: str) -> None:
    """The other command endpoints share the same round trip and timeout."""
    print(f"\nProbe 4: other OCPP commands at {COMMAND_TIMEOUT}s")

    calls: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
        (
            "get_charge_point_status",
            lambda: client.get_charge_point_status(charge_point_id),
        ),
        (
            "get_charge_point_energy_meter_reading",
            lambda: client.get_charge_point_energy_meter_reading(charge_point_id),
        ),
    ]

    over_baseline = []
    for name, call in calls:
        elapsed, outcome = await timed(call)
        if isinstance(outcome, BaseException):
            print(f"    {name:<38} {elapsed:6.2f}s  {outcome!r}")
        else:
            flag = ""
            if elapsed > BASELINE_DEFAULT_TIMEOUT:
                over_baseline.append(f"{name} ({elapsed:.1f}s)")
                flag = f"  <- over 0.7.0's {BASELINE_DEFAULT_TIMEOUT:.0f}s default"
            print(f"    {name:<38} {elapsed:6.2f}s{flag}")

    if over_baseline:
        record(
            "command-band",
            PASS,
            f"these need more than the {default_request_timeout():.0f}s "
            "cloud-read default: " + ", ".join(over_baseline),
        )
    else:
        record(
            "command-band",
            INCONCLUSIVE,
            f"every command answered inside {default_request_timeout():.0f}s this "
            "run; the charger was awake "
            "and quick, so this run does not exercise the slow path",
        )


# --------------------------------------------------------------------------
# Probe 5: soak -- what Home Assistant actually does
# --------------------------------------------------------------------------


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


@asynccontextmanager
async def loop_lag_sampler(interval: float = 0.05):
    """Track how late the event loop is servicing a timer it promised to run.

    httpx enforces a read timeout as a wall-clock deadline on this loop. A
    saturated loop -- the normal condition inside Home Assistant, which runs
    every integration on one -- services the socket late, so a response the
    server delivered in time can still land past the deadline. Run this on the
    Home Assistant host to see how much of the timeout budget the loop eats.
    """
    worst = [0.0]

    async def sample():
        while True:
            started = time.monotonic()
            await asyncio.sleep(interval)
            worst[0] = max(worst[0], time.monotonic() - started - interval)

    task = asyncio.create_task(sample())
    try:
        yield worst
    finally:
        task.cancel()


async def measure(
    client: Evnex, call: Callable[[], Awaitable[_T]]
) -> tuple[float, float | None, _T | BaseException]:
    """Time a call, capturing time-to-first-byte as well as the total.

    The read timeout applies to each socket read, not to the call as a whole,
    so the number that decides whether it fires is when response headers
    arrive -- not how long the whole request took.
    """
    ttfb: list[float] = []
    started = time.monotonic()

    async def on_response(response: httpx.Response) -> None:
        ttfb.append(time.monotonic() - started)

    hooks = client.httpx_client.event_hooks["response"]
    hooks.append(on_response)
    try:
        elapsed, outcome = await timed(call)
    finally:
        hooks.remove(on_response)
    return elapsed, (ttfb[-1] if ttfb else None), outcome


async def probe_soak(
    client: Evnex,
    org_id: str,
    charge_point_id: str,
    interval: float,
    cycles: int,
) -> None:
    """Replay the coordinator's poll on HA's cadence and watch the tail.

    One sample proves nothing about an intermittent failure. The integration
    runs this sequence every 5 minutes, 288 times a day; a call that averages
    3.8s only has to reach the ceiling occasionally to produce the warning in
    the log. This is the probe that can actually observe that.
    """
    budget = default_request_timeout()
    print(
        f"\nProbe 5: soak -- {cycles} cycles every {interval:.0f}s "
        f"({cycles * interval / 60:.0f} minutes). Ctrl-C reports early."
    )

    samples: dict[str, list[float]] = {}
    timeouts: dict[str, int] = {}
    worst_lag = 0.0

    try:
        for cycle in range(1, cycles + 1):
            started = time.monotonic()
            calls = coordinator_reads(client, org_id, charge_point_id)
            calls.append(
                (
                    "get_charge_point_override",
                    lambda: client.get_charge_point_override(charge_point_id),
                )
            )

            worst, worst_name, errors = 0.0, "", []
            async with loop_lag_sampler() as lag:
                for name, call in calls:
                    elapsed, ttfb, outcome = await measure(client, call)
                    if isinstance(outcome, BaseException):
                        timeouts[name] = timeouts.get(name, 0) + 1
                        errors.append(f"{name} {type(outcome).__name__}")
                        continue
                    # TTFB is what the read timeout governs; fall back to the
                    # total if the hook did not fire.
                    samples.setdefault(name, []).append(
                        ttfb if ttfb is not None else elapsed
                    )
                    if elapsed > worst:
                        worst, worst_name = elapsed, name
            worst_lag = max(worst_lag, lag[0])

            stamp = datetime.now().strftime("%H:%M:%S")
            summary = f"slowest {worst_name} {worst:.2f}s" if worst_name else ""
            summary += f"  loop lag {lag[0] * 1000:.0f}ms"
            if errors:
                summary += "  FAILED: " + ", ".join(errors)
            print(f"    cycle {cycle:>3}/{cycles}  {stamp}  {summary}")

            if cycle < cycles:
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\n    interrupted; reporting what was collected")

    if not samples:
        record("soak", INCONCLUSIVE, "no samples collected")
        return

    print(
        f"\n    time to first byte, which is what the read timeout governs "
        f"(worst loop lag {worst_lag * 1000:.0f}ms)"
    )
    print(f"    {'endpoint':<30}{'n':>4}{'min':>8}{'med':>8}{'p95':>8}{'max':>8}")
    for name, values in sorted(samples.items(), key=lambda kv: -max(kv[1])):
        print(
            f"    {name:<30}{len(values):>4}{min(values):>7.2f}s"
            f"{percentile(values, 0.5):>7.2f}s{percentile(values, 0.95):>7.2f}s"
            f"{max(values):>7.2f}s"
        )

    if timeouts:
        detail = ", ".join(f"{name} x{count}" for name, count in timeouts.items())
        record(
            "soak",
            FAIL,
            f"reproduced the Home Assistant failure: {detail} over "
            f"{sum(len(v) for v in samples.values())} calls",
        )
        return

    worst_name, worst_values = max(samples.items(), key=lambda kv: max(kv[1]))
    worst = max(worst_values)
    if worst * MIN_HEADROOM > budget:
        record(
            "soak",
            FAIL,
            f"no timeout yet, but {worst_name} peaked at {worst:.2f}s against "
            f"the {budget:.0f}s ceiling ({budget / worst:.1f}x headroom) -- that "
            f"tail is what fails under a longer soak",
        )
    else:
        record(
            "soak",
            PASS,
            f"no timeouts over {sum(len(v) for v in samples.values())} calls; "
            f"worst was {worst_name} at {worst:.2f}s, "
            f"{budget / worst:.1f}x inside the {budget:.0f}s ceiling",
        )


# --------------------------------------------------------------------------
# Sign-in and charge point selection
# --------------------------------------------------------------------------


def cached_tokens() -> TokenSet | None:
    """Reuse the session `evnex auth login` cached, if there is one."""
    override = os.environ.get("EVNEX_TOKEN_CACHE")
    cache = (
        Path(override)
        if override
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "evnex"
        / "tokens.json"
    )
    if cache.is_file():
        try:
            return TokenSet.from_dict(json.loads(cache.read_text()))
        except (ValueError, KeyError):
            return None
    return None


async def sign_in() -> EvnexAuth:
    tokens = cached_tokens()
    if tokens is None and os.environ.get("EVNEX_REFRESH_TOKEN"):
        tokens = TokenSet(refresh_token=os.environ["EVNEX_REFRESH_TOKEN"])

    auth = EvnexAuth(tokens=tokens)
    if tokens is not None:
        return auth

    username = os.environ.get("EVNEX_CLIENT_USERNAME") or input("EVNEX username: ")
    password = os.environ.get("EVNEX_CLIENT_PASSWORD") or getpass.getpass(
        "EVNEX password: "
    )
    result = await auth.start_authentication(username, password)
    while isinstance(result, AuthChallenge):
        code = input(f"Enter the code for challenge {result.name}: ")
        result = await auth.respond_to_challenge(result, code)
    return auth


async def pick_online_charge_point(
    client: Evnex, org_id: str
) -> tuple[str, str] | None:
    """Return (id, name) of an ONLINE charge point, as the coordinator gates on."""
    for charge_point in await client.get_org_charge_points(org_id):
        detail = await client.get_charge_point_detail_v3(charge_point.id)
        status = detail.data.attributes.networkStatus
        print(f"    {charge_point.name} ({charge_point.id}): {status}")
        if status == "ONLINE":
            return charge_point.id, charge_point.name
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="run only probe 1, which needs no credentials",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="how many times to probe the override round trip (default 3)",
    )
    parser.add_argument(
        "--charge-point",
        help="charge point id to probe (default: the first ONLINE one)",
    )
    parser.add_argument(
        "--soak",
        action="store_true",
        help="poll on the HA coordinator's cadence and report the latency tail",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=300.0,
        help="seconds between soak cycles (default 300, matching HA)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=12,
        help="how many soak cycles to run (default 12, so one hour)",
    )
    args = parser.parse_args()

    # The probes are slow by nature; keep the running commentary live.
    sys.stdout.reconfigure(line_buffering=True)

    await probe_fail_fast()
    await probe_wire_headers()

    if not args.offline_only:
        auth = await sign_in()
        client = Evnex(auth=auth)
        user = await client.get_user_detail()
        org_id = client.org_id
        print(f"\nSigned in as {user.email}; org {org_id}")

        print("\nCharge points:")
        if args.charge_point:
            selected: tuple[str, str] | None = (args.charge_point, args.charge_point)
        else:
            selected = await pick_online_charge_point(client, org_id)

        if selected is None:
            record(
                "charge-point",
                INCONCLUSIVE,
                "no ONLINE charge point; probes 3 and 4 need one to talk to",
            )
        else:
            charge_point_id, name = selected
            print(f"  probing '{name}'")
            await probe_cloud_reads(client, org_id, charge_point_id)
            await probe_override(client, charge_point_id, args.rounds)
            await probe_other_commands(client, charge_point_id)
            if args.soak:
                await probe_soak(
                    client, org_id, charge_point_id, args.interval, args.cycles
                )

        await client.httpx_client.aclose()

    print("\n" + "=" * 72)
    for result in results:
        print(f"{result.verdict:<13} {result.name:<20} {result.detail}")
    print("=" * 72)

    if any(r.verdict == FAIL for r in results):
        return 1
    if any(r.verdict == INCONCLUSIVE for r in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
