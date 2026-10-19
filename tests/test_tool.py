import datetime as dt
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from bili_local_tool import analysis, bili, service
from bili_local_tool.demo import DemoApp
from bili_local_tool.server import serve
from bili_local_tool.storage import Storage

TZ = analysis.TZ


def ts(y, m, d, h=12):
    return int(dt.datetime(y, m, d, h, tzinfo=TZ).timestamp())


def rec(id, t, coin, name="小花花", gid=1, num=1, ruid=1, uname="A", bag=False, refunded=False):
    return {
        "id": id, "timestamp": t, "pay_coin": coin, "gift_name": name, "gift_id": gid, "gift_num": num,
        "ruid": ruid, "r_uname": uname, "bag_desc": analysis.BAG if bag else "",
        "status_msg": analysis.REFUNDED if refunded else "",
    }


class AnalysisTest(unittest.TestCase):
    def test_coin_parses_thousands(self):
        self.assertEqual(analysis.coin({"pay_coin": "1,520"}), 1520)
        self.assertEqual(analysis.coin({"coin": "30"}), 30)
        self.assertEqual(analysis.coin({}), 0)

    def test_ranges(self):
        now = dt.datetime(2026, 3, 15, 10, tzinfo=TZ)
        r = analysis.resolve_range("last_month", now=now)
        self.assertTrue(r.contains(ts(2026, 2, 1, 0)))
        self.assertTrue(r.contains(ts(2026, 2, 28, 23)))
        self.assertFalse(r.contains(ts(2026, 3, 1, 0)))
        c = analysis.resolve_range("custom", "2026-01-10", "2026-01-10", now=now)
        self.assertTrue(c.contains(ts(2026, 1, 10, 23)))
        self.assertFalse(c.contains(ts(2026, 1, 11, 0)))
        self.assertEqual(analysis.resolve_range("custom", "bad"), analysis.Range())

    def test_overview_excludes_bag_and_refunded(self):
        records = [
            rec(1, ts(2026, 1, 5), "100", uname="A"),
            rec(2, ts(2026, 1, 5, 20), "1,000", name="告白气球", gid=2, ruid=2, uname="B"),
            rec(3, ts(2026, 3, 1), "50", bag=True),
            rec(4, ts(2026, 3, 2), "999", refunded=True),
        ]
        o = analysis.overview(records, analysis.Range())
        self.assertEqual(o["totals"]["spent"], 1100)
        self.assertEqual(o["totals"]["bag_value"], 50)
        self.assertEqual(o["totals"]["count"], 2)
        self.assertEqual(o["totals"]["refunded"], 1)
        self.assertEqual([m["month"] for m in o["by_month"]], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(o["by_month"][1]["spent"], 0)
        self.assertEqual(o["top_anchors"][0]["name"], "B")
        self.assertEqual(o["by_hour"][20], 1000)
        self.assertEqual(o["totals"]["biggest_day"], {"date": "2026-01-05", "spent": 1100})
        json.dumps(o)  # 必须能直接序列化

    def test_anchor_bubbles(self):
        records = [
            {**rec(1, ts(2026, 1, 1), "100", ruid=7, uname="A"), "room_id": 70},
            rec(2, ts(2026, 1, 2), "30", ruid=7, uname="A", bag=True),
            rec(3, ts(2026, 1, 3), "500", ruid=8, uname="B"),
            rec(4, ts(2026, 1, 4), "999", ruid=9, uname="C", refunded=True),
        ]
        by_value = analysis.anchor_bubbles(records, analysis.Range())
        self.assertEqual([(a["ruid"], a["value"]) for a in by_value], [(8, 500), (7, 130)])
        self.assertEqual(by_value[1]["room_id"], 70)
        by_count = analysis.anchor_bubbles(records, analysis.Range(), "count")
        self.assertEqual([(a["ruid"], a["value"]) for a in by_count], [(7, 2), (8, 1)])

    def test_anchor_cards_cache(self):
        with tempfile.TemporaryDirectory() as d:
            app = service.App(Storage(Path(d)))
            fake = {"7": {"uname": "A", "face": "http://i0.hdslb.com/bfs/face/a.jpg", "room_id": 70}}
            with mock.patch.object(bili.BiliClient, "anchor_cards", return_value=fake) as call, \
                    mock.patch.object(service.time, "sleep"):
                cards = app.anchor_cards([7, 8, 7])
                self.assertEqual(call.call_args[0][0], [7, 8])
                self.assertEqual(cards["7"]["face"], "https://i0.hdslb.com/bfs/face/a.jpg")
                self.assertEqual(cards["8"]["face"], "")  # 查不到也缓存，下次不再请求
                app.anchor_cards([7, 8])
                self.assertEqual(call.call_count, 1)

    def test_records_page_filters(self):
        records = [rec(i, ts(2026, 1, 1) + i, "10", ruid=i % 2, uname=f"U{i % 2}") for i in range(120)]
        p = analysis.records_page(records, analysis.Range(), ruid="1", page=2)
        self.assertEqual(p["total"], 60)
        self.assertEqual(p["pages"], 2)
        self.assertEqual(len(p["items"]), 10)
        self.assertEqual(p["sum"], 600)
        self.assertEqual(analysis.records_page(records, analysis.Range(), q="u0")["total"], 60)

    def test_blindbox(self):
        boxes = {"9": {"name": "心动盲盒", "blind_price": 150, "gifts": [
            {"gift_id": 1, "name": "大", "price": 1000, "chance": "1%"},
            {"gift_id": 2, "name": "小", "price": 100, "chance": "99%"},
        ]}}
        draws = {"9": [
            {"gift_id": 1, "gift_num": 1, "timestamp": ts(2026, 1, 1), "ruid": 5, "rname": "A"},
            {"gift_id": 2, "gift_num": 3, "timestamp": ts(2026, 2, 1), "ruid": 5, "rname": "A"},
            {"gift_id": 77, "gift_num": 1, "timestamp": ts(2026, 2, 2), "ruid": 6, "rname": "B"},
        ]}
        r = analysis.blindbox_report(boxes, draws, analysis.Range())
        b = r["boxes"][0]
        self.assertEqual(b["draws"], 5)
        self.assertEqual(b["spent"], 750)
        self.assertEqual(b["value"], 1300)
        self.assertEqual(b["profit"], 550)
        self.assertEqual(b["unknown"], 1)
        self.assertEqual([m["month"] for m in b["months"]], ["2026-01", "2026-02"])
        self.assertEqual(r["totals"]["profit"], 550)

    def test_blindbox_from_records_without_draws(self):
        # B站开盒记录接口查不到较早的盲盒：仍然要显示购买数和花费，盈亏为空
        boxes = {"35206": {"name": "幸运盲盒", "blind_price": 50, "gifts": [{"gift_id": 1, "price": 52}]},
                 "35578": {"not_blind_box": True}}
        records = [
            rec(1, ts(2026, 3, 30), "50", name="幸运盲盒", gid=35206, uname="S"),
            rec(2, ts(2026, 5, 30), "90", name="噜噜盲盒", gid=35578, uname="S"),
            rec(3, ts(2026, 5, 30), "52", name="情书", gid=35545),
        ]
        r = analysis.blindbox_report(boxes, {}, analysis.Range(), records)
        by_name = {b["name"]: b for b in r["boxes"]}
        self.assertEqual(set(by_name), {"幸运盲盒", "噜噜盲盒"})
        self.assertEqual((by_name["幸运盲盒"]["bought"], by_name["幸运盲盒"]["spent"]), (1, 50))
        self.assertEqual(by_name["噜噜盲盒"]["blind_price"], 90)  # 没有奖池时按实际花费推单价
        self.assertIsNone(by_name["噜噜盲盒"]["profit"])
        self.assertEqual(r["totals"]["spent"], 140)
        self.assertEqual(r["totals"]["missing"], 2)
        self.assertIsNone(r["totals"]["profit"])
        json.dumps(r)

    def test_synthesis(self):
        records = [
            rec(1, ts(2026, 5, 1), "100", name="玲珑宝斋-锦囊", num=10),
            rec(2, ts(2026, 5, 1), "300", name="缘起瓷瓶", bag=True),
            rec(3, ts(2026, 5, 2), "100", name="玲珑宝斋-锦囊", refunded=True),
            rec(4, ts(2026, 5, 9), "50", name="缘起瓷瓶", bag=True),  # 超过结束时间 + 49 小时
        ]
        acts = [{"name": "x", "materials": ["玲珑宝斋-锦囊"], "products": ["缘起瓷瓶"], "start": "2026-05-01", "end": "2026-05-05"}]
        a = analysis.synthesis_report(records, acts)[0]
        self.assertEqual((a["spent"], a["earned"], a["profit"]), (100, 300, 200))
        self.assertEqual(a["materials_num"], 10)


class BiliClientTest(unittest.TestCase):
    def test_sign_matches_md5_of_sorted_query(self):
        q = bili.sign_params({"b": "2", "a": "x y", "statistics": '{"k":1}'})
        base, sign = q.rsplit("&sign=", 1)
        self.assertEqual(base, "a=x%20y&b=2&statistics=%7B%22k%22%3A1%7D")
        self.assertEqual(sign, hashlib.md5((base + bili.APP_SEC).encode()).hexdigest())

    def test_allowlist(self):
        bili.check_url("https://api.live.bilibili.com/x")
        for bad in ("https://example.com/api", "http://api.bilibili.com/x", "https://evil.com/?h=api.bilibili.com",
                    "https://api.bilibili.com.evil.com/"):
            with self.assertRaises(ValueError):
                bili.check_url(bad)
        with mock.patch.object(bili.requests.Session, "request") as req:
            with self.assertRaises(ValueError):
                bili.BiliClient()._request("GET", "https://example.com/")
            req.assert_not_called()


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def pay_record_page(self, next_id=None):
        self.calls += 1
        return self.pages[int(next_id or 0)]


class SyncTest(unittest.TestCase):
    def test_incremental_stops_after_recheck_window(self):
        now = ts(2026, 6, 30)
        existing = [rec(10, now - 3 * 86400, "1"), rec(9, now - 30 * 86400, "1")]
        pages = [
            {"list": [rec(12, now, "5"), rec(11, now - 86400, "5")], "params": {"next_id": 1}},
            {"list": [rec(10, now - 3 * 86400, "1", refunded=True), rec(8, now - 20 * 86400, "1")], "params": {"next_id": 2}},
            {"list": [rec(7, now - 40 * 86400, "1")], "params": {}},
        ]
        c = FakeClient(pages)
        with mock.patch.object(service.time, "sleep"):
            merged, added = service.sync_pay_records(c, existing, lambda t: None)
        self.assertEqual(added, 2)
        self.assertEqual(c.calls, 2)  # 第二页碰到一周前的记录就停
        self.assertEqual([r["id"] for r in merged], [12, 11, 10, 9])
        self.assertTrue(analysis.is_refunded(merged[2]))  # 已有记录被更新成最新状态


class StorageTest(unittest.TestCase):
    def test_default_dir_is_outside_project(self):
        from bili_local_tool import storage

        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BILI_LOCAL_DATA", None)
            d = storage.default_data_dir()
        self.assertNotIn(str(Path(__file__).resolve().parents[1]), str(d))
        with mock.patch.dict("os.environ", {"BILI_LOCAL_DATA": "/tmp/x"}):
            self.assertEqual(storage.default_data_dir(), Path("/tmp/x").resolve())

    def test_permissions_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = Storage(Path(d) / "data")
            s.save_session({"cookies": {"SESSDATA": "x"}, "mid": 1})
            p = Path(d) / "data" / "session.json"
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual((Path(d) / "data").stat().st_mode & 0o777, 0o700)
            self.assertEqual(s.load_session()["mid"], 1)
            s.clear_session()
            self.assertIsNone(s.load_session())


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = DemoApp()
        cls.httpd, url = serve(cls.app, 18765)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        html = cls.get("/")[2].decode()
        cls.token = html.split('name="local-token" content="')[1].split('"')[0]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    @classmethod
    def get(cls, path, headers=None, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        h = {"Host": f"127.0.0.1:{cls.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def api(self, path, **kw):
        status, headers, body = self.get(path, {"X-Bili-Local-Token": self.token, **kw.pop("headers", {})}, **kw)
        return status, json.loads(body)

    def test_index_has_csp_and_token(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn(b"__TOKEN__", body)

    def test_rejects_missing_token_and_bad_host(self):
        self.assertEqual(self.get("/api/status")[0], 403)
        self.assertEqual(self.get("/api/status", {"X-Bili-Local-Token": "nope"})[0], 403)
        self.assertEqual(self.get("/", {"Host": "evil.example:80"})[0], 421)
        self.assertEqual(self.get("/../pyproject.toml")[0], 404)
        status, headers, _ = self.get("/api/status", {"Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_api_endpoints(self):
        status, st = self.api("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(st["logged_in"] and st["demo"])
        for path in ("/api/overview?range=all", "/api/overview?range=30d", "/api/records?page=2&kind=paid",
                     "/api/blindbox", "/api/bubbles?metric=count", "/api/synthesis", "/api/medals", "/api/wallet", "/api/settings", "/api/netlog"):
            status, data = self.api(path)
            self.assertEqual(status, 200, path)
        status, o = self.api("/api/overview")
        self.assertGreater(o["totals"]["spent"], 0)
        status, b = self.api("/api/blindbox")
        self.assertEqual(b["boxes"][0]["name"], "心动盲盒")

    def test_export_csv(self):
        status, headers, body = self.get("/api/export?format=csv", {"X-Bili-Local-Token": self.token})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("﻿时间".encode()))
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_bad_config_rejected(self):
        status, data = self.api("/api/synthesis", method="PUT", body=b'{"activities": "x"}',
                                headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
