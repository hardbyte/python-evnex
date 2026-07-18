"""Resource commands: charge point status, sessions, insights, and control.

These commands talk to the EVNEX Cloud API through an authenticated Evnex
client. Human output is aligned plain text on stdout; with ``--json`` the same
data is emitted as a single JSON document on stdout (built from the pydantic
models) while diagnostics stay on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import NoReturn

import httpx

from evnex.api import Evnex
from evnex.cli._auth import signed_in_auth
from evnex.schema.charge_points import EvnexChargePoint
from evnex.schema.v3.charge_points import EvnexChargePointSession
from evnex.schema.v3.locations import EvnexLocation


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _abort(message: str, code: int) -> NoReturn:
    """Print a diagnostic to stderr and exit with the given status."""
    print(message, file=sys.stderr)
    sys.exit(code)


@asynccontextmanager
async def open_client(args: argparse.Namespace) -> AsyncIterator[Evnex]:
    """Sign in and yield an Evnex client, closing its HTTP client on exit."""
    auth = await signed_in_auth(args)
    # Building the httpx client loads the CA bundle from disk; do that off the
    # event loop so the blocking file I/O does not stall it.
    client = await asyncio.to_thread(Evnex, auth=auth)
    try:
        yield client
    finally:
        await client.httpx_client.aclose()


async def _list_charge_points(client: Evnex) -> list[EvnexChargePoint]:
    """Fetch the account's charge points (and set the client's org id)."""
    await client.get_user_detail()
    # The retry decorator erases the annotated return type to Any; pin it back.
    charge_points: list[EvnexChargePoint] = await client.get_org_charge_points()
    return charge_points


def _match_charge_point(
    charge_points: list[EvnexChargePoint], selector: str
) -> EvnexChargePoint:
    """Resolve a selector to a single charge point.

    An exact id wins; otherwise the selector is matched case-insensitively as a
    substring of the name or serial. Zero or multiple matches abort with exit 2.
    """
    for charge_point in charge_points:
        if charge_point.id == selector:
            return charge_point

    needle = selector.casefold()
    matches = [
        charge_point
        for charge_point in charge_points
        if needle in charge_point.name.casefold()
        or needle in charge_point.serial.casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        _abort(f"No charge point matches {selector!r}", 2)
    lines = [f"{selector!r} matches several charge points; be more specific:"]
    lines += [f"  {cp.id}  {cp.name}" for cp in matches]
    _abort("\n".join(lines), 2)


def _resolve_one(
    charge_points: list[EvnexChargePoint], selector: str | None
) -> EvnexChargePoint:
    """Resolve the target charge point, defaulting to the sole one if unique."""
    if selector is not None:
        return _match_charge_point(charge_points, selector)
    if len(charge_points) == 1:
        return charge_points[0]
    lines = ["Select a charge point with --charge-point:"]
    lines += [f"  {cp.id}  {cp.name}" for cp in charge_points]
    _abort("\n".join(lines), 2)


def _kw(watts: float | None) -> str:
    return "-" if watts is None else f"{watts / 1000:.2f} kW"


def _kwh(watt_hours: float | None) -> str:
    return "-" if watt_hours is None else f"{watt_hours / 1000:.2f} kWh"


def _fmt_dt(value: datetime | None) -> str:
    """Local-readable ISO-8601 with second resolution."""
    if value is None:
        return "-"
    return value.astimezone().replace(microsecond=0).isoformat()


def _fmt_period(seconds: float) -> str:
    """Seconds from midnight as HH:MM."""
    total_minutes = int(seconds) // 60
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print rows as columns padded to a common width."""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    for row in [headers, *rows]:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def _latest_session(
    sessions: list[EvnexChargePointSession],
) -> EvnexChargePointSession | None:
    ordered = _newest_first(sessions)
    return ordered[0] if ordered else None


def _newest_first(
    sessions: list[EvnexChargePointSession],
) -> list[EvnexChargePointSession]:
    # The API does not document an ordering; sort rather than assume one
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(sessions, key=lambda s: s.attributes.startDate or epoch, reverse=True)


