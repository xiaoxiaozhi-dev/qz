"""
rename_tool 测试（不启动 Maya）。

测什么：
  1. 名字生成纯函数（前缀 / 后缀 / 替换 / 编号 / 删字）
  2. action 在**假 cmds** 上跑完整流程 —— 前缀、含层级编号、替换、删字、查重名
  3. 卡片：manifest 能扫到、按钮点下去参数对不对（含 1 起 → 0 起的删字下标）

为什么用假 cmds 而不是直接 mock action：
    编号、层级摊平、uuid 定位这些逻辑都在 action 里，只 mock 掉 action
    等于什么都没测。这里用一个最小的假场景，把真实代码跑一遍。

跑法：
    .venv/Scripts/python.exe test_rename_tool.py
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# ★ GUI 测试必须 offscreen，且要在 import Qt 之前设
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


# ══ 1. 纯函数 ═════════════════════════════════════════
print("1. 名字生成")
import maya_actions.rename_tool as rt

check("加前缀", rt.apply_prefix("cube1", "L_") == "L_cube1")
check("加后缀", rt.apply_suffix("cube1", "_geo") == "cube1_geo")
check("替换", rt.apply_replace("L_hand_l", "L_", "R_") == "R_hand_l")
check("查找为空时原样返回", rt.apply_replace("cube1", "", "x") == "cube1")
check("编号两位", rt.build_numbered("bone_", 1) == "bone_01")
check("编号带前后缀", rt.build_numbered("bone_", 12, "L_", "_jnt") == "L_bone_12_jnt")
check("删第 0 位", rt.pop_char("cube1", 0) == "ube1")
check("删最后一位", rt.pop_char("cube1", -1) == "cube1"[:-1])
check("删空返回 None", rt.pop_char("a", 0) is None)
check("越界返回 None", rt.pop_char("ab", 9) is None)


# ══ 2. 假 cmds ════════════════════════════════════════
class FakeScene(object):
    """只实现 rename_tool 真正用到的那几个 cmds 接口。"""

    def __init__(self):
        self.nodes = {}      # uuid -> {"name": str, "parent": uuid|None}
        self.sel = []
        self._n = 0

    def add(self, name, parent=None):
        self._n += 1
        u = "uuid-%d" % self._n
        self.nodes[u] = {"name": name, "parent": parent}
        self.opt(u, name)    # 保证父级也被建进节点表
        return u

    def opt(self, *a):
        pass

    def path(self, u):
        parts, cur = [], u
        while cur:
            parts.append(self.nodes[cur]["name"])
            cur = self.nodes[cur]["parent"]
        return "|" + "|".join(reversed(parts))

    def uuid_by_path(self, p):
        if p in self.nodes:
            return p
        for u in self.nodes:
            if self.path(u) == p:
                return u
        return None

    def uuid_by_name(self, name):
        """按短名找（Maya 里短名不唯一时取第一个，够用）。"""
        for u, d in self.nodes.items():
            if d["name"] == name:
                return u
            if self.path(u) == name:
                return u
        return None

    def siblings(self, parent, exclude=None):
        return [u for u, d in self.nodes.items()
                if d["parent"] == parent and u != exclude]

    def take_short(self, name, parent, me=None):
        """Maya 的重名规则：同级重名会补数字。"""
        base, i = name, 1
        cur = name
        while any(self.nodes[s]["name"] == cur
                  for s in self.siblings(parent, exclude=me)):
            cur = "%s%d" % (base, i)
            i += 1
        return cur

    def cmds(self):
        scene = self

        class C(object):
            def ls(self, *args, **kw):
                if kw.get("selection"):
                    return [scene.path(u) for u in scene.sel]
                if kw.get("objectsOnly"):
                    counts = {}
                    for d in scene.nodes.values():
                        counts[d["name"]] = counts.get(d["name"], 0) + 1
                    out = []
                    for u, d in scene.nodes.items():
                        out.append(scene.path(u) if counts[d["name"]] > 1
                                   else d["name"])
                    return out
                if kw.get("uuid"):
                    u = scene.uuid_by_name(args[0])
                    return [u] if u else []
                if kw.get("l"):
                    u = scene.uuid_by_path(args[0])
                    return [scene.path(u)] if u else []
                return []

            def listRelatives(self, obj, allDescendents=False, fullPath=False):
                root = scene.uuid_by_path(obj) or scene.uuid_by_name(obj)
                if root is None:
                    return None
                out, frontier = [], [root]
                while frontier:
                    nxt = []
                    for p in frontier:
                        for u, d in scene.nodes.items():
                            if d["parent"] == p:
                                nxt.append(u)
                    out.extend(nxt)
                    frontier = nxt
                return [scene.path(u) for u in out]

            def rename(self, long_name, new):
                u = scene.uuid_by_path(long_name)
                if u is None:
                    raise RuntimeError("no object %s" % long_name)
                parent = scene.nodes[u]["parent"]
                # 自己不算占位：同级改回自己原名是合法的
                scene.nodes[u]["name"] = scene.take_short(new, parent, me=u)
                return scene.path(u)

            def select(self, items=None, clear=False):
                if clear or not items:
                    scene.sel = []
                else:
                    scene.sel = [scene.uuid_by_path(i) for i in items]

        return C()


def use_scene(scene):
    """把假 cmds 塞进 sys.modules，让 action 里的 import 拿到它。"""
    mod = types.ModuleType("maya.cmds")
    mod_cmds = scene.cmds()
    sys.modules["maya"] = types.ModuleType("maya")
    sys.modules["maya.cmds"] = mod_cmds
    return mod_cmds


def new_scene():
    s = FakeScene()
    grp = s.add("grp")
    s.add("cube_a", parent=grp)
    s.add("cube_b", parent=grp)
    s.add("sphere")
    s.sel = [grp]
    return s


print("\n2. action 全流程（假 cmds）")
scene = new_scene()
use_scene(scene)

# 前缀
r = rt.rename_tool(op="prefix", prefix="L_", hierarchy=True)
names = sorted(d["name"] for d in scene.nodes.values())
check("含层级加前缀：子级+父级都改", names == ["L_cube_a", "L_cube_b", "L_grp", "sphere"], names)
check("返回数量", r["count"] == 3, r)

# 编号（含层级）
scene = new_scene()
use_scene(scene)
r = rt.rename_tool(op="rename", base="bone_", hierarchy=True)
names = sorted(d["name"] for d in scene.nodes.values())
check("含层级编号：01/02 子级 + 03 父级",
      names == ["bone_01", "bone_02", "bone_03", "sphere"], names)

# 替换（仅选中）
scene = new_scene()
use_scene(scene)
scene.sel = [u for u, d in scene.nodes.items() if d["name"] == "sphere"]
r = rt.rename_tool(op="replace", search="sphere", replace="ball")
check("替换", [d["name"] for d in scene.nodes.values() if d["name"] == "ball"] == ["ball"])
check("替换没命中时 count=0", rt.rename_tool(op="replace", search="zzz", replace="y")["count"] == 0)

# 删字
scene = new_scene()
use_scene(scene)
scene.sel = [u for u, d in scene.nodes.items() if d["name"] == "sphere"]
rt.rename_tool(op="delete_index", index=0)
check("删首字", "phere" in [d["name"] for d in scene.nodes.values()])
rt.rename_tool(op="delete_index", index=-1)
check("删尾字", "pher" in [d["name"] for d in scene.nodes.values()])

# 重名检测
scene = new_scene()
use_scene(scene)
g2 = scene.add("grp2")
scene.add("sphere", parent=g2)          # 与顶层 sphere 重名
r = rt.rename_tool(op="check_duplicates")
check("查重名：找到 2 个", r["count"] == 2, r)
check("查重名：选中了它们", len(scene.sel) == 2, scene.sel)

# 空输入 / 空选择
scene = new_scene()
use_scene(scene)
check("空前缀不执行", rt.rename_tool(op="prefix", prefix="")["count"] == 0)
check("未知 op 有提示", "未知操作" in rt.rename_tool(op="nope")["warning"])
scene.sel = []
check("未选中给 warning", "未选中" in rt.rename_tool(op="prefix", prefix="L_")["warning"])


# ══ 3. 注册 + 卡片 ═════════════════════════════════════
print("\n3. 注册与卡片")
import maya_actions                     # noqa: F401 触发扫描
import maya_bridge.server as srv

check("action 已注册", "rename_tool" in srv.ACTIONS)

from ui import plugin_loader
plugins = plugin_loader.scan(os.path.join(HERE, "plugins"))
info = next((p for p in plugins if p.key == ("maya", "rename_tool")), None)
check("manifest 能扫到", info is not None)
check("显示名", info is not None and info.name == "批量重命名",
      info.name if info else "")

from PySide6 import QtWidgets
from ui.app_style import apply_application_style

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # ★ 必须持有
apply_application_style(app)
calls = []


class _Log(object):
    def info(self, msg):
        pass

    def error(self, msg):
        pass


class _Client(object):
    def call(self, action, **kw):
        calls.append((action, kw))
        return {"ok": True, "data": {"count": 0, "names": []}}


w = info.load_factory()(_Client(), _Log())
check("默认范围 = 仅选中", w.sel_rb.isChecked())

w.show()
app.processEvents()
search_rect = w.search_edit.geometry()
replace_rect = w.replace_edit.geometry()
button_rect = w.replace_btn.geometry()
check("查找与替换分两行，按钮填满两行和间距",
      search_rect.bottom() < replace_rect.top()
      and button_rect.left() > max(search_rect.right(), replace_rect.right())
      and button_rect.top() == search_rect.top()
      and button_rect.bottom() == replace_rect.bottom(),
      (search_rect, replace_rect, button_rect))

w.prefix_edit.setText("L_")
w.prefix_btn.click()
check("加前缀按钮 → action", calls[-1][0] == "rename_tool", calls[-1])
check("加前缀参数", calls[-1][1]["op"] == "prefix"
      and calls[-1][1]["prefix"] == "L_"
      and calls[-1][1]["hierarchy"] is False, calls[-1])

w.hier_rb.setChecked(True)
w.search_edit.setText("_old")
w.replace_edit.setText("")
w.replace_btn.click()
check("替换按钮保留空替换值和层级参数",
      calls[-1] == ("rename_tool", {"op": "replace", "search": "_old",
                                    "replace": "", "hierarchy": True}), calls[-1])

w.base_edit.setText("bone_")
w.add_prefix_cb.setChecked(True)
w.add_suffix_cb.setChecked(False)  # 显式测试只套用前缀，不依赖用户的启动默认值。
w.rename_btn.click()
check("含层级开关生效", calls[-1][1]["hierarchy"] is True, calls[-1])
check("重命名参数",
      calls[-1][1]["op"] == "rename"
      and calls[-1][1]["base"] == "bone_"
      and calls[-1][1]["use_prefix"] is True
      and calls[-1][1]["use_suffix"] is False, calls[-1])

w.index_spin.setValue(3)
w.del_index_btn.click()
check("删字下标 1 起 → 0 起", calls[-1][1]["index"] == 2, calls[-1])
w.del_start_btn.click()
check("删首字 = 0", calls[-1][1]["index"] == 0, calls[-1])
w.del_end_btn.click()
check("删尾字 = -1", calls[-1][1]["index"] == -1, calls[-1])

w.check_btn.click()
check("查重名 op", calls[-1][1]["op"] == "check_duplicates", calls[-1])

# 卡片高度不能塌（canvas 按 sizeHint 撑高）
w.resize(288, 100)
w.adjustSize()
check("卡片有最小高度", w.sizeHint().height() >= 200, w.sizeHint().height())

del w
app.processEvents()

print("\n通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
