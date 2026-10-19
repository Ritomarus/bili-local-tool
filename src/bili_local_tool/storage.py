"""本地数据目录：登录凭证、消费记录、盲盒记录、配置，全部是本机上的 JSON 文件。

目录权限 700、文件权限 600，只有当前系统用户能读。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    # 合成活动：花在「材料」上的电池算支出，背包里合成出的「成品」送出去时的价值算收入
    "synthesis": [
        {
            "name": "玲珑宝斋",
            "materials": ["玲珑宝斋-锦囊", "玲珑宝斋-瓷瓶", "玲珑宝斋-银盒", "玲珑宝斋-云锦", "玲珑宝斋-玉函"],
            "products": ["相识玉扣", "常伴珠钗", "缘起瓷瓶", "倾心宝冠", "万象天衣"],
            "start": "",
            "end": "",
        }
    ],
    # 自动识别不到的盲盒，可以在这里手动补礼物 ID
    "extra_blind_box_ids": [],
}


def default_data_dir() -> Path:
    """默认放在系统的应用数据目录，而不是项目目录。

    项目如果放在「文稿」「桌面」里，macOS 开了 iCloud 同步时会把登录凭证一起传到 iCloud；
    Application Support 不会被同步。
    """
    env = os.environ.get("BILI_LOCAL_DATA")
    if env:
        return Path(env).expanduser().resolve()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BiliLocal"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "BiliLocal"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "bili-local"


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def read(self, *parts: str, default: Any = None) -> Any:
        path = self._path(*parts)
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write(self, obj: Any, *parts: str) -> None:
        """原子写入：先写临时文件再替换，避免写一半崩溃导致数据损坏。"""
        path = self._path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise

    # ---------- 登录凭证 ----------

    def load_session(self) -> dict[str, Any] | None:
        s = self.read("session.json")
        if isinstance(s, dict) and s.get("cookies", {}).get("SESSDATA"):
            return s
        return None

    def save_session(self, session: dict[str, Any]) -> None:
        self.write(session, "session.json")

    def clear_session(self) -> None:
        self._path("session.json").unlink(missing_ok=True)

    # ---------- 按账号存放的数据 ----------

    def user_dir(self, mid: int) -> str:
        return f"uid_{int(mid)}"

    def load_records(self, mid: int) -> list[dict[str, Any]]:
        return self.read(self.user_dir(mid), "pay_records.json", default=[])

    def save_records(self, mid: int, records: list[dict[str, Any]]) -> None:
        self.write(records, self.user_dir(mid), "pay_records.json")

    def load_draws(self, mid: int) -> dict[str, list[dict[str, Any]]]:
        """{盲盒礼物ID: [开盒记录...]}"""
        return self.read(self.user_dir(mid), "blind_draws.json", default={})

    def save_draws(self, mid: int, draws: dict[str, list[dict[str, Any]]]) -> None:
        self.write(draws, self.user_dir(mid), "blind_draws.json")

    def load_meta(self, mid: int) -> dict[str, Any]:
        return self.read(self.user_dir(mid), "meta.json", default={})

    def save_meta(self, mid: int, meta: dict[str, Any]) -> None:
        self.write(meta, self.user_dir(mid), "meta.json")

    def purge_user(self, mid: int) -> None:
        shutil.rmtree(self._path(self.user_dir(mid)), ignore_errors=True)

    # ---------- 全局 ----------

    def load_blind_boxes(self) -> dict[str, Any]:
        """{盲盒礼物ID: 奖池信息}，各账号共用。"""
        return self.read("blind_boxes.json", default={})

    def save_blind_boxes(self, boxes: dict[str, Any]) -> None:
        self.write(boxes, "blind_boxes.json")

    def load_anchor_cards(self) -> dict[str, Any]:
        """{主播UID: {name, face, room_id, updated_at}}，头像缓存，各账号共用。"""
        return self.read("anchor_cards.json", default={})

    def save_anchor_cards(self, cards: dict[str, Any]) -> None:
        self.write(cards, "anchor_cards.json")

    def load_config(self) -> dict[str, Any]:
        cfg = self.read("config.json", default={})
        return {**DEFAULT_CONFIG, **(cfg if isinstance(cfg, dict) else {})}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.write(cfg, "config.json")
