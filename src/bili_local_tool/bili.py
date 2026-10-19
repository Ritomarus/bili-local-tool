"""B站官方接口客户端。

所有请求只会发往 ALLOWED_HOSTS 里的 B站官方域名，其它域名在发出前就会被拒绝。
每一次请求（方法、域名、路径、状态码）都会记进内存里的 REQUEST_LOG，
可以在页面「安全」标签里实时查看，参数和 Cookie 不会被记录。
"""

from __future__ import annotations

import collections
import hashlib
import threading
import time
import urllib.parse
from typing import Any

import requests

ALLOWED_HOSTS = frozenset(
    {
        "passport.bilibili.com",
        "api.bilibili.com",
        "api.live.bilibili.com",
    }
)

WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
APP_UA = (
    "Mozilla/5.0 BiliDroid/8.87.0 (bbcallen@gmail.com) os/android model/PJD110 "
    "mobi_app/android build/8870400 channel/oppo innerVer/8870400 osVer/14 network/2"
)

# 安卓客户端的公开 appkey / appsec。消费记录（payRecord）是 App 接口，必须带签名。
APP_KEY = "1d8b6e7d45233436"
APP_SEC = "560c52ccd288fed045859ed18bffd973"

# 扫码状态码
QR_SUCCESS = 0
QR_EXPIRED = 86038
QR_SCANNED = 86090
QR_WAITING = 86101

REQUEST_LOG: collections.deque[dict[str, Any]] = collections.deque(maxlen=300)
_log_lock = threading.Lock()


class BiliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class NotLoggedIn(BiliError):
    pass


def _quote(value: str) -> str:
    # 与浏览器 encodeURIComponent 一致，保证签名串和实际发出的参数完全相同
    return urllib.parse.quote(value, safe="-_.!~*'()")


def sign_params(params: dict[str, str]) -> str:
    """App 接口签名：按 key 排序拼接后加 appsec 做 md5，返回完整查询串。"""
    query = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in sorted(params.items()))
    sign = hashlib.md5((query + APP_SEC).encode()).hexdigest()
    return f"{query}&sign={sign}"


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"拒绝请求非白名单地址: {parts.scheme}://{parts.hostname}")


def _log(method: str, url: str, status: int | str) -> None:
    parts = urllib.parse.urlsplit(url)
    with _log_lock:
        REQUEST_LOG.appendleft(
            {"ts": int(time.time()), "method": method, "host": parts.hostname, "path": parts.path, "status": status}
        )


