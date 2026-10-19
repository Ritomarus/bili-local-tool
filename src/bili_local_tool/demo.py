"""演示模式：用随机生成的假数据跑整个界面，不登录、不访问任何网络。"""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path
from typing import Any

from . import analysis
from .service import App
from .storage import Storage

ANCHORS = [
    (10001, "星野小鹿"), (10002, "阿柚Yuzu"), (10003, "白桃乌龙"), (10004, "夜航船长"),
    (10005, "猫猫头不睡觉"), (10006, "糯米团子"), (10007, "银河打工人"), (10008, "雾岛听风"),
    (10009, "番茄炒蛋"), (10010, "小鲸鱼Whale"),
]
# (礼物ID, 名称, 单价电池)
GIFTS = [
    (31036, "小花花", 1), (31039, "打call", 5), (31037, "牛哇牛哇", 1), (31164, "这个好诶", 10),
    (31531, "情书", 52), (31025, "告白气球", 520), (31842, "星河入梦", 1999), (31200, "冲鸭", 100),
    (31214, "干杯", 66), (31216, "花式夸夸", 29),
]
MATERIALS = [(33901, "玲珑宝斋-锦囊", 10), (33902, "玲珑宝斋-瓷瓶", 50), (33903, "玲珑宝斋-银盒", 100)]
PRODUCTS = [(33911, "相识玉扣", 20), (33912, "常伴珠钗", 66), (33913, "缘起瓷瓶", 200), (33914, "倾心宝冠", 1000)]
BOX = {
    "32251": {
        "name": "心动盲盒",
        "img": "",
        "blind_price": 150,
        "gifts": [
            {"gift_id": 32124, "name": "浪漫城堡", "img": "", "price": 22330, "chance": "0.04%"},
            {"gift_id": 32125, "name": "蛇年限定", "img": "", "price": 1000, "chance": "0.5%"},
            {"gift_id": 32128, "name": "梦幻游乐园", "img": "", "price": 299, "chance": "6%"},
            {"gift_id": 32130, "name": "电影票", "img": "", "price": 138, "chance": "40%"},
            {"gift_id": 32131, "name": "小蛋糕", "img": "", "price": 66, "chance": "35%"},
            {"gift_id": 32132, "name": "棉花糖", "img": "", "price": 99, "chance": "18.46%"},
        ],
        "updated_at": 0,
    }
}


def _make_data(seed: int = 7) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rnd = random.Random(seed)
    now = int(time.time())
    records: list[dict[str, Any]] = []
    draws: list[dict[str, Any]] = []
    rid = 900000
    weights = [10, 7, 5, 4, 3, 2, 2, 1, 1, 1]
    for day in range(360, -1, -1):
        if rnd.random() < 0.35:
            continue
        for _ in range(rnd.choice([1, 1, 2, 3, 5])):
            ruid, rname = rnd.choices(ANCHORS, weights)[0]
            ts = now - day * 86400 - rnd.randint(0, 86400) + (20 * 3600 if rnd.random() < 0.6 else 0)
            ts = min(ts, now - 60)
            kind = rnd.random()
            rid += 1
            base = {"id": rid, "timestamp": ts, "ruid": ruid, "r_uname": rname, "gift_img": "", "coin_type": "gold"}
            if kind < 0.12:
                n = rnd.choice([1, 1, 5, 10])
                records.append({**base, "gift_id": 32251, "gift_name": "心动盲盒", "gift_num": n,
                                "pay_coin": f"{150 * n:,}", "bag_desc": "", "status_msg": ""})
                pool = BOX["32251"]["gifts"]
                for _ in range(n):
                    g = rnd.choices(pool, [0.04, 0.5, 6, 40, 35, 18.46])[0]
                    draws.append({"gift_id": g["gift_id"], "gift_name": g["name"], "gift_num": 1, "gift_img": "",
                                  "timestamp": ts, "ruid": ruid, "rname": rname,
                                  "original_gift_id": 32251, "original_gift_name": "心动盲盒"})
            elif kind < 0.18 and day < 40:
                gid, name, price = rnd.choice(MATERIALS)
                n = rnd.choice([1, 5, 10])
                records.append({**base, "gift_id": gid, "gift_name": name, "gift_num": n,
                                "pay_coin": f"{price * n:,}", "bag_desc": "", "status_msg": ""})
                if rnd.random() < 0.55:
                    pid, pname, pprice = rnd.choices(PRODUCTS, [50, 30, 15, 2])[0]
                    rid += 1
                    records.append({**base, "id": rid, "timestamp": ts + 120, "gift_id": pid, "gift_name": pname,
                                    "gift_num": 1, "pay_coin": f"{pprice:,}", "bag_desc": analysis.BAG, "status_msg": ""})
            elif kind < 0.24:
                records.append({**base, "gift_id": 31036, "gift_name": "小花花", "gift_num": rnd.choice([1, 10, 30]),
                                "pay_coin": "0", "bag_desc": analysis.BAG, "status_msg": ""})
            else:
                gid, name, price = rnd.choices(GIFTS, [30, 20, 15, 10, 6, 1, 0.3, 4, 5, 6])[0]
                n = rnd.choice([1, 1, 1, 2, 5, 10])
                status = analysis.REFUNDED if rnd.random() < 0.01 else ""
                records.append({**base, "gift_id": gid, "gift_name": name, "gift_num": n,
                                "pay_coin": f"{price * n:,}", "bag_desc": "", "status_msg": status})
    records.sort(key=lambda r: r["timestamp"], reverse=True)
    draws.sort(key=lambda d: d["timestamp"], reverse=True)
    return records, {"32251": draws}


