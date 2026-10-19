"""把 B站客户端、本地存储和统计拼起来：登录状态、后台同步、给页面的数据。"""

from __future__ import annotations

import io
import threading
import time
from typing import Any, Callable

import qrcode
import qrcode.image.svg

from . import analysis
from .bili import QR_SUCCESS, BiliClient, BiliError, NotLoggedIn
from .storage import Storage

# 增量同步时，在已有最新记录之前再往回翻一周，补上 B站延迟入账或状态变化（比如退回）的记录
RECHECK_WINDOW = 7 * 86400
BLIND_INFO_TTL = 3 * 86400
CARD_TTL = 7 * 86400
PAGE_DELAY = 0.35


def qr_svg(text: str) -> str:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


class SyncState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.text = ""
        self.error = ""
        self.finished_at: int | None = None
        self.added = 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "text": self.text,
                "error": self.error,
                "finished_at": self.finished_at,
                "added": self.added,
            }

    def set(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)


class App:
    demo = False

    def __init__(self, storage: Storage):
        self.storage = storage
        self.sync = SyncState()
        self._qr: dict[str, BiliClient] = {}
        self._lock = threading.Lock()

    # ---------- 登录 ----------

    def session(self) -> dict[str, Any] | None:
        return self.storage.load_session()

    def client(self) -> BiliClient:
        s = self.session()
        if not s:
            raise NotLoggedIn(-101, "还没有登录")
        return BiliClient(s["cookies"])

    def status(self) -> dict[str, Any]:
        s = self.session()
        if not s:
            return {"logged_in": False, "demo": self.demo}
        meta = self.storage.load_meta(s["mid"])
        return {
            "logged_in": True,
            "demo": self.demo,
            "user": {"mid": s["mid"], "uname": s.get("uname", ""), "face": s.get("face", "")},
            "last_sync": meta.get("last_sync"),
            "records": len(self.storage.load_records(s["mid"])),
        }

    def qr_start(self) -> dict[str, Any]:
        c = BiliClient()
        url, key = c.qr_generate()
        with self._lock:
            self._qr = {key: c}  # 只保留最新一张二维码
        return {"key": key, "svg": qr_svg(url)}

    def qr_poll(self, key: str) -> dict[str, Any]:
        with self._lock:
            c = self._qr.get(key)
        if not c:
            return {"code": -1, "message": "二维码已过期，请刷新"}
        code, message, refresh_token = c.qr_poll(key)
        if code != QR_SUCCESS:
            return {"code": code, "message": message}
        if "SESSDATA" not in c.cookies:
            return {"code": -1, "message": "B站确认了登录，但没有返回 SESSDATA，请重试"}
        c.ensure_buvid()
        nav = c.nav()
        self.storage.save_session(
            {
                "cookies": c.cookies,
                "refresh_token": refresh_token,
                "mid": nav["mid"],
                "uname": nav.get("uname", ""),
                "face": analysis.https(nav.get("face")),
                "saved_at": int(time.time()),
            }
        )
        with self._lock:
            self._qr.clear()
        return {"code": 0, "message": "登录成功"}

    def logout(self, purge: bool) -> dict[str, Any]:
        s = self.session()
        revoked = False
        if s:
            try:
                BiliClient(s["cookies"]).logout()
                revoked = True
            except BiliError:
                pass
            if purge:
                self.storage.purge_user(s["mid"])
        self.storage.clear_session()
        return {"revoked": revoked}

    # ---------- 同步 ----------

    def start_sync(self) -> dict[str, Any]:
        s = self.session()
        if not s:
            raise NotLoggedIn(-101, "还没有登录")
        with self.sync.lock:
            if self.sync.running:
                return {"started": False}
            self.sync.running = True
            self.sync.error = ""
            self.sync.added = 0
            self.sync.text = "准备同步…"
        threading.Thread(target=self._sync_worker, args=(s,), daemon=True).start()
        return {"started": True}

    def _sync_worker(self, s: dict[str, Any]) -> None:
        mid = s["mid"]
        try:
            c = BiliClient(s["cookies"])
            nav = c.nav()  # 顺便确认凭证还有效，并刷新昵称头像
            s.update(uname=nav.get("uname", s.get("uname")), face=analysis.https(nav.get("face")))
            self.storage.save_session(s)

            records, added = sync_pay_records(c, self.storage.load_records(mid), lambda t: self.sync.set(text=t))
            self.storage.save_records(mid, records)
            self.sync.set(added=added)

            self.sync.set(text="正在同步盲盒记录…")
            boxes = refresh_blind_boxes(c, self.storage, records, lambda t: self.sync.set(text=t))
            draws = sync_blind_draws(c, boxes, self.storage.load_draws(mid), lambda t: self.sync.set(text=t))
            self.storage.save_draws(mid, draws)

            self.sync.set(text="正在更新主播头像…")
            self.anchor_cards([r.get("ruid") for r in records], refresh=True)

            self.storage.save_meta(mid, {**self.storage.load_meta(mid), "last_sync": int(time.time())})
            self.sync.set(text=f"同步完成，新增 {added} 条消费记录")
        except NotLoggedIn:
            self.sync.set(error="登录已失效，请退出后重新扫码登录")
        except BiliError as e:
            self.sync.set(error=f"同步失败：{e.message}")
        except Exception as e:  # 后台线程里兜底，错误显示到页面上
            self.sync.set(error=f"同步出错：{e}")
        finally:
            self.sync.set(running=False, finished_at=int(time.time()))

    # ---------- 页面数据 ----------

    def _mid(self) -> int:
        s = self.session()
        if not s:
            raise NotLoggedIn(-101, "还没有登录")
        return s["mid"]

    def records(self) -> list[dict[str, Any]]:
        return self.storage.load_records(self._mid())

    def blindbox(self, rng: analysis.Range) -> dict[str, Any]:
        mid = self._mid()
        return analysis.blindbox_report(
            self.storage.load_blind_boxes(), self.storage.load_draws(mid), rng, self.storage.load_records(mid)
        )

    def bubbles(self, rng: analysis.Range, metric: str) -> list[dict[str, Any]]:
        items = analysis.anchor_bubbles(self.records(), rng, metric)
        cards = self.anchor_cards([a["ruid"] for a in items])
        for a in items:
            card = cards.get(str(a["ruid"])) or {}
            a["face"] = card.get("face", "")
            a["name"] = a["name"] or card.get("name", "")
            a["room_id"] = a["room_id"] or card.get("room_id", 0)
        return items

    def anchor_cards(self, ruids: list[Any], refresh: bool = False) -> dict[str, Any]:
        """主播头像缓存：缺的（或 refresh 时过期的）按 50 个一批向 B站补。查不到的也记下，避免反复请求。"""
        cache = self.storage.load_anchor_cards()
        now = int(time.time())
        want = [
            int(u) for u in dict.fromkeys(ruids)
            if u and (str(u) not in cache or (refresh and now - cache[str(u)].get("updated_at", 0) > CARD_TTL))
        ]
        if want:
            c = BiliClient()  # 公开接口，不带登录 Cookie
            for i in range(0, len(want), 50):
                batch = want[i : i + 50]
                try:
                    got = c.anchor_cards(batch)
                except BiliError:
                    break
                for uid in batch:
                    info = got.get(str(uid)) or {}
                    cache[str(uid)] = {
                        "name": info.get("uname", ""),
                        "face": analysis.https(info.get("face")),
                        "room_id": info.get("room_id", 0),
                        "updated_at": now,
                    }
                time.sleep(PAGE_DELAY)
            self.storage.save_anchor_cards(cache)
        return cache

    def synthesis(self) -> dict[str, Any]:
        cfg = self.storage.load_config()
        return {"activities": analysis.synthesis_report(self.records(), cfg["synthesis"]), "config": cfg["synthesis"]}

    def medals(self) -> list[dict[str, Any]]:
        c = self.client()
        out: list[dict[str, Any]] = []
        for page in range(1, 51):
            data = c.medals_page(page)
            for item in (data.get("list") or []) + (data.get("special_list") or []):
                out.append(normalize_medal(item))
            if not (data.get("page_info") or {}).get("has_more"):
                break
            time.sleep(PAGE_DELAY)
        seen, uniq = set(), []
        for m in out:
            if m["medal_id"] not in seen:
                seen.add(m["medal_id"])
                uniq.append(m)
        return uniq

    def wallet(self) -> dict[str, Any]:
        w = self.client().wallet() or {}
        gold = float(w.get("gold") or 0)
        return {"battery": analysis.num(gold / 100), "silver": w.get("silver", 0), "bp": w.get("bp", "0")}