class BiliClient:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.http = requests.Session()
        self.http.headers.update(
            {"User-Agent": WEB_UA, "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"}
        )
        if cookies:
            self.http.cookies.update(cookies)

    @property
    def cookies(self) -> dict[str, str]:
        return self.http.cookies.get_dict()

    @property
    def csrf(self) -> str:
        return self.cookies.get("bili_jct", "")

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        """发请求并返回完整 JSON。HTTP 412（风控）会退避重试。"""
        check_url(url)
        for attempt in range(retries + 1):
            try:
                resp = self.http.request(
                    method, url, params=params, data=data, json=json, headers=headers, timeout=15, allow_redirects=False
                )
            except requests.RequestException as e:
                _log(method, url, "error")
                if attempt == retries:
                    raise BiliError(-1, f"网络错误: {e}") from e
                time.sleep(1.5 * (attempt + 1))
                continue
            _log(method, url, resp.status_code)
            if resp.status_code == 412:
                if attempt == retries:
                    break
                time.sleep(10 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise BiliError(resp.status_code, f"HTTP {resp.status_code}")
            payload = resp.json()
            if payload.get("code") == -101:
                raise NotLoggedIn(-101, payload.get("message") or "账号未登录")
            return payload
        raise BiliError(412, "请求太频繁，被 B站风控拦截了，请过几分钟再试")

    def _data(self, method: str, url: str, **kw: Any) -> Any:
        payload = self._request(method, url, **kw)
        if payload.get("code") != 0:
            raise BiliError(payload.get("code", -1), payload.get("message") or payload.get("msg") or "接口返回错误")
        return payload.get("data")

    # ---------- 扫码登录 ----------

    def qr_generate(self) -> tuple[str, str]:
        data = self._data("GET", "https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        return data["url"], data["qrcode_key"]

    def qr_poll(self, key: str) -> tuple[int, str, str]:
        """返回 (状态码, 提示, refresh_token)。成功时登录 Cookie 已写进 self.http.cookies。"""
        data = self._data(
            "GET", "https://passport.bilibili.com/x/passport-login/web/qrcode/poll", params={"qrcode_key": key}
        )
        code = data.get("code", -1)
        if code == QR_SUCCESS and "SESSDATA" not in self.cookies:
            # 少数情况下 Cookie 只出现在跳转链接的参数里
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(data.get("url", "")).query)
            for name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
                if qs.get(name):
                    self.http.cookies.set(name, qs[name][0], domain=".bilibili.com")
        return code, data.get("message", ""), data.get("refresh_token", "")

    def ensure_buvid(self) -> None:
        """部分直播接口没有 buvid3 会触发风控，登录后补一次。"""
        if "buvid3" in self.cookies:
            return
        try:
            data = self._data("GET", "https://api.bilibili.com/x/frontend/finger/spi")
            self.http.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
            self.http.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except (BiliError, KeyError, TypeError):
            pass

    def logout(self) -> None:
        """调用 B站官方退出接口，让这份 Cookie 在服务端失效。"""
        self._request("POST", "https://passport.bilibili.com/login/exit/v2", data={"biliCSRF": self.csrf})

    # ---------- 账号 ----------

    def nav(self) -> dict[str, Any]:
        data = self._data("GET", "https://api.bilibili.com/x/web-interface/nav")
        if not data or not data.get("isLogin"):
            raise NotLoggedIn(-101, "账号未登录")
        return data

    def wallet(self) -> dict[str, Any]:
        return self._data(
            "GET",
            "https://api.live.bilibili.com/xlive/revenue/v1/wallet/myWallet",
            params={"need_bp": 1, "need_metal": 1, "platform": "pc"},
        )

    def medals_page(self, page: int) -> dict[str, Any]:
        return self._data(
            "GET",
            "https://api.live.bilibili.com/xlive/app-ucenter/v1/fansMedal/panel",
            params={"page": page, "page_size": 10},
        )

    # ---------- 消费记录 / 盲盒 ----------

    def pay_record_page(self, next_id: str | int | None = None) -> dict[str, Any]:
        """直播送礼消费记录（最多约一年），每页 50 条，用 next_id 翻页。"""
        params = {
            "actionKey": "appkey",
            "appkey": APP_KEY,
            "build": "8870400",
            "c_locale": "zh-Hans_CN",
            "channel": "oppo",
            "coin_type": "gold",
            "device": "android",
            "disable_rcmd": "0",
            "mobi_app": "android",
            "page_size": "50",
            "platform": "android",
            "s_locale": "zh-Hans_CN",
            "statistics": '{"appId":1,"platform":3,"version":"8.87.0","abtest":""}',
            "ts": str(int(time.time())),
            "version": "8.87.0",
        }
        if next_id:
            params["next_id"] = str(next_id)
        url = "https://api.live.bilibili.com/xlive/revenue/v2/giftStream/payRecord?" + sign_params(params)
        return self._data("GET", url, headers={"User-Agent": APP_UA})

    def gift_catalog(self) -> list[dict[str, Any]]:
        data = self._data(
            "GET", "https://api.live.bilibili.com/xlive/web-room/v1/giftPanel/giftConfig", params={"platform": "pc"}
        )
        return data.get("list") or []

    def blind_box_info(self, gift_id: int) -> dict[str, Any] | None:
        """盲盒的单价和奖池（价格单位：金瓜子，100 金瓜子 = 1 电池）。不是盲盒时返回 None。"""
        payload = self._request(
            "GET",
            "https://api.live.bilibili.com/xlive/general-interface/v1/blindFirstWin/getInfo",
            params={"gift_id": gift_id},
        )
        data = payload.get("data") or {}
        if payload.get("code") != 0 or not data.get("gifts"):
            return None
        return data

    def anchor_cards(self, uids: list[int]) -> dict[str, dict[str, Any]]:
        """批量查主播的昵称、头像、直播间号。没有直播间的用户不会出现在结果里。"""
        data = self._data(
            "POST", "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids", json={"uids": uids}
        )
        return data if isinstance(data, dict) else {}

    def blind_draw_page(self, gift_id: int, cursor: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"gift_id": gift_id, "page_size": 50}
        for k, v in (cursor or {}).items():
            if k != "have_more" and v is not None:
                params[k] = v
        return self._data(
            "GET", "https://api.live.bilibili.com/xlive/general-interface/v1/blind-box/drawStream", params=params
        )
