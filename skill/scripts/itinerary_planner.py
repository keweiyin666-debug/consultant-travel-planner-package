#!/usr/bin/env python3
"""
出差行程规划辅助脚本

用途：
    1. 根据已查询到的航班/高铁/市内交通数据计算时间线和预算
    2. 在实时数据缺失时生成带“待确认”标记的 Markdown 草案

用法：
    python3 skill/scripts/itinerary_planner.py
    python3 skill/scripts/itinerary_planner.py --input trip.json --output itinerary.md
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DEFAULT_PREFERRED_AIRLINES = ["MU", "FM", "东方航空", "上海航空"]


def calculate_buffer_time(scene: str) -> int:
    """返回指定场景的缓冲时间（分钟）。"""
    buffers = {
        "airport_departure": 120,
        "airport_arrival": 60,
        "station_departure": 60,
        "station_arrival": 30,
        "client_transfer": 15,
    }
    return buffers.get(scene, 60)


def parse_datetime(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")


def format_date(date_text: str) -> str:
    if not date_text:
        return "-"
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    return f"{dt.year}年{dt.month}月{dt.day}日（{WEEKDAYS[dt.weekday()]}）"


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def money(value) -> str:
    if value in (None, "", "待查询", "待确认"):
        return "待确认"
    try:
        return f"¥{float(value):.0f}"
    except (TypeError, ValueError):
        return str(value)


def duration_text(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}分钟"
    hours, mins = divmod(minutes, 60)
    return f"{hours}小时{mins}分钟" if mins else f"{hours}小时"


def availability_text(option: dict) -> str:
    """格式化航班/高铁余票或舱位库存状态。"""
    availability = (
        option.get("availability")
        or option.get("ticket_availability")
        or option.get("seat_availability")
        or option.get("inventory")
    )
    if availability:
        return str(availability)

    seat_class = option.get("seat_class") or option.get("cabin") or option.get("class")
    seats_left = option.get("seats_left")
    if seats_left is not None:
        return f"{seat_class or '余票'}：{seats_left}"

    if option.get("mode") == "train" or option.get("type", "").startswith("高铁"):
        return "二等座余票待确认"
    if option.get("mode") == "flight" or "飞" in option.get("type", "") or option.get("airline"):
        return "经济舱余票待确认"
    return "待确认"


def airline_score(option: dict, preferred_airlines: Optional[List[str]] = None) -> float:
    """按用户航司偏好打分；非航班方案给中性分。"""
    if option.get("mode") == "train" or option.get("type", "").startswith("高铁"):
        return 0.5

    preferred = preferred_airlines or DEFAULT_PREFERRED_AIRLINES
    airline_text = " ".join(str(option.get(key, "")) for key in ("airline", "airline_code", "name", "no"))
    if any(code in airline_text for code in preferred):
        return 1.0
    if option.get("major_airline"):
        return 0.75
    return 0.45


def rank_options(options: list, preferred_airlines: Optional[List[str]] = None) -> list:
    """按政策优先级、价格、总时长、时间偏好得分排序交通方案。"""
    options = [option for option in options if is_allowed_transport_option(option)]
    if not options:
        return []

    known_prices = [o.get("price") for o in options if isinstance(o.get("price"), (int, float))]
    known_durations = [o.get("duration_min") for o in options if isinstance(o.get("duration_min"), (int, float))]
    min_price = min(known_prices) if known_prices else 0
    max_price = max(known_prices) if known_prices else 0
    min_dur = min(known_durations) if known_durations else 0
    max_dur = max(known_durations) if known_durations else 0

    scored = []
    for option in options:
        price = option.get("price")
        duration = option.get("duration_min")
        price_score = 0.5
        duration_score = 0.5

        if isinstance(price, (int, float)) and max_price != min_price:
            price_score = 1 - (price - min_price) / (max_price - min_price)
        elif isinstance(price, (int, float)):
            price_score = 1

        if isinstance(duration, (int, float)) and max_dur != min_dur:
            duration_score = 1 - (duration - min_dur) / (max_dur - min_dur)
        elif isinstance(duration, (int, float)):
            duration_score = 1

        dep_hour = option.get("departure_hour", 12)
        time_score = max(0, 1 - abs(dep_hour - 12) / 12)
        type_priority = {
            "直飞": 1.0,
            "经停": 0.92,
            "经停同票": 0.92,
            "高铁直达": 0.9,
            "联程": 0.75,
            "高铁换乘": 0.7,
            "临近城市+高铁接驳": 0.6,
        }.get(option.get("type"), 0.5)

        total = (
            type_priority * 0.25
            + price_score * 0.18
            + duration_score * 0.25
            + time_score * 0.10
            + airline_score(option, preferred_airlines) * 0.22
        )
        scored.append((total, option))

    return [option for _, option in sorted(scored, key=lambda item: item[0], reverse=True)]


def is_allowed_transport_option(option: dict) -> bool:
    """过滤违反硬性差旅规则的交通方案。"""
    for key in ("connection_wait_min", "stopover_min", "transfer_wait_min"):
        wait = option.get(key)
        if isinstance(wait, (int, float)) and wait > 120:
            return False
    return True


def build_itinerary_timeline(trip: dict) -> list:
    timeline = []
    outbound = trip.get("outbound") or trip.get("outbound_flight") or {}
    inbound = trip.get("return") or trip.get("return_flight") or {}
    mode = outbound.get("mode", "flight")
    terminal_name = outbound.get("from_terminal", "机场" if mode == "flight" else "高铁站")
    arrival_terminal = outbound.get("to_terminal", "目的地机场" if mode == "flight" else "目的地高铁站")
    depart_scene = "airport_departure" if mode == "flight" else "station_departure"
    arrive_scene = "airport_arrival" if mode == "flight" else "station_arrival"
    local_to_terminal = int(trip.get("origin_to_terminal_min", trip.get("origin_to_airport_min", 45)))
    arrival_to_dest = int(trip.get("terminal_to_dest_min", trip.get("airport_to_dest_min", 45)))

    depart_date = trip["depart_date"]
    dep_time = outbound.get("dep", "待确认")
    if dep_time != "待确认":
        dep_dt = parse_datetime(depart_date, dep_time)
        terminal_arrival = dep_dt - timedelta(minutes=calculate_buffer_time(depart_scene))
        leave_origin = terminal_arrival - timedelta(minutes=local_to_terminal)
        timeline.extend([
            {
                "date": leave_origin.strftime("%Y-%m-%d"),
                "time": format_time(leave_origin),
                "event": f"从 {trip['origin_address']} 出发",
                "note": f"前往{terminal_name}，预计{duration_text(local_to_terminal)}",
            },
            {
                "date": terminal_arrival.strftime("%Y-%m-%d"),
                "time": format_time(terminal_arrival),
                "event": f"抵达{terminal_name}",
                "note": f"预留{duration_text(calculate_buffer_time(depart_scene))}办理值机/安检/候车",
            },
            {
                "date": depart_date,
                "time": dep_time,
                "event": f"乘坐 {outbound.get('no', '待确认班次')} 前往 {trip.get('dest_city', trip['dest_address'])}",
                "note": outbound.get("note", "经济舱/二等座按政策执行"),
            },
        ])
    else:
        timeline.append({
            "date": depart_date,
            "time": "待确认",
            "event": f"从 {trip['origin_address']} 出发并前往{terminal_name}",
            "note": "班次时间待实时查询后倒推",
        })

    arr_time = outbound.get("arr", "待确认")
    if arr_time != "待确认":
        arr_dt = parse_datetime(depart_date, arr_time)
        leave_terminal = arr_dt + timedelta(minutes=calculate_buffer_time(arrive_scene))
        arrive_dest = leave_terminal + timedelta(minutes=arrival_to_dest)
        timeline.extend([
            {
                "date": depart_date,
                "time": arr_time,
                "event": f"抵达{arrival_terminal}",
                "note": "",
            },
            {
                "date": leave_terminal.strftime("%Y-%m-%d"),
                "time": format_time(leave_terminal),
                "event": f"出站后前往 {trip['dest_address']}",
                "note": f"预计{duration_text(arrival_to_dest)}",
            },
            {
                "date": arrive_dest.strftime("%Y-%m-%d"),
                "time": format_time(arrive_dest),
                "event": "抵达目的地/酒店",
                "note": trip.get("hotel", {}).get("name", "汉庭酒店待确认"),
            },
        ])

    for visit in trip.get("client_visits", []):
        timeline.append({
            "date": visit.get("date", depart_date),
            "time": visit.get("time", "待确认"),
            "event": f"拜访 {visit.get('client', '客户')}",
            "note": visit.get("address", ""),
        })

    return_date = trip.get("return_date")
    if return_date:
        ret_mode = inbound.get("mode", mode)
        ret_terminal = inbound.get("from_terminal", arrival_terminal)
        ret_arrival_terminal = inbound.get("to_terminal", terminal_name)
        ret_scene = "airport_departure" if ret_mode == "flight" else "station_departure"
        dest_to_terminal = int(trip.get("dest_to_terminal_min", arrival_to_dest))
        ret_dep = inbound.get("dep", "待确认")
        if ret_dep != "待确认":
            ret_dep_dt = parse_datetime(return_date, ret_dep)
            ret_terminal_arrival = ret_dep_dt - timedelta(minutes=calculate_buffer_time(ret_scene))
            leave_dest = ret_terminal_arrival - timedelta(minutes=dest_to_terminal)
            timeline.extend([
                {
                    "date": leave_dest.strftime("%Y-%m-%d"),
                    "time": format_time(leave_dest),
                    "event": f"从酒店/客户处出发前往{ret_terminal}",
                    "note": f"预计{duration_text(dest_to_terminal)}",
                },
                {
                    "date": ret_terminal_arrival.strftime("%Y-%m-%d"),
                    "time": format_time(ret_terminal_arrival),
                    "event": f"抵达{ret_terminal}",
                    "note": f"预留{duration_text(calculate_buffer_time(ret_scene))}",
                },
                {
                    "date": return_date,
                    "time": ret_dep,
                    "event": f"乘坐 {inbound.get('no', '待确认班次')} 返回 {trip.get('origin_city', trip['origin_address'])}",
                    "note": inbound.get("note", "经济舱/二等座按政策执行"),
                },
            ])
        else:
            timeline.append({
                "date": return_date,
                "time": "待确认",
                "event": f"从目的地返程至 {trip.get('origin_city', trip['origin_address'])}",
                "note": "返程班次待实时查询",
            })

        ret_arr = inbound.get("arr")
        if ret_arr:
            timeline.append({
                "date": return_date,
                "time": ret_arr,
                "event": f"抵达{ret_arrival_terminal}",
                "note": "",
            })

    return sorted(timeline, key=lambda item: (item["date"], item["time"]))


def calculate_budget(trip: dict) -> dict:
    depart_date = datetime.strptime(trip["depart_date"], "%Y-%m-%d")
    return_date = trip.get("return_date")
    if return_date:
        end_date = datetime.strptime(return_date, "%Y-%m-%d")
        days = max((end_date - depart_date).days + 1, 1)
    else:
        days = 1
    nights = int(trip.get("nights", max(days - 1, 1 if trip.get("hotel_required", True) else 0)))

    outbound = trip.get("outbound") or trip.get("outbound_flight") or {}
    inbound = trip.get("return") or trip.get("return_flight") or {}
    flight_cost = sum(v for v in [outbound.get("price"), inbound.get("price")] if isinstance(v, (int, float)))
    train_cost = float(trip.get("train_cost", 0) or 0)
    hotel_price = float((trip.get("hotel") or {}).get("price", trip.get("hotel_price", 280)) or 280)
    local_km = float(trip.get("local_transport_km", 0) or 0)
    local_cost = float(trip.get("local_transport_cost", local_km * 3 if local_km else 200))

    budget = {
        "机票": flight_cost if flight_cost else "待确认",
        "高铁": train_cost,
        "市内交通": round(local_cost),
        "酒店": round(hotel_price * nights),
        "餐饮补贴": 100 * days,
    }
    known_total = sum(value for value in budget.values() if isinstance(value, (int, float)))
    budget["合计"] = f"{money(known_total)} + 待确认项" if any(isinstance(v, str) for v in budget.values()) else known_total
    budget["_days"] = days
    budget["_nights"] = nights
    return budget


def render_markdown(trip: dict) -> str:
    timeline = build_itinerary_timeline(trip)
    budget = calculate_budget(trip)
    options = rank_options(trip.get("transport_options", []), trip.get("preferred_airlines"))
    hotel = trip.get("hotel", {})
    risks = trip.get("risks") or [
        "航班/高铁票价和余票需以分贝通或官方平台下单时为准。",
        "市内交通耗时受天气、路况和高峰时段影响，建议出发前复核。",
    ]

    lines = [
        "# 出差行程单",
        "",
        "## 基本信息",
        "",
        "| 项目 | 内容 |",
        "|------|------|",
        f"| 出差人 | {trip.get('traveler', '-')} |",
        f"| 出差日期 | {format_date(trip['depart_date'])}" + (f" ~ {format_date(trip.get('return_date'))}" if trip.get("return_date") else "") + " |",
        f"| 出发地 | {trip.get('origin_address', '-')} |",
        f"| 目的地 | {trip.get('dest_address', '-')} |",
        f"| 出差目的 | {trip.get('purpose', '客户拜访')} |",
        "",
        "## 推荐方案摘要",
        "",
        trip.get("recommendation", "优先选择符合差旅政策且总耗时较短的直达交通方案；票价和余票以实时查询为准。"),
        "",
        "## 交通方案对比",
        "",
        "| 排名 | 方案 | 出发 | 到达 | 时长 | 费用 | 余票/库存 | 说明 |",
        "|------|------|------|------|------|------|-----------|------|",
    ]

    if options:
        for index, option in enumerate(options[:3], 1):
            stop_text = option.get("stop", "")
            stop_suffix = f" / 经停 {stop_text}" if stop_text else ""
            lines.append(
                f"| {index} | {option.get('name', option.get('no', '待确认'))}{stop_suffix} | "
                f"{option.get('dep', '待确认')} | {option.get('arr', '待确认')} | "
                f"{duration_text(int(option.get('duration_min', 0)))} | {money(option.get('price'))} | "
                f"{availability_text(option)} | "
                f"{option.get('type', '')} {option.get('note', '')} |"
            )
    else:
        lines.append("| - | 待实时查询 | 待确认 | 待确认 | 待确认 | 待确认 | 待确认 | MCP/订票平台接入后补齐 |")

    lines.extend([
        "",
        "## 详细时间线",
        "",
        "| 日期 | 时间 | 事项 | 备注 |",
        "|------|------|------|------|",
    ])
    for item in timeline:
        lines.append(f"| {format_date(item['date'])} | {item['time']} | {item['event']} | {item.get('note', '')} |")

    lines.extend([
        "",
        "## 酒店建议",
        "",
        f"- 酒店：{hotel.get('name', '目的地附近汉庭酒店（待确认具体门店）')}",
        f"- 房型：{hotel.get('room_type', '大床房')}",
        f"- 地址：{hotel.get('address', '待通过高德 POI 或分贝通确认')}",
        f"- 参考价：{money(hotel.get('price', 280))}/晚",
        "",
        "## 费用预算",
        "",
        "| 项目 | 金额 | 备注 |",
        "|------|------|------|",
        f"| 机票 | {money(budget['机票'])} | 经济舱 |",
        f"| 高铁 | {money(budget['高铁'])} | 二等座 |",
        f"| 市内交通 | {money(budget['市内交通'])} | 打车估算，约 3 元/km 或按已查路径 |",
        f"| 酒店 | {money(budget['酒店'])} | 汉庭大床房 × {budget['_nights']} 晚 |",
        f"| 餐饮补贴 | {money(budget['餐饮补贴'])} | 100 元/天 × {budget['_days']} 天 |",
        f"| **合计** | **{money(budget['合计'])}** | 含待确认项时需下单前复核 |",
        "",
        "## 出差检查清单",
        "",
        "### 证件类",
        "- [ ] 身份证",
        "- [ ] 名片",
        "- [ ] 工牌/门禁卡（如需进入客户办公区）",
        "",
        "### 设备类",
        "- [ ] 笔记本电脑 + 充电器",
        "- [ ] 手机 + 充电器 + 充电宝",
        "- [ ] 耳机/转接头/扩展坞",
        "- [ ] 翻页笔（如需演示）",
        "",
        "### 资料与预订",
        "- [ ] 客户资料、拜访议程、演示材料",
        "- [ ] 确认航班/高铁班次和酒店订单",
        "- [ ] 确认客户联系人、到访登记和会议室安排",
        "- [ ] 在分贝通完成预订并保存报销凭证",
        "",
        "## 风险与待确认事项",
        "",
    ])
    for risk in risks:
        lines.append(f"- {risk}")

    lines.extend([
        "",
        f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ])
    return "\n".join(lines) + "\n"


def demo_trip() -> dict:
    return {
        "traveler": "-",
        "origin_address": "无锡美的公园天下",
        "origin_city": "无锡",
        "dest_address": "北京市朝阳区XX大厦",
        "dest_city": "北京",
        "depart_date": "2026-05-15",
        "return_date": "2026-05-17",
        "purpose": "客户需求调研",
        "preferred_airlines": DEFAULT_PREFERRED_AIRLINES,
        "origin_to_terminal_min": 45,
        "terminal_to_dest_min": 60,
        "dest_to_terminal_min": 60,
        "local_transport_km": 70,
        "outbound": {
            "mode": "flight",
            "no": "MU1234",
            "from_terminal": "无锡/上海机场",
            "to_terminal": "北京首都机场",
            "dep": "08:00",
            "arr": "10:30",
            "price": 800,
            "note": "经济舱，具体机场以实时查询为准",
        },
        "return": {
            "mode": "flight",
            "no": "MU1235",
            "from_terminal": "北京首都机场",
            "to_terminal": "无锡/上海机场",
            "dep": "18:00",
            "arr": "20:30",
            "price": 800,
        },
        "hotel": {
            "name": "汉庭酒店（目的地附近门店待确认）",
            "room_type": "大床房",
            "address": "北京市朝阳区，靠近客户地址",
            "price": 280,
        },
        "client_visits": [
            {
                "date": "2026-05-16",
                "time": "10:00",
                "client": "客户A",
                "address": "北京市朝阳区XX大厦",
            }
        ],
        "transport_options": [
            {"name": "MU1234 直飞", "no": "MU1234", "airline": "东方航空", "type": "直飞", "dep": "08:00", "arr": "10:30", "duration_min": 150, "price": 800, "departure_hour": 8, "availability": "经济舱有票"},
            {"name": "CA1234 直飞", "no": "CA1234", "airline": "中国国际航空", "major_airline": True, "type": "直飞", "dep": "08:15", "arr": "10:45", "duration_min": 150, "price": 760, "departure_hour": 8, "availability": "经济舱剩余9张"},
            {"name": "高铁 Gxxx", "mode": "train", "type": "高铁直达", "dep": "09:00", "arr": "14:30", "duration_min": 330, "price": 553, "departure_hour": 9, "availability": "二等座有票"},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成顾问出差行程单 Markdown")
    parser.add_argument("--input", help="输入 JSON 文件；省略时使用内置示例")
    parser.add_argument("--output", help="输出 Markdown 文件；省略时打印到 stdout")
    args = parser.parse_args()

    if args.input:
        trip = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        trip = demo_trip()

    markdown = render_markdown(trip)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"已生成：{args.output}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