class DemoApp(App):
    demo = True
    MID = 1

    def __init__(self) -> None:
        super().__init__(Storage(Path(tempfile.mkdtemp(prefix="bili-local-demo-"))))
        records, draws = _make_data()
        self.storage.save_records(self.MID, records)
        self.storage.save_draws(self.MID, draws)
        self.storage.save_blind_boxes(BOX)
        self.storage.save_meta(self.MID, {"last_sync": int(time.time())})
        self._login()

    def _login(self) -> None:
        self.storage.save_session(
            {"cookies": {"SESSDATA": "demo"}, "mid": self.MID, "uname": "演示账号", "face": "", "saved_at": 0}
        )

    def qr_start(self) -> dict[str, Any]:
        from .service import qr_svg

        return {"key": "demo", "svg": qr_svg("演示模式：不需要扫码")}

    def qr_poll(self, key: str) -> dict[str, Any]:
        self._login()
        return {"code": 0, "message": "登录成功（演示）"}

    def logout(self, purge: bool) -> dict[str, Any]:
        self.storage.clear_session()
        return {"revoked": False}

    def start_sync(self) -> dict[str, Any]:
        self.sync.set(running=False, error="", text="演示模式：数据是随机生成的，没有真的同步", finished_at=int(time.time()))
        return {"started": False}

    def anchor_cards(self, ruids: list[Any], refresh: bool = False) -> dict[str, Any]:
        return {}  # 演示模式不联网，气泡里显示名字首字

    def medals(self) -> list[dict[str, Any]]:
        rnd = random.Random(3)
        out = []
        for i, (ruid, name) in enumerate(ANCHORS):
            lvl = max(1, 28 - i * 3 + rnd.randint(-2, 2))
            out.append({
                "medal_id": 5000 + i, "name": name[:2] + "团", "level": lvl, "intimacy": rnd.randint(100, 9000),
                "next_intimacy": 10000, "today_feed": rnd.choice([0, 0, 100, 500, 1500]), "day_limit": 1500,
                "lighted": rnd.random() < 0.7, "wearing": i == 0, "guard_level": 3 if i < 2 else 0,
                "anchor": name, "anchor_face": "", "ruid": ruid, "room_id": 20000 + i, "living": rnd.random() < 0.3,
            })
        return out

    def wallet(self) -> dict[str, Any]:
        return {"battery": 128, "silver": 2333, "bp": "5"}
