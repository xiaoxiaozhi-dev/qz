"""
layout_state — 持久化中台 UI 状态（中台 2.0）。

存储：data/layout.json（旧 ~/.qz-tool/layout.json 由 paths 一次性迁移）

v4 schema（2026-10-18，Max 接入时确立）：
{
  "version": 4,
  "open_plugins": [
    {"dcc": "maya", "id": "bone_mirror", "x": 200, "y": 150},
    {"dcc": "max",  "id": "create_box",  "x": 560, "y": 320}
  ],
  "locked": false,
  "log_height": 70,
  "always_on_top": false,
  "sidebar": {"visible": false, "page": 0},
  "window": {"x": 120, "y": 80, "w": 1440, "h": 900, "maximized": false}
}

★ 为什么 open_plugins 要带 dcc ★
maya 和 max 各自都有 create_box / move_selected，光靠 id 分不清是哪个节点。
内部唯一键统一用 (dcc, id) 复合键，这里存的就是它。

★ 兼容 ★
v3（无 dcc 字段）→ 全部按 "maya" 处理，用户现有的开窗记录无缝迁移，不用手改。
v2（无 version）→ 仍按 v3 归一化后再补 dcc。
v1（per_dcc）→ 同样归一化。

窗口位置做屏幕校验：显示器被拔掉 / 分辨率变化时，把窗口拉回可见区域，
避免窗口跑到屏幕外找不到。
"""

import os
import json
import math
import paths
from dcc_bridge import DCC_PORTS
from typing import List, Tuple, Optional, Dict


# ── 默认值 ─────────────────────────────────────
DEFAULT_LOG_HEIGHT = 70
DEFAULT_WINDOW = {"x": 100, "y": 80, "w": 1400, "h": 880}
MIN_WINDOW_W = 1024
MIN_WINDOW_H = 640


def _default_path() -> str:
    return paths.user_state_path('layout.json')


def _default_state() -> dict:
    return {
        "version": 4,
        "open_plugins": [],
        "locked": False,
        "log_height": DEFAULT_LOG_HEIGHT,
        "log_max_lines": 1000,
        "always_on_top": False,
        "collapsed_groups": [],
        "canvas_view": None,
        "sidebar": {"visible": False, "page": 0},
        "window": dict(DEFAULT_WINDOW, maximized=False),
    }


def _normalize_window(raw) -> dict:
    """清洗 window 字段，任何异常都退回默认值。"""
    win = dict(DEFAULT_WINDOW, maximized=False)
    if not isinstance(raw, dict):
        return win
    try:
        win["x"] = int(raw.get("x", DEFAULT_WINDOW["x"]))
        win["y"] = int(raw.get("y", DEFAULT_WINDOW["y"]))
        win["w"] = max(MIN_WINDOW_W, int(raw.get("w", DEFAULT_WINDOW["w"])))
        win["h"] = max(MIN_WINDOW_H, int(raw.get("h", DEFAULT_WINDOW["h"])))
        win["maximized"] = bool(raw.get("maximized", False))
    except (TypeError, ValueError):
        return dict(DEFAULT_WINDOW, maximized=False)
    return win


def _normalize_sidebar(raw):
    if not isinstance(raw, dict):
        return {"visible": False, "page": 0}
    page = raw.get('page')
    return {"visible": raw.get('visible') is True,
            "page": page if type(page) is int and page in (0, 1) else 0}


def _normalize_collapsed(raw):
    result = set()
    if isinstance(raw, (list, tuple)):
        for key in raw:
            if not isinstance(key, (list, tuple)):
                continue
            if len(key) == 2 and key[0] == 'dcc' and key[1] in DCC_PORTS:
                result.add(tuple(key))
            elif (len(key) == 3 and key[0] == 'cat' and key[1] in DCC_PORTS
                  and isinstance(key[2], str) and key[2]):
                result.add(tuple(key))
    return [list(key) for key in sorted(result)]