def normalize_medal(item: dict[str, Any]) -> dict[str, Any]:
    m = item.get("medal") or {}
    a = item.get("anchor_info") or {}
    room = item.get("room_info") or {}
    return {
        "medal_id": m.get("medal_id"),
        "name": m.get("medal_name", ""),
        "level": m.get("level", 0),
        "intimacy": m.get("intimacy", 0),
        "next_intimacy": m.get("next_intimacy", 0),
        "today_feed": m.get("today_feed", 0),
        "day_limit": m.get("day_limit", 0),
        "lighted": m.get("is_lighted") == 1,
        "wearing": m.get("wearing_status") == 1,
        "guard_level": m.get("guard_level", 0),
        "anchor": a.get("nick_name", ""),
        "anchor_face": analysis.https(a.get("avatar")),
        "ruid": m.get("target_id") or a.get("uid"),
        "room_id": room.get("room_id", 0),
        "living": room.get("living_status") == 1,
    }


# ---------- 同步细节（独立成函数，方便单测） ----------


def sync_pay_records(
    c: BiliClient, existing: list[dict[str, Any]], progress: Callable[[str], None]
) -> tuple[list[dict[str, Any]], int]:
    """增量拉取消费记录。已有数据时只翻到「最新记录往前一周」为止。"""
    by_id = {r["id"]: r for r in existing if r.get("id") is not None}
    latest = max((int(r.get("timestamp") or 0) for r in existing), default=0)
    cutoff = latest - RECHECK_WINDOW if latest else 0
    added = 0
    next_id = None
    for page in range(1, 1001):
        data = c.pay_record_page(next_id)
        rows = data.get("list") or []
        if not rows:
            break
        stop = False
        for r in rows:
            if cutoff and int(r.get("timestamp") or 0) < cutoff:
                stop = True
                break
            if r.get("id") not in by_id:
                added += 1
            by_id[r.get("id")] = r  # 已有的也覆盖，拿到最新状态（例如被退回）
        progress(f"正在拉取消费记录：第 {page} 页，新增 {added} 条")
        next_id = (data.get("params") or {}).get("next_id")
        if stop or not next_id:
            break
        time.sleep(PAGE_DELAY)
    merged = sorted(by_id.values(), key=lambda r: (int(r.get("timestamp") or 0), int(r.get("id") or 0)), reverse=True)
    return merged, added


