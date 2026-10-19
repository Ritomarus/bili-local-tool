"""纯计算：把本地保存的消费记录 / 开盒记录汇总成页面要用的统计。不发任何网络请求。

金额单位统一用「电池」（10 电池 = 1 元）。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
BAG = "包裹道具"
REFUNDED = "已退回"
DAY = 86400


# ---------- 基础 ----------


def coin(r: dict[str, Any]) -> float:
    raw = r.get("pay_coin") or r.get("coin") or "0"
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        return 0.0


def is_bag(r: dict[str, Any]) -> bool:
    return r.get("bag_desc") == BAG


def is_refunded(r: dict[str, Any]) -> bool:
    return r.get("status_msg") == REFUNDED


def local(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, TZ)


def https(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url.replace("http://", "https://", 1)


def num(x: float) -> float | int:
    """整数就返回 int，JSON 里少一串 .0"""
    return int(x) if float(x).is_integer() else round(x, 2)


@dataclass(frozen=True)
class Range:
    start: int | None = None  # 含
    end: int | None = None  # 不含
    label: str = "全部时间"

    def contains(self, ts: int) -> bool:
        return (self.start is None or ts >= self.start) and (self.end is None or ts < self.end)


def _day_start(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())


def resolve_range(name: str = "all", start: str = "", end: str = "", now: dt.datetime | None = None) -> Range:
    now = now or dt.datetime.now(TZ)
    today = now.date()
    if name == "7d":
        return Range(_day_start(today - dt.timedelta(days=6)), None, "近 7 天")
    if name == "30d":
        return Range(_day_start(today - dt.timedelta(days=29)), None, "近 30 天")
    if name == "this_month":
        return Range(_day_start(today.replace(day=1)), None, "本月")
    if name == "last_month":
        first = today.replace(day=1)
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        return Range(_day_start(prev), _day_start(first), "上月")
    if name == "this_year":
        return Range(_day_start(today.replace(month=1, day=1)), None, "今年")
    if name == "custom":
        try:
            s = dt.date.fromisoformat(start) if start else None
            e = dt.date.fromisoformat(end) if end else None
        except ValueError:
            return Range()
        return Range(
            _day_start(s) if s else None,
            _day_start(e + dt.timedelta(days=1)) if e else None,
            f"{start or '最早'} ~ {end or '至今'}",
        )
    return Range()


def normalize(r: dict[str, Any]) -> dict[str, Any]:
    ts = int(r.get("timestamp") or 0)
    return {
        "id": r.get("id"),
        "ts": ts,
        "time": local(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
        "gift_id": r.get("gift_id"),
        "gift_name": r.get("gift_name") or "",
        "gift_img": https(r.get("gift_img")),
        "gift_num": int(r.get("gift_num") or 0),
        "coin": num(coin(r)),
        "ruid": r.get("ruid"),
        "anchor": r.get("r_uname") or (f"UID {r.get('ruid')}" if r.get("ruid") else ""),
        "bag": is_bag(r),
        "refunded": is_refunded(r),
    }


def _valid(records: Iterable[dict[str, Any]], rng: Range) -> list[dict[str, Any]]:
    return [r for r in records if not is_refunded(r) and rng.contains(int(r.get("timestamp") or 0))]


def _month_span(first: dt.date, last: dt.date) -> list[str]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------- 概览 ----------


def overview(records: list[dict[str, Any]], rng: Range) -> dict[str, Any]:
    rows = _valid(records, rng)
    paid = [r for r in rows if not is_bag(r)]
    refunded = sum(1 for r in records if is_refunded(r) and rng.contains(int(r.get("timestamp") or 0)))

    month = defaultdict(lambda: {"spent": 0.0, "bag": 0.0})
    day: dict[str, float] = defaultdict(float)
    hour = [0.0] * 24
    anchors: dict[Any, dict[str, Any]] = {}
    gifts: dict[Any, dict[str, Any]] = {}

    for r in rows:
        ts = int(r.get("timestamp") or 0)
        t = local(ts)
        c = coin(r)
        bag = is_bag(r)
        month[t.strftime("%Y-%m")]["bag" if bag else "spent"] += c

        a = anchors.setdefault(
            r.get("ruid"),
            {"ruid": r.get("ruid"), "name": "", "spent": 0.0, "bag": 0.0, "count": 0, "last_ts": 0},
        )
        a["name"] = a["name"] or r.get("r_uname") or f"UID {r.get('ruid')}"
        a["bag" if bag else "spent"] += c
        a["count"] += 1
        a["last_ts"] = max(a["last_ts"], ts)

        if bag:
            continue
        day[t.strftime("%Y-%m-%d")] += c
        hour[t.hour] += c
        g = gifts.setdefault(
            (r.get("gift_id"), r.get("gift_name")),
            {"gift_id": r.get("gift_id"), "name": r.get("gift_name") or "", "img": "", "num": 0, "spent": 0.0},
        )
        g["img"] = g["img"] or https(r.get("gift_img"))
        g["num"] += int(r.get("gift_num") or 0)
        g["spent"] += c

    stamps = [int(r.get("timestamp") or 0) for r in rows]
    months = []
    if stamps:
        span = _month_span(local(min(stamps)).date(), local(max(stamps)).date())
        months = [{"month": m, "spent": num(month[m]["spent"]), "bag": num(month[m]["bag"])} for m in span]

    spent = sum(coin(r) for r in paid)
    big_day, big_day_spent = max(day.items(), key=lambda kv: kv[1], default=(None, 0))
    anchor_list = sorted(anchors.values(), key=lambda a: a["spent"] + a["bag"], reverse=True)
    for a in anchor_list:
        a["spent"], a["bag"] = num(a["spent"]), num(a["bag"])
    gift_list = sorted(gifts.values(), key=lambda g: g["spent"], reverse=True)
    for g in gift_list:
        g["spent"] = num(g["spent"])

    return {
        "range": rng.label,
        "totals": {
            "spent": num(spent),
            "yuan": round(spent / 10, 2),
            "bag_value": num(sum(coin(r) for r in rows if is_bag(r))),
            "count": len(paid),
            "bag_count": len(rows) - len(paid),
            "gift_num": sum(int(r.get("gift_num") or 0) for r in paid),
            "anchors": len({r.get("ruid") for r in paid}),
            "days_active": len(day),
            "first_ts": min(stamps) if stamps else None,
            "last_ts": max(stamps) if stamps else None,
            "refunded": refunded,
            "biggest_day": {"date": big_day, "spent": num(big_day_spent)},
        },
        "by_month": months,
        "by_day": [{"date": d, "spent": num(v)} for d, v in sorted(day.items())],
        "by_hour": [num(v) for v in hour],
        "top_anchors": anchor_list[:15],
        "anchor_count": len(anchor_list),
        "top_gifts": gift_list[:15],
        "biggest": [normalize(r) for r in sorted(paid, key=coin, reverse=True)[:10]],
    }


# ---------- 消费明细 ----------


def records_page(
    records: list[dict[str, Any]],
    rng: Range,
    q: str = "",
    ruid: str = "",
    kind: str = "all",
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    q = q.strip().lower()
    rows = []
    for r in records:
        if not rng.contains(int(r.get("timestamp") or 0)):
            continue
        if kind == "paid" and (is_bag(r) or is_refunded(r)):
            continue
        if kind == "bag" and not is_bag(r):
            continue
        if kind == "refunded" and not is_refunded(r):
            continue
        if ruid and str(r.get("ruid")) != ruid:
            continue
        if q and q not in (r.get("gift_name") or "").lower() and q not in (r.get("r_uname") or "").lower():
            continue
        rows.append(r)
    rows.sort(key=lambda r: (int(r.get("timestamp") or 0), int(r.get("id") or 0)), reverse=True)

    anchors: dict[Any, str] = {}
    for r in records:
        if r.get("ruid") is not None:
            anchors.setdefault(r["ruid"], r.get("r_uname") or f"UID {r['ruid']}")

    pages = max(1, -(-len(rows) // size))
    page = min(max(1, page), pages)
    return {
        "total": len(rows),
        "page": page,
        "pages": pages,
        "sum": num(sum(coin(r) for r in rows if not is_refunded(r))),
        "items": [normalize(r) for r in rows[(page - 1) * size : page * size]],
        "anchors": sorted(({"ruid": k, "name": v} for k, v in anchors.items()), key=lambda a: a["name"]),
    }


# ---------- 盲盒 ----------


def _is_box_record(r: dict[str, Any], boxes: dict[str, dict[str, Any]]) -> bool:
    return "盲盒" in (r.get("gift_name") or "") or str(r.get("gift_id")) in boxes


def blindbox_report(
    boxes: dict[str, dict[str, Any]],
    draws: dict[str, list[dict[str, Any]]],
    rng: Range,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """盲盒统计，两个来源：

    - 消费记录：买了几个、花了多少电池（一直都有）
    - 开盒记录（drawStream）：每个盲盒开出了什么。B站只返回最近一段时间的，更早的查不到

    盈亏只能按有开盒结果的那部分算：爆出价值 − 盲盒单价 × 有结果的开盒数。
    boxes 里的价格已经换算成电池。
    """
    boxes = {k: v for k, v in boxes.items() if not v.get("not_blind_box")}
    bought: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records or []:
        if not is_refunded(r) and _is_box_record(r, boxes) and rng.contains(int(r.get("timestamp") or 0)):
            bought[str(r.get("gift_id"))].append(r)

    out = []
    for box_id in set(bought) | {k for k in draws if k in boxes}:
        info = boxes.get(box_id, {})
        purchases = bought.get(box_id, [])
        rows = [d for d in draws.get(box_id, []) if rng.contains(int(d.get("timestamp") or 0))]
        if not purchases and not rows:
            continue
        pool = {int(g["gift_id"]): g for g in info.get("gifts", [])}
        bought_n = sum(int(r.get("gift_num") or 0) for r in purchases)
        bought_spent = sum(coin(r) for r in purchases if not is_bag(r))
        price = float(info.get("blind_price") or 0) or (bought_spent / bought_n if bought_n else 0)

        gifts: dict[int, dict[str, Any]] = {}
        anchors: dict[Any, dict[str, Any]] = {}
        months = defaultdict(lambda: {"spent": 0.0, "value": 0.0})
        total_n, value, unknown = 0, 0.0, 0
        for d in rows:
            n = int(d.get("gift_num") or 0)
            gid = int(d.get("gift_id") or 0)
            known = pool.get(gid)
            unit = float(known["price"]) if known else 0.0
            if not known:
                unknown += n
            total_n += n
            value += unit * n

            g = gifts.setdefault(
                gid,
                {
                    "gift_id": gid,
                    "name": d.get("gift_name") or (known or {}).get("name", ""),
                    "img": https(d.get("gift_img") or (known or {}).get("img")),
                    "price": num(unit),
                    "count": 0,
                    "chance": (known or {}).get("chance", ""),
                },
            )
            g["count"] += n

            a = anchors.setdefault(
                d.get("ruid"), {"ruid": d.get("ruid"), "name": d.get("rname") or "", "draws": 0, "value": 0.0}
            )
            a["draws"] += n
            a["value"] += unit * n

            m = months[local(int(d.get("timestamp") or 0)).strftime("%Y-%m")]
            m["spent"] += price * n
            m["value"] += unit * n

        spent = price * total_n
        gift_list = sorted(gifts.values(), key=lambda g: (-float(g["price"]), g["gift_id"]))
        # 奖池里有但一次都没开出来的礼物也列出来，方便对照概率
        for gid, g in pool.items():
            if gid not in gifts:
                gift_list.append(
                    {"gift_id": gid, "name": g.get("name", ""), "img": https(g.get("img")), "price": num(float(g["price"])),
                     "count": 0, "chance": g.get("chance", "")}
                )
        gift_list.sort(key=lambda g: (-float(g["price"]), g["gift_id"]))
        for g in gift_list:
            g["share"] = round(g["count"] / total_n * 100, 2) if total_n else 0

        # 按直播间：购买来自消费记录，开盒结果来自开盒记录
        for r in purchases:
            a = anchors.setdefault(
                r.get("ruid"), {"ruid": r.get("ruid"), "name": r.get("r_uname") or "", "draws": 0, "value": 0.0}
            )
            a["name"] = a["name"] or r.get("r_uname") or ""
            a["bought"] = a.get("bought", 0) + int(r.get("gift_num") or 0)
            a["bought_spent"] = a.get("bought_spent", 0.0) + (0 if is_bag(r) else coin(r))
        anchor_list = []
        for a in anchors.values():
            s = price * a["draws"]
            anchor_list.append({
                "ruid": a["ruid"], "name": a["name"],
                "bought": a.get("bought", 0), "spent": num(a.get("bought_spent", s)),
                "draws": a["draws"], "value": num(a["value"]),
                "profit": num(a["value"] - s) if a["draws"] else None,
            })
        anchor_list.sort(key=lambda a: (a["bought"], a["draws"]), reverse=True)

        last_ts = max([int(r.get("timestamp") or 0) for r in purchases] + [int(d.get("timestamp") or 0) for d in rows])
        out.append(
            {
                "id": box_id,
                "name": info.get("name") or (purchases[0].get("gift_name") if purchases else "") or f"盲盒 {box_id}",
                "img": https(info.get("img") or (purchases[0].get("gift_img") if purchases else "")),
                "blind_price": num(price),
                "has_pool": bool(pool),
                "bought": bought_n,
                # 有消费记录就用实际花的电池，否则按开盒数 × 单价估算
                "spent": num(bought_spent if purchases else spent),
                "draws": total_n,
                "draw_spent": num(spent),
                "value": num(value),
                "profit": num(value - spent) if total_n else None,
                "rate": round(value / spent * 100, 1) if total_n and spent else None,
                "missing": max(0, bought_n - total_n),
                "unknown": unknown,
                "last_ts": last_ts,
                "gifts": gift_list if total_n else [],
                "anchors": anchor_list,
                "months": [
                    {"month": k, "spent": num(v["spent"]), "value": num(v["value"]), "profit": num(v["value"] - v["spent"])}
                    for k, v in sorted(months.items())
                ],
            }
        )
    out.sort(key=lambda b: (float(b["spent"]), b["last_ts"]), reverse=True)
    known_spent = sum(float(b["draw_spent"]) for b in out if b["draws"])
    value = sum(float(b["value"]) for b in out)
    known = sum(b["draws"] for b in out)
    return {
        "range": rng.label,
        "boxes": out,
        "totals": {
            "bought": sum(b["bought"] or b["draws"] for b in out),
            "spent": num(sum(float(b["spent"]) for b in out)),
            "draws": known,
            "value": num(value),
            "profit": num(value - known_spent) if known else None,
            "rate": round(value / known_spent * 100, 1) if known and known_spent else None,
            "missing": sum(b["missing"] for b in out),
        },
    }


# ---------- 合成活动 ----------


def _date_ts(s: str, end: bool = False) -> int | None:
    if not s:
        return None
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        return None
    return _day_start(d + dt.timedelta(days=1)) if end else _day_start(d)


def synthesis_report(records: list[dict[str, Any]], activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """支出：买材料花的电池；收入：背包里合成出的成品送出去时的价值。"""
    out = []
    for act in activities:
        materials = [m for m in act.get("materials", []) if m]
        products = [p for p in act.get("products", []) if p]
        start, end = _date_ts(act.get("start", "")), _date_ts(act.get("end", ""), end=True)
        # 活动结束后背包里的成品往往还要过一两天才送完，成品多放宽 49 小时
        product_end = end + 49 * 3600 if end else None

        spent = earned = 0.0
        mat_n = prod_n = 0
        prods: dict[Any, dict[str, Any]] = {}
        anchors: dict[Any, dict[str, Any]] = {}
        for r in records:
            if is_refunded(r):
                continue
            ts = int(r.get("timestamp") or 0)
            name = r.get("gift_name") or ""
            c = coin(r)
            n = int(r.get("gift_num") or 0)
            in_act = (start is None or ts >= start) and (end is None or ts < end)
            in_prod = (start is None or ts >= start) and (product_end is None or ts < product_end)

            if in_act and c > 0 and any(m in name for m in materials):
                spent += c
                mat_n += n
                side = "spent"
            elif is_bag(r) and in_prod and any(p in name for p in products):
                earned += c
                prod_n += n
                p = prods.setdefault(
                    r.get("gift_id"),
                    {"gift_id": r.get("gift_id"), "name": name, "img": https(r.get("gift_img")), "count": 0, "value": 0.0},
                )
                p["count"] += n
                p["value"] += c
                side = "earned"
            else:
                continue
            a = anchors.setdefault(
                r.get("ruid"), {"ruid": r.get("ruid"), "name": r.get("r_uname") or "", "spent": 0.0, "earned": 0.0}
            )
            a[side] += c

        prod_list = sorted(prods.values(), key=lambda p: p["value"], reverse=True)
        for p in prod_list:
            p["price"] = num(p["value"] / p["count"]) if p["count"] else 0
            p["value"] = num(p["value"])
        anchor_list = sorted(
            ({**a, "spent": num(a["spent"]), "earned": num(a["earned"]), "profit": num(a["earned"] - a["spent"])}
             for a in anchors.values()),
            key=lambda a: a["spent"] + a["earned"],
            reverse=True,
        )
        out.append(
            {
                "name": act.get("name") or "未命名活动",
                "spent": num(spent),
                "earned": num(earned),
                "profit": num(earned - spent),
                "materials_num": mat_n,
                "products_num": prod_n,
                "products": prod_list,
                "anchors": anchor_list,
            }
        )
    return out
