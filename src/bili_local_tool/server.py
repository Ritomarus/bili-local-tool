"""本机网页服务，只监听 127.0.0.1。

防护措施：
- Host 头必须是 127.0.0.1 / localhost（防 DNS rebinding）
- 所有 /api 请求必须带每次启动随机生成的令牌（防止别的网站偷偷调用本机接口）
- 不返回任何 CORS 头；HTML 带严格的 CSP
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import secrets
import socketserver
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Callable

from . import analysis
from .bili import ALLOWED_HOSTS, REQUEST_LOG, BiliError, NotLoggedIn
from .service import App

TOKEN_HEADER = "X-Bili-Local-Token"
CSP = (
    "default-src 'self'; img-src 'self' data: https://*.hdslb.com; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def make_handler(app: App, port: int, token: str) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    static = resources.files("bili_local_tool") / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "BiliLocal"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # 不在终端刷请求日志
            pass

        # ---------- 入口 ----------

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def _dispatch(self, method: str) -> None:
            if self.headers.get("Host") not in allowed_hosts:
                self._send(HTTPStatus.MISDIRECTED_REQUEST, b"bad host", "text/plain")
                return
            url = urllib.parse.urlsplit(self.path)
            self.query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            path = url.path
            if not path.startswith("/api/"):
                if method == "GET":
                    self._static(path)
                else:
                    self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")
                return

            given = self.headers.get(TOKEN_HEADER) or ""
            if not secrets.compare_digest(given.encode(), token.encode()):
                self._json({"error": "令牌无效，请刷新页面"}, HTTPStatus.FORBIDDEN)
                return
            route = ROUTES.get((method, path))
            if not route:
                self._json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
                return
            try:
                result = route(self)
                if result is not None:
                    self._json(result)
            except NotLoggedIn as e:
                self._json({"error": e.message, "need_login": True}, HTTPStatus.UNAUTHORIZED)
            except BiliError as e:
                self._json({"error": f"B站接口错误：{e.message}"}, HTTPStatus.BAD_GATEWAY)
            except ApiError as e:
                self._json({"error": e.message}, e.status)
            except Exception as e:  # 兜底，避免连接直接断掉
                self._json({"error": f"本地服务出错：{e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        # ---------- 输出 ----------

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, status: int = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            if "/" in name or name.startswith("."):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            f = static / name
            if not f.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            body = f.read_bytes()
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            extra = {}
            if name == "index.html":
                body = body.replace(b"__TOKEN__", token.encode())
                extra = {"Content-Security-Policy": CSP, "X-Frame-Options": "DENY"}
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, body, ctype, extra)

        def body_json(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求太大")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 格式错误") from None
            if not isinstance(data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 格式错误")
            return data

        def rng(self) -> analysis.Range:
            q = self.query
            return analysis.resolve_range(q.get("range", "all"), q.get("start", ""), q.get("end", ""))

        def download(self, body: bytes, filename: str, ctype: str) -> None:
            quoted = urllib.parse.quote(filename)
            self._send(HTTPStatus.OK, body, ctype, {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})

    # ---------- 路由 ----------

    def auth_status(h: Handler) -> Any:
        return {**app.status(), "sync": app.sync.snapshot()}

    def qr_start(h: Handler) -> Any:
        return app.qr_start()

    def qr_poll(h: Handler) -> Any:
        return app.qr_poll(h.query.get("key", ""))

    def logout(h: Handler) -> Any:
        return app.logout(bool(h.body_json().get("purge")))

    def sync_start(h: Handler) -> Any:
        return app.start_sync()

    def overview(h: Handler) -> Any:
        return analysis.overview(app.records(), h.rng())

    def bubbles(h: Handler) -> Any:
        return app.bubbles(h.rng(), "count" if h.query.get("metric") == "count" else "spent")

    def records(h: Handler) -> Any:
        q = h.query
        try:
            page = int(q.get("page", "1"))
        except ValueError:
            page = 1
        return analysis.records_page(
            app.records(), h.rng(), q=q.get("q", ""), ruid=q.get("ruid", ""), kind=q.get("kind", "all"), page=page
        )

    def export(h: Handler) -> None:
        items = analysis.records_page(app.records(), h.rng(), q=h.query.get("q", ""), ruid=h.query.get("ruid", ""),
                                      kind=h.query.get("kind", "all"), size=10**9)["items"]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if h.query.get("format") == "json":
            h.download(json.dumps(items, ensure_ascii=False, indent=1).encode(), f"bili-消费记录-{stamp}.json",
                       "application/json")
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["时间", "主播", "主播UID", "礼物", "礼物ID", "数量", "电池", "包裹礼物", "已退回"])
        for r in items:
            w.writerow([r["time"], r["anchor"], r["ruid"], r["gift_name"], r["gift_id"], r["gift_num"], r["coin"],
                        "是" if r["bag"] else "", "是" if r["refunded"] else ""])
        h.download(("﻿" + buf.getvalue()).encode(), f"bili-消费记录-{stamp}.csv", "text/csv; charset=utf-8")

    def blindbox(h: Handler) -> Any:
        return app.blindbox(h.rng())

    def synthesis(h: Handler) -> Any:
        return app.synthesis()

    def synthesis_save(h: Handler) -> Any:
        acts = h.body_json().get("activities")
        if not isinstance(acts, list) or len(acts) > 50:
            raise ApiError(HTTPStatus.BAD_REQUEST, "活动配置格式错误")
        clean = []
        for a in acts:
            if not isinstance(a, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "活动配置格式错误")
            clean.append({
                "name": str(a.get("name", ""))[:50],
                "materials": [str(x).strip()[:50] for x in a.get("materials", []) if str(x).strip()][:50],
                "products": [str(x).strip()[:50] for x in a.get("products", []) if str(x).strip()][:50],
                "start": str(a.get("start", ""))[:10],
                "end": str(a.get("end", ""))[:10],
            })
        cfg = app.storage.load_config()
        cfg["synthesis"] = clean
        app.storage.save_config(cfg)
        return app.synthesis()

    def settings(h: Handler) -> Any:
        cfg = app.storage.load_config()
        return {
            "data_dir": str(app.storage.root),
            "allowed_hosts": sorted(ALLOWED_HOSTS),
            "extra_blind_box_ids": cfg.get("extra_blind_box_ids", []),
            "demo": app.demo,
        }

    def settings_save(h: Handler) -> Any:
        ids = h.body_json().get("extra_blind_box_ids", [])
        try:
            ids = sorted({int(x) for x in ids})[:50]
        except (TypeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "盲盒 ID 必须是数字") from None
        cfg = app.storage.load_config()
        cfg["extra_blind_box_ids"] = ids
        app.storage.save_config(cfg)
        return settings(h)

    def netlog(h: Handler) -> Any:
        return list(REQUEST_LOG)

    def medals(h: Handler) -> Any:
        return app.medals()

    def wallet(h: Handler) -> Any:
        return app.wallet()

    ROUTES: dict[tuple[str, str], Callable[[Handler], Any]] = {
        ("GET", "/api/status"): auth_status,
        ("POST", "/api/qr"): qr_start,
        ("GET", "/api/qr/poll"): qr_poll,
        ("POST", "/api/logout"): logout,
        ("POST", "/api/sync"): sync_start,
        ("GET", "/api/overview"): overview,
        ("GET", "/api/records"): records,
        ("GET", "/api/bubbles"): bubbles,
        ("GET", "/api/export"): export,
        ("GET", "/api/blindbox"): blindbox,
        ("GET", "/api/synthesis"): synthesis,
        ("PUT", "/api/synthesis"): synthesis_save,
        ("GET", "/api/settings"): settings,
        ("PUT", "/api/settings"): settings_save,
        ("GET", "/api/netlog"): netlog,
        ("GET", "/api/medals"): medals,
        ("GET", "/api/wallet"): wallet,
    }
    return Handler


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # 跳过 HTTPServer 默认的反向 DNS 查询（getfqdn），在 macOS 上能卡好几秒
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def serve(app: App, port: int) -> tuple[ThreadingHTTPServer, str]:
    """尝试从 port 开始找一个空闲端口。返回 (server, 访问地址)。"""
    token = secrets.token_urlsafe(24)
    last: OSError | None = None
    for p in range(port, port + 20):
        try:
            httpd = LocalServer(("127.0.0.1", p), make_handler(app, p, token))
        except OSError as e:
            last = e
            continue
        return httpd, f"http://127.0.0.1:{p}/"
    raise SystemExit(f"找不到可用端口（从 {port} 开始）：{last}")