async def cmd_live_status(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        if args.charge_point is not None:
            targets = [_match_charge_point(charge_points, args.charge_point)]
        else:
            targets = charge_points

        payload = []
        blocks: list[list[str]] = []
        for charge_point in targets:
            detail = await client.get_charge_point_detail_v3(charge_point.id)
            sessions = await client.get_charge_point_sessions(charge_point.id)
            attributes = detail.data.attributes
            latest = _latest_session(sessions)

            if args.json:
                payload.append(
                    {
                        "chargePoint": attributes.model_dump(mode="json"),
                        "sessions": [s.model_dump(mode="json") for s in sessions],
                    }
                )
                continue

            lines = [f"{attributes.name} ({attributes.serial})"]
            lines.append(f"  Network: {attributes.networkStatus}")
            for connector in attributes.connectors:
                lines.append(
                    f"  Connector {connector.connectorId}: {connector.ocppStatus}"
                )
                if connector.meter is not None:
                    lines.append(f"    Charging power: {_kw(connector.meter.power)}")
                    if connector.meter.supplyActivePower is not None:
                        lines.append(
                            f"    Grid power: {_kw(connector.meter.supplyActivePower)}"
                        )
            if latest is not None and latest.attributes.endDate is None:
                session = latest.attributes
                summary = f"  Active session: {_kwh(session.totalPowerUsage)}"
                if session.totalCost is not None:
                    summary += (
                        f", {session.totalCost.amount:.2f} {session.totalCost.currency}"
                    )
                lines.append(summary)
            blocks.append(lines)

        if args.json:
            print(json.dumps(payload, indent=2))
            return
        if not blocks:
            print("No charge points found", file=sys.stderr)
            return
        print("\n\n".join("\n".join(block) for block in blocks))


async def cmd_charge_points_list(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        if args.json:
            print(
                json.dumps(
                    [cp.model_dump(mode="json") for cp in charge_points], indent=2
                )
            )
            return
        rows = [[cp.id, cp.name, cp.serial, cp.networkStatus] for cp in charge_points]
        _print_table(["ID", "Name", "Serial", "Network"], rows)


async def cmd_charge_points_show(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        detail = await client.get_charge_point_detail_v3(charge_point.id)
        attributes = detail.data.attributes

        if args.json:
            print(json.dumps(attributes.model_dump(mode="json"), indent=2))
            return

        print(f"{attributes.name} ({attributes.serial})")
        print(f"  Model: {attributes.model}")
        print(f"  Firmware: {attributes.firmware}")
        print(f"  Serial: {attributes.serial}")
        print(f"  Network status: {attributes.networkStatus}")
        for connector in attributes.connectors:
            print(
                f"  Connector {connector.connectorId} "
                f"({connector.connectorType}): {connector.ocppStatus}"
            )
            if connector.meter is not None:
                print(f"    Charging power: {_kw(connector.meter.power)}")
                if connector.meter.supplyActivePower is not None:
                    print(f"    Grid power: {_kw(connector.meter.supplyActivePower)}")
        schedule = attributes.profiles.chargeSchedule
        enabled = "enabled" if schedule is not None and schedule.enabled else "disabled"
        print(f"  Charge schedule: {enabled}")


async def cmd_sessions_list(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        sessions = await client.get_charge_point_sessions(charge_point.id)
        sessions = _newest_first(sessions)[: args.limit]

        if args.json:
            print(json.dumps([s.model_dump(mode="json") for s in sessions], indent=2))
            return

        rows = []
        for session in sessions:
            attributes = session.attributes
            end = (
                "active" if attributes.endDate is None else _fmt_dt(attributes.endDate)
            )
            cost = "-"
            if attributes.totalCost is not None:
                cost = (
                    f"{attributes.totalCost.amount:.2f} {attributes.totalCost.currency}"
                )
            rows.append(
                [
                    _fmt_dt(attributes.startDate),
                    end,
                    _kwh(attributes.totalPowerUsage),
                    cost,
                ]
            )
        _print_table(["Start", "End", "Energy", "Cost"], rows)


async def cmd_locations_list(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        await client.get_user_detail()
        # The retry decorator erases the annotated return type to Any; pin it back.
        locations: list[EvnexLocation] = await client.get_org_locations()

        if args.json:
            print(
                json.dumps([loc.model_dump(mode="json") for loc in locations], indent=2)
            )
            return

        rows = []
        for location in locations:
            attributes = location.attributes
            city = attributes.address.city if attributes.address else None
            retailer = (
                attributes.icpDetails.electricityRetailer
                if attributes.icpDetails
                else None
            )
            rows.append(
                [
                    attributes.name,
                    city or "-",
                    attributes.icpNumber or "-",
                    retailer or "-",
                    attributes.timeZone or "-",
                ]
            )
        _print_table(["Name", "City", "ICP", "Retailer", "Timezone"], rows)


async def cmd_insights(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        await client.get_user_detail()
        insights = await client.get_org_insight(days=args.days)

        if args.json:
            print(json.dumps([i.model_dump(mode="json") for i in insights], indent=2))
            return

        rows = []
        for entry in insights:
            cost = "-"
            if entry.cost.cost is not None:
                cost = f"{entry.cost.cost:.2f} {entry.cost.currency or ''}".strip()
            rows.append(
                [
                    entry.startDate.strftime("%Y-%m-%d"),
                    _kwh(entry.powerUsage),
                    cost,
                    str(entry.sessions),
                ]
            )
        _print_table(["Date", "Energy", "Cost", "Sessions"], rows)


async def cmd_charge_now(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        await client.set_charge_point_override(charge_point.id, charge_now=True)
        print(f"Charging now on {charge_point.name} ({charge_point.serial})")


async def cmd_charge_auto(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        await client.set_charge_point_override(charge_point.id, charge_now=False)
        print(
            f"Returned {charge_point.name} ({charge_point.serial}) "
            "to its charging schedule"
        )


async def cmd_charge_stop(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        if not args.yes:
            # See the module note: blocking on input() is fine for this CLI.
            answer = input(
                f"Stop the active charging session on {charge_point.name}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                _abort("Aborted.", 1)
        try:
            await client.stop_charge_point(charge_point.id)
        except httpx.ReadTimeout:
            # The API answers a stop with no active session as a 504 that
            # surfaces as a read timeout.
            _abort(f"No active charging session on {charge_point.name} to stop.", 1)
        print(f"Stopped charging on {charge_point.name} ({charge_point.serial})")


async def cmd_schedule_show(args: argparse.Namespace) -> None:
    async with open_client(args) as client:
        charge_points = await _list_charge_points(client)
        charge_point = _resolve_one(charge_points, args.charge_point)
        detail = await client.get_charge_point_detail_v3(charge_point.id)
        schedule = detail.data.attributes.profiles.chargeSchedule

        if args.json:
            dumped = None if schedule is None else schedule.model_dump(mode="json")
            print(json.dumps(dumped, indent=2))
            return

        if schedule is None:
            print(f"No charge schedule configured for {charge_point.name}")
            return
        print(
            f"Charge schedule for {charge_point.name}: "
            f"{'enabled' if schedule.enabled else 'disabled'}"
        )
        for period in schedule.chargingSchedulePeriods:
            print(f"  {_fmt_period(period.startPeriod)}  {period.limit:g} A")


def add_resource_commands(
    sub: argparse._SubParsersAction,
    cache_flags: argparse.ArgumentParser,
    otp_flags: argparse.ArgumentParser,
) -> None:
    """Attach the resource command groups to the top-level subparsers."""
    sign_in = [cache_flags, otp_flags]

    json_flag = argparse.ArgumentParser(add_help=False)
    json_flag.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON on stdout",
    )
    cp_flag = argparse.ArgumentParser(add_help=False)
    cp_flag.add_argument(
        "--charge-point",
        metavar="ID",
        help="charge point id, or a part of its name or serial of its name or serial",
    )

    status = sub.add_parser(
        "status",
        parents=[cp_flag, json_flag, *sign_in],
        help="show a live view of your charge points",
        description=(
            "Show, for each charge point (or the one selected with "
            "--charge-point), its network status, each connector's status and "
            "power, and any active charging session's energy and cost."
        ),
    )
    status.set_defaults(func=cmd_live_status)

    charge_points = sub.add_parser(
        "charge-points",
        help="list and inspect charge points",
        description="List charge points or show the detail of one.",
    )
    charge_points.set_defaults(print_group_help=charge_points.print_help)
    charge_points_sub = charge_points.add_subparsers(dest="charge_points_command")

    cp_list = charge_points_sub.add_parser(
        "list",
        parents=[json_flag, *sign_in],
        help="list charge points (id, name, serial, network status)",
    )
    cp_list.set_defaults(func=cmd_charge_points_list)

    cp_show = charge_points_sub.add_parser(
        "show",
        parents=[json_flag, *sign_in],
        help="show the detail of one charge point",
    )
    cp_show.add_argument(
        "charge_point",
        nargs="?",
        metavar="ID",
        help="charge point id, or a part of its name or serial of its name or serial",
    )
    cp_show.set_defaults(func=cmd_charge_points_show)

    sessions = sub.add_parser(
        "sessions",
        help="list charging sessions",
        description="List recent charging sessions for a charge point.",
    )
    sessions.set_defaults(print_group_help=sessions.print_help)
    sessions_sub = sessions.add_subparsers(dest="sessions_command")

    sessions_list = sessions_sub.add_parser(
        "list",
        parents=[cp_flag, json_flag, *sign_in],
        help="list recent charging sessions for a charge point",
    )
    sessions_list.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="maximum number of sessions to show (default 10)",
    )
    sessions_list.set_defaults(func=cmd_sessions_list)

    locations = sub.add_parser(
        "locations",
        help="list locations",
        description="List the organisation's locations.",
    )
    locations.set_defaults(print_group_help=locations.print_help)
    locations_sub = locations.add_subparsers(dest="locations_command")

    locations_list = locations_sub.add_parser(
        "list",
        parents=[json_flag, *sign_in],
        help="list locations (name, city, ICP number, retailer, timezone)",
    )
    locations_list.set_defaults(func=cmd_locations_list)

    insights = sub.add_parser(
        "insights",
        parents=[json_flag, *sign_in],
        help="show daily energy, cost, and session counts for the organisation",
    )
    insights.add_argument(
        "--days",
        type=int,
        choices=(7, 14, 30),
        default=7,
        help="reporting window in days (default 7)",
    )
    insights.set_defaults(func=cmd_insights)

    charge = sub.add_parser(
        "charge",
        help="control charging on a charge point",
        description="Start charging now, return to the schedule, or stop charging.",
    )
    charge.set_defaults(print_group_help=charge.print_help)
    charge_sub = charge.add_subparsers(dest="charge_command")

    charge_now = charge_sub.add_parser(
        "now",
        parents=[cp_flag, *sign_in],
        help="start charging immediately, overriding the schedule",
    )
    charge_now.set_defaults(func=cmd_charge_now)

    charge_auto = charge_sub.add_parser(
        "auto",
        parents=[cp_flag, *sign_in],
        help="return control to the configured charging schedule",
    )
    charge_auto.set_defaults(func=cmd_charge_auto)

    charge_stop = charge_sub.add_parser(
        "stop",
        parents=[cp_flag, *sign_in],
        help="stop the active charging session",
    )
    charge_stop.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation prompt"
    )
    charge_stop.set_defaults(func=cmd_charge_stop)

    schedule = sub.add_parser(
        "schedule",
        help="view the charging schedule",
        description="Show the charging schedule configured on a charge point.",
    )
    schedule.set_defaults(print_group_help=schedule.print_help)
    schedule_sub = schedule.add_subparsers(dest="schedule_command")

    schedule_show = schedule_sub.add_parser(
        "show",
        parents=[cp_flag, json_flag, *sign_in],
        help="show the charging schedule (enabled state and periods)",
    )
    schedule_show.set_defaults(func=cmd_schedule_show)
