#!/usr/bin/env python3
"""MCP wrapper for the vendored FlyClaw flight search CLI.

The original flight-ticket-server backend depends on driving Ctrip through
Chrome.  This wrapper keeps the same main route-search tool name while using
FlyClaw's JSON CLI output as the data source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parents[1]
PYTHON = PROJECT_DIR / ".venv-flight" / "bin" / "python"
FLYCLAW_DIR = BASE_DIR / "vendor" / "flyclaw"
FLYCLAW = FLYCLAW_DIR / "flyclaw.py"

if str(FLYCLAW_DIR) not in sys.path:
    sys.path.insert(0, str(FLYCLAW_DIR))
from airport_manager import airport_manager  # noqa: E402

mcp = FastMCP(
    "flyclaw-flight-server",
    instructions=(
        "Flight search backed by vendored FlyClaw. Returns JSON-friendly "
        "records for route searches, flight-number queries, and simple "
        "connection lookups."
    ),
)


def _run_flyclaw(args: list[str], timeout: int = 45) -> tuple[Any, str]:
    cmd = [str(PYTHON), str(FLYCLAW), *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(FLYCLAW_DIR),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode != 0:
            raise RuntimeError(stderr or stdout or f"FlyClaw exited with {proc.returncode}")
    except subprocess.TimeoutExpired as exc:
        # FlyClaw can print complete JSON and then wait for slow worker threads
        # to exit. Treat parseable partial stdout as a successful degraded result.
        stdout_raw = exc.stdout or ""
        stderr_raw = exc.stderr or ""
        if isinstance(stdout_raw, bytes):
            stdout_raw = stdout_raw.decode("utf-8", errors="replace")
        if isinstance(stderr_raw, bytes):
            stderr_raw = stderr_raw.decode("utf-8", errors="replace")
        stdout = stdout_raw.strip()
        stderr = (stderr_raw.strip() + "\n[FlyClaw wrapper] subprocess timed out after JSON output").strip()
        if not stdout:
            raise RuntimeError(f"FlyClaw timed out after {timeout} seconds")
    if not stdout:
        return [], stderr
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FlyClaw returned non-JSON output: {stdout[:500]}") from exc
    if isinstance(data, (list, dict)):
        return data, stderr
    return [], stderr


def _is_china_airport(iata: str) -> bool:
    info = airport_manager.get_info(iata or "")
    if not info:
        return False
    return info.get("country_cn") == "中国" or info.get("country_en") in {"China", "CN"}


def _is_china_record(record: dict[str, Any]) -> bool:
    return _is_china_airport(record.get("origin_iata", "")) and _is_china_airport(
        record.get("destination_iata", "")
    )


def _time_part(value: Any, record: dict[str, Any] | None = None) -> str:
    if not value:
        return ""
    text = str(value)
    if record and _is_china_record(record) and ("+00:00" in text or text.endswith("Z")):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            local_dt = dt.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
            return local_dt.strftime("%H:%M")
        except ValueError:
            pass
    if "T" in text and len(text) >= 16:
        return text[11:16]
    if " " in text and len(text) >= 16:
        return text[11:16]
    return text


def _airport_name(record: dict[str, Any], side: str) -> str:
    iata = record.get(f"{side}_iata") or record.get(side) or ""
    city = record.get(f"{side}_city") or record.get(f"{side}_name") or ""
    if city and iata and iata not in city:
        return f"{city}({iata})"
    return city or iata


def _format_price(record: dict[str, Any]) -> str:
    price = record.get("price")
    if price is None or price == "":
        return "待确认"
    currency = str(record.get("currency") or "CNY").upper()
    if currency == "CNY":
        return f"¥{price}"
    return f"{price} {currency}"


def _normalize_route_record(record: dict[str, Any], index: int) -> dict[str, Any]:
    segments = record.get("segments") or []
    stops = record.get("stops")
    if stops is None and segments:
        stops = max(len(segments) - 1, 0)
    duration = record.get("duration_minutes")
    if duration is None:
        duration = record.get("duration")

    normalized = {
        "序号": index,
        "航空公司": record.get("airline") or record.get("airline_name") or "未知",
        "航班号": record.get("flight_number") or "未知",
        "出发时间": _time_part(
            record.get("scheduled_departure")
            or record.get("departure_time")
            or record.get("depart_time"),
            record,
        ),
        "出发机场": _airport_name(record, "origin"),
        "到达时间": _time_part(
            record.get("scheduled_arrival")
            or record.get("arrival_time")
            or record.get("arrive_time"),
            record,
        ),
        "到达机场": _airport_name(record, "destination"),
        "价格": _format_price(record),
        "余票/库存": record.get("availability") or record.get("inventory") or "待确认",
        "经停次数": stops if stops is not None else "待确认",
        "最长中转分钟": record.get("max_layover_minutes"),
        "飞行时长分钟": duration,
        "数据来源": record.get("source") or "flyclaw",
        "舱位": record.get("cabin") or "economy",
        "约束状态": record.get("_constraint_status", "main"),
        "约束说明": record.get("_constraint_reason", ""),
        "segments": segments,
        "raw": record,
    }
    return normalized


def _records_from_flyclaw_payload(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    if isinstance(payload, dict):
        return (
            payload.get("flights") or [],
            payload.get("fallback_flights") or [],
            payload.get("query_constraints") or {},
            payload.get("source_warnings") or [],
        )
    if isinstance(payload, list):
        return payload, [], {}, []
    return [], [], {}, []


def _format_route_result(
    flights: list[dict[str, Any]],
    departure_city: str,
    destination_city: str,
    departure_date: str,
) -> str:
    if not flights:
        return f"{departure_date} {departure_city} -> {destination_city} 未查到可用航班。"
    lines = [f"{departure_date} {departure_city} -> {destination_city} 航班结果："]
    for item in flights:
        lines.append(
            "- {flight} {dep_time} {dep_airport} -> {arr_time} {arr_airport} "
            "{price} 经停:{stops} 来源:{source}".format(
                flight=item.get("航班号", "未知"),
                dep_time=item.get("出发时间", ""),
                dep_airport=item.get("出发机场", ""),
                arr_time=item.get("到达时间", ""),
                arr_airport=item.get("到达机场", ""),
                price=item.get("价格", "待确认"),
                stops=item.get("经停次数", "待确认"),
                source=item.get("数据来源", "flyclaw"),
            )
        )
    return "\n".join(lines)


@mcp.tool
def getCurrentDate() -> dict[str, Any]:
    """Return today's local date in YYYY-MM-DD format."""
    today = date.today().isoformat()
    return {"status": "success", "current_date": today}


