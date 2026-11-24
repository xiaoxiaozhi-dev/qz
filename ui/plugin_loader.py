"""
PluginLoader — 扫描 plugins/<dcc>/<plugin>/ 目录，读取 manifest.json，加载 widget 工厂。

目录结构（2026-10-18 起，Max 接入时确立）：
    plugins/
    ├─ maya/
    │  ├─ create_box/     manifest.json + widget.py
    │  └─ ...
    └─ max/
       ├─ create_box/     manifest.json + widget.py
       └─ ...

manifest 字段：
  - id              必填。**裸名，不带 DCC 前缀**（如 "create_box"）
  - name            必填
  - dcc             必填，必须与所在目录名一致（不一致则跳过该插件）
  - icon            相对插件目录的 svg/png
  - widget_factory  必填，格式 "module.attr" 或 "module.py:attr"
  - shortcut        字符串
  - category        可选，资源树里的分组。空 = 平铺在 DCC 分组下；
                    "settings" = 归到「设置」组（调试/设置类功能）

★ 为什么用 (dcc, id) 复合键而不是 "max_create_box" 前缀 ★
maya 和 max 各自都有 create_box / move_selected，id 会重名。
前缀是第三份重复信息（目录、dcc 字段已经各说了一遍），容易与前两者不一致。
复合键把「归属」这一事实集中在内部数据结构里，对外一律裸 id：
  - 目录      plugins/max/create_box/
  - manifest  "id": "create_box", "dcc": "max"
  - 树/卡片   显示 name
  - 内部键    ("max", "create_box")
"""

import os
import json
import importlib.util
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from dcc_bridge import DCC_PORTS

DCC_DIRS = tuple(DCC_PORTS)


@dataclass
class PluginInfo:
    id: str
    name: str
    dcc: str                         # "maya" | "max"
    icon: str
    widget_factory_path: str
    shortcut: str = ""
    # 资源树里的分组（2026-10-21）。空 = 平铺在 DCC 分组下。
    # 目前只有一个取值 "settings"（显示为「设置」）——
    # 装自动启动 / ping / reload 这类**调试与设置**性质的功能。
    # ⚠️ 这里存的是**内部标识**（英文），显示名由树负责映射 ——
    #    改名只动一处，不散落在 6 个 manifest 里。
    category: str = ""
    user_script: Optional[dict] = field(default=None, repr=False)
    _dir: str = field(default="", repr=False)
    _factory: Optional[Callable] = field(default=None, repr=False)

    @property
    def key(self) -> Tuple[str, str]:
        """全局唯一键 = (dcc, id)。

        画布 / 布局持久化 / 选中状态全都只认这个，不认裸 id，
        所以 maya 的 create_box 和 max 的 create_box 不会互相认错。
        """
        return (self.dcc, self.id)

    def load_factory(self) -> Callable:
        if self._factory is not None:
            return self._factory
        if self.user_script is not None:
            from functools import partial
            from .user_script_widget import create_widget
            self._factory = partial(create_widget, script=self.user_script)
            return self._factory
        module_name, _, attr = self.widget_factory_path.partition(":")
        if not attr:
            module_name, _, attr = module_name.rpartition(".")
        path = os.path.join(self._dir, f"{module_name}.py")
        # ★ 模块名必须带 dcc ★
        # maya 的 create_box 和 max 的 create_box 会生成完全相同的
        # "plugins.create_box.widget"，同名模块一旦在 widget.py 里触发
        # 任何 import 就会串味。带上 dcc 彻底杜绝。
        mod_id = f"plugins.{self.dcc}_{self.id}_{module_name}"
        spec = importlib.util.spec_from_file_location(mod_id, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._factory = getattr(mod, attr)
        return self._factory


def _read_manifest(plugin_dir: str, dir_dcc: str) -> Optional[PluginInfo]:
    manifest_path = os.path.join(plugin_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        declared = data.get("dcc", "")
        # manifest 的 dcc 与目录名必须一致：放错目录会让人以为是另一个 DCC 的插件
        if declared != dir_dcc:
            print("[PluginLoader] skip %s: manifest.dcc=%r 与目录 %r 不一致"
                  % (os.path.basename(plugin_dir), declared, dir_dcc))
            return None

        return PluginInfo(
            id=data["id"],
            name=data["name"],
            dcc=dir_dcc,
            icon=data.get("icon", ""),
            widget_factory_path=data["widget_factory"],
            shortcut=data.get("shortcut", ""),
            category=data.get("category", ""),
            _dir=plugin_dir,
        )
    except Exception as e:
        print("[PluginLoader] skip %s: %s" % (plugin_dir, e))
        return None


def scan(plugins_dir: str) -> List[PluginInfo]:
    """扫描 plugins/<dcc>/<plugin>/ 两层结构，返回全量插件。

    资源树并列显示各宿主，未连接的宿主也可以打开插件卡片。
    """
    result: List[PluginInfo] = []
    if not os.path.isdir(plugins_dir):
        return result

    for dcc in DCC_DIRS:
        dcc_dir = os.path.join(plugins_dir, dcc)
        if not os.path.isdir(dcc_dir):
            continue
        for name in sorted(os.listdir(dcc_dir)):
            sub = os.path.join(dcc_dir, name)
            if not os.path.isdir(sub):
                continue
            info = _read_manifest(sub, dcc)
            if info is not None:
                result.append(info)
    return result


def index_by_key(plugins: List[PluginInfo]) -> dict:
    """{("maya","create_box"): PluginInfo, ...}"""
    return {p.key: p for p in plugins}