def refresh_blind_boxes(
    c: BiliClient, storage: Storage, records: list[dict[str, Any]], progress: Callable[[str], None]
) -> dict[str, Any]:
    """从礼物列表、消费记录和手动配置里找出盲盒，并缓存各盲盒的单价和奖池。"""
    cache = storage.load_blind_boxes()
    candidates: dict[int, dict[str, Any]] = {}
    try:
        for g in c.gift_catalog():
            if "盲盒" in (g.get("name") or "") and "测试" not in g["name"]:
                candidates[int(g["id"])] = {"name": g["name"], "img": g.get("img_basic", "")}
    except BiliError:
        pass
    for r in records:
        if "盲盒" in (r.get("gift_name") or "") and r.get("gift_id"):
            candidates.setdefault(int(r["gift_id"]), {"name": r["gift_name"], "img": r.get("gift_img", "")})
    for gid in storage.load_config().get("extra_blind_box_ids", []):
        candidates.setdefault(int(gid), {"name": "", "img": ""})

    now = int(time.time())
    for gid, hint in candidates.items():
        old = cache.get(str(gid))
        if old and now - old.get("updated_at", 0) < BLIND_INFO_TTL:
            continue
        progress(f"正在读取盲盒奖池：{hint['name'] or gid}")
        info = c.blind_box_info(gid)
        if info is None:
            cache[str(gid)] = {"not_blind_box": True, "updated_at": now}
            continue
        cache[str(gid)] = {
            "name": info.get("blind_gift_name") or hint["name"],
            "img": hint["img"],
            "blind_price": analysis.num(float(info.get("blind_price") or 0) / 100),
            "gifts": [
                {
                    "gift_id": g.get("gift_id"),
                    "name": g.get("gift_name", ""),
                    "img": g.get("gift_img", ""),
                    "price": analysis.num(float(g.get("price") or 0) / 100),
                    "chance": g.get("chance", ""),
                }
                for g in info.get("gifts", [])
            ],
            "updated_at": now,
        }
        time.sleep(PAGE_DELAY)
    storage.save_blind_boxes(cache)
    return {k: v for k, v in cache.items() if not v.get("not_blind_box")}


def sync_blind_draws(
    c: BiliClient,
    boxes: dict[str, Any],
    existing: dict[str, list[dict[str, Any]]],
    progress: Callable[[str], None],
) -> dict[str, list[dict[str, Any]]]:
    out = dict(existing)
    for box_id, info in boxes.items():
        old = out.get(box_id, [])
        latest = max((int(d.get("timestamp") or 0) for d in old), default=0)
        new: list[dict[str, Any]] = []
        cursor = None
        for page in range(1, 101):
            data = c.blind_draw_page(int(box_id), cursor)
            rows = data.get("list") or []
            stop = False
            for d in rows:
                if latest and int(d.get("timestamp") or 0) <= latest:
                    stop = True
                    break
                new.append(
                    {k: d.get(k) for k in ("gift_id", "gift_name", "gift_num", "gift_img", "timestamp", "ruid", "rname",
                                           "original_gift_id", "original_gift_name")}
                )
            progress(f"正在同步「{info.get('name')}」开盒记录：第 {page} 页")
            params = data.get("params") or {}
            more = params.get("have_more")
            if more is None:
                more = data.get("isMore") in (1, "1") or data.get("has_more") == 1
            if stop or not rows or not more:
                break
            cursor = params
            time.sleep(PAGE_DELAY)
        if new or old:
            out[box_id] = new + old
    return out