@mcp.tool
def searchFlightRoutes(
    departure_city: str,
    destination_city: str,
    departure_date: str,
    stops: str = "0",
    cabin: str = "economy",
    limit: int = 10,
    sort: str = "departure",
    layover_max_hours: float | None = 2.0,
) -> dict[str, Any]:
    """Search flights by route using FlyClaw.

    Args:
        departure_city: Origin city, airport name, or IATA code.
        destination_city: Destination city, airport name, or IATA code.
        departure_date: Travel date in YYYY-MM-DD format.
        stops: 0, 1, 2, or any. Defaults to nonstop.
        cabin: economy, premium, business, or first. Defaults to economy.
        limit: Max returned results.
        sort: cheapest, fastest, departure, or arrival.
        layover_max_hours: Optional maximum layover hours for connecting flights.
    """
    args = [
        "search",
        "--from",
        departure_city,
        "--to",
        destination_city,
        "--date",
        departure_date,
        "--stops",
        str(stops),
        "--cabin",
        cabin,
        "--limit",
        str(limit),
        "--sort",
        sort,
        "--currency",
        "cny",
        "--timeout",
        "12",
        "--return-time",
        "6",
        "-o",
        "json",
        "--include-fallback",
    ]
    if layover_max_hours is not None and str(stops) != "0":
        args.extend(["--layover-max-hours", str(layover_max_hours)])

    try:
        payload, stderr = _run_flyclaw(args, timeout=25)
        records, fallback_records, query_constraints, source_warnings = _records_from_flyclaw_payload(payload)
        flights = [_normalize_route_record(record, i + 1) for i, record in enumerate(records)]
        fallback_flights = [
            _normalize_route_record(record, i + 1)
            for i, record in enumerate(fallback_records)
        ]
        return {
            "status": "success",
            "backend": "flyclaw",
            "departure_city": departure_city,
            "destination_city": destination_city,
            "departure_date": departure_date,
            "flight_count": len(flights),
            "flights": flights,
            "fallback_count": len(fallback_flights),
            "fallback_flights": fallback_flights,
            "query_constraints": query_constraints,
            "source_warnings": source_warnings,
            "formatted_output": _format_route_result(
                flights, departure_city, destination_city, departure_date
            ),
            "diagnostics": stderr,
        }
    except Exception as exc:
        return {
            "status": "error",
            "backend": "flyclaw",
            "message": str(exc),
            "error_code": "FLYCLAW_SEARCH_FAILED",
        }