def _normalize_canvas_view(raw):
    if not isinstance(raw, dict):
        return None
    try:
        center = raw['center']
        if not isinstance(center, dict):
            return None
        values = (center['x'], center['y'], raw['scale'])
        if any(isinstance(value, bool) for value in values):
            return None
        x, y, scale = map(float, values)
        if (not all(math.isfinite(value) for value in (x, y, scale))
                or abs(x) > 1e8 or abs(y) > 1e8 or not 1e-4 <= scale <= 1e4):
            return None
        return dict(center=dict(x=x, y=y), scale=scale)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _normalize(data: dict) -> dict:
    """补齐字段 + 兼容旧格式（v1 per_dcc / v2 无 window）。"""
    if not isinstance(data, dict):
        return _default_state()

    # v1：完全没有 version 字段，带 per_dcc
    if "version" not in data:
        legacy_open = []
        try:
            pd = data.get("per_dcc", {})
            for dcc in ("maya", "max"):
                for pid in pd.get(dcc, {}).get("open_plugins", []) or []:
                    # v1 本来就带 dcc，直接用
                    legacy_open.append({"dcc": dcc, "id": pid,
                                        "x": 0, "y": 0})
        except Exception:
            pass
        out = _default_state()
        out["open_plugins"] = legacy_open
        return out

    # open_plugins 清洗
    # ★ dcc 缺省补 "maya" —— v3 记录全是 Maya 插件，这样无缝迁移 ★
    cleaned = []
    for p in data.get("open_plugins", []) or []:
        if not isinstance(p, dict) or "id" not in p:
            continue
        try:
            cleaned.append({
                "dcc": str(p.get("dcc", "maya")),
                "id": str(p["id"]),
                "x": float(p.get("x", 0)),
                "y": float(p.get("y", 0)),
            })
        except (TypeError, ValueError):
            continue

    # log_height
    try:
        log_h = int(data.get("log_height", DEFAULT_LOG_HEIGHT))
    except (TypeError, ValueError):
        log_h = DEFAULT_LOG_HEIGHT
    log_h = max(0, min(log_h, 1000))
    try:
        log_limit = max(100, min(int(data.get('log_max_lines', 1000)), 100000))
    except (TypeError, ValueError):
        log_limit = 1000

    return {
        "version": 4,
        "open_plugins": cleaned,
        "locked": bool(data.get("locked", False)),
        "log_height": log_h,
        "log_max_lines": log_limit,
        "always_on_top": data.get('always_on_top') is True,
        "collapsed_groups": _normalize_collapsed(data.get('collapsed_groups')),
        "canvas_view": _normalize_canvas_view(data.get('canvas_view')),
        "sidebar": _normalize_sidebar(data.get('sidebar')),
        "window": _normalize_window(data.get("window")),
    }


def load_all() -> dict:
    path = _default_path()
    if not os.path.isfile(path):
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _default_state()
    return _normalize(data)


def save_all(state: dict):
    path = _default_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_normalize(state), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[layout_state] save failed: {e}")


# ── 对外 API ───────────────────────────────────
def load_open():
    """return ([((dcc, id), x, y)...], locked)

    key 是 (dcc, id) 复合键 —— 与 PluginInfo.key 对应，
    所以 maya 的 create_box 和 max 的 create_box 都能各自还原。
    """
    d = load_all()
    items = [((p["dcc"], p["id"]), p["x"], p["y"])
             for p in d["open_plugins"]]
    return items, bool(d["locked"])


def save_open(items, locked: bool,
              log_height: Optional[int] = None, window: Optional[Dict] = None,
              collapsed_groups=None, canvas_view=None, sidebar=None):
    """保存布局。items 为 [((dcc, id), x, y), ...]。

    log_height / window / collapsed_groups / canvas_view / sidebar 为 None 时保留已有值。
    """
    cur = load_all()
    plugins = []
    for key, x, y in items:
        dcc, pid = key
        plugins.append({"dcc": dcc, "id": pid, "x": x, "y": y})
    cur.update({
        "version": 4,
        "open_plugins": plugins,
        "locked": bool(locked),
        "log_height": cur["log_height"] if log_height is None else int(log_height),
        "log_max_lines": cur['log_max_lines'],
        "always_on_top": cur['always_on_top'],
        "window": cur["window"] if window is None else window,
    })
    if collapsed_groups is not None:
        cur['collapsed_groups'] = collapsed_groups
    if canvas_view is not None:
        cur['canvas_view'] = canvas_view
    if sidebar is not None:
        cur['sidebar'] = sidebar
    save_all(cur)


def load_collapsed_groups():
    return load_all()['collapsed_groups']


def load_canvas_view():
    return load_all()['canvas_view']


def load_sidebar():
    return load_all()['sidebar']


def load_window() -> Dict:
    """return {x, y, w, h, maximized}"""
    return load_all()["window"]


def forget_plugin(key):
    """Remove a deleted tool's saved card entry while preserving other UI state."""
    cur = load_all()
    dcc, plugin_id = key
    cur['open_plugins'] = [item for item in cur['open_plugins']
                           if (item['dcc'], item['id']) != (dcc, plugin_id)]
    save_all(cur)


def save_window(geometry: Dict):
    """单独保存窗口几何（closeEvent 用）。"""
    cur = load_all()
    cur["window"] = geometry
    save_all(cur)


def load_always_on_top() -> bool:
    return load_all()['always_on_top']


def save_always_on_top(enabled: bool):
    cur = load_all()
    cur['always_on_top'] = bool(enabled)
    save_all(cur)


def load_log_height() -> int:
    return load_all()["log_height"]


def load_log_limit() -> int:
    return load_all()['log_max_lines']


def save_log_limit(limit: int):
    cur = load_all()
    cur['log_max_lines'] = limit
    save_all(cur)


def save_log_height(h: int):
    cur = load_all()
    cur["log_height"] = int(h)
    save_all(cur)