@mcp.tool
def getFlightInfo(flight_number: str, flight_date: str | None = None) -> dict[str, Any]:
    """Query flight status/details by flight number using FlyClaw."""
    args = [
        "query",
        "--flight",
        flight_number,
        "--currency",
        "cny",
        "--timeout",
        "12",
        "--return-time",
        "6",
        "-o",
        "json",
    ]
    if flight_date:
        args.extend(["--date", flight_date])
    try:
        payload, stderr = _run_flyclaw(args, timeout=25)
        records, _, _, _ = _records_from_flyclaw_payload(payload)
        flights = [_normalize_route_record(record, i + 1) for i, record in enumerate(records)]
        return {
            "status": "success",
            "backend": "flyclaw",
            "flight_number": flight_number,
            "flight_count": len(flights),
            "flights": flights,
            "diagnostics": stderr,
        }
    except Exception as exc:
        return {
            "status": "error",
            "backend": "flyclaw",
            "message": str(exc),
            "error_code": "FLYCLAW_QUERY_FAILED",
        }


@mcp.tool
def getFlightStatus(flight_number: str, flight_date: str | None = None) -> dict[str, Any]:
    """Alias for getFlightInfo, kept for compatibility with the old server."""
    return getFlightInfo(flight_number, flight_date)


@mcp.tool
def getTransferFlightsByThreePlace(
    from_place: str,
    transfer_place: str,
    to_place: str,
    departure_date: str,
    min_transfer_time: float = 1.0,
    max_transfer_time: float = 2.0,
) -> dict[str, Any]:
    """Search two route legs through a specified transfer place.

    FlyClaw can also search general connecting itineraries via stops=any; this
    compatibility tool is intentionally simple and returns candidate first and
    second leg pools for the planner to combine.
    """
    first = searchFlightRoutes(
        from_place,
        transfer_place,
        departure_date,
        stops="0",
        cabin="economy",
        limit=5,
        sort="departure",
        layover_max_hours=None,
    )
    second = searchFlightRoutes(
        transfer_place,
        to_place,
        departure_date,
        stops="0",
        cabin="economy",
        limit=5,
        sort="departure",
        layover_max_hours=None,
    )
    return {
        "status": "success"
        if first.get("status") == "success" and second.get("status") == "success"
        else "partial",
        "backend": "flyclaw",
        "from_place": from_place,
        "transfer_place": transfer_place,
        "to_place": to_place,
        "departure_date": departure_date,
        "min_transfer_time": min_transfer_time,
        "max_transfer_time": max_transfer_time,
        "first_leg": first,
        "second_leg": second,
    }


@mcp.tool
def getWeatherByCity(
    city_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Compatibility placeholder; use amap-maps or variflight weather tools."""
    return {
        "status": "unsupported",
        "backend": "flyclaw",
        "message": "FlyClaw backend does not provide city weather. Use amap-maps or airport weather tools.",
        "city_name": city_name,
        "start_date": start_date,
        "end_date": end_date,
    }


@mcp.tool
def getWeatherByLocation(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Compatibility placeholder; use amap-maps for location weather/routes."""
    return {
        "status": "unsupported",
        "backend": "flyclaw",
        "message": "FlyClaw backend does not provide coordinate weather. Use amap-maps.",
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
    }


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio", show_banner=False)
    except BrokenPipeError:
        sys.exit(0)
