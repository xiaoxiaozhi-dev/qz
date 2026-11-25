"""中台 2.0 集成测试（双 DCC：maya + max 并列）。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets
app = QtWidgets.QApplication(sys.argv)

from ui.main_window import MainWindow
from ui.layout_state import _default_path as layout_path

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("  -> " + str(detail)) if detail else ""))


def pump(n=10, ms=30):
    for _ in range(n):
        QtCore.QThread.msleep(ms)
        QtWidgets.QApplication.processEvents()


# 1 启动 + 插件扫描
# ★ 先清掉上轮遗留的布局，否则启动就把 8 个节点全恢复了，
#   后面「同名插件共存」这类断言会被上一轮的状态污染。
_layout = layout_path()
if os.path.isfile(_layout):
    os.remove(_layout)
w = MainWindow()
pump()
maya = sorted(p.id for p in w._all_plugins if p.dcc == "maya")
mx = sorted(p.id for p in w._all_plugins if p.dcc == "max")
check("标题", w.windowTitle().startswith("Qz Tools"), w.windowTitle())
# auto_setup 是自动启动安装卡片（2026-10-19 加），
# 两边各一份 —— 逻辑同构但 UI 各写各的，不是同一份代码
# ⚠️ maya 侧 2026-10-21 补上了 ping / reload 两张卡片
#   （原先只有 max 侧有，这次同步过去了）
# 2026-11-02 加了 rename_tool（批量重命名卡片）
check("扫到 maya 插件", maya == ["auto_setup",
                                "fbx_to_blender", "fbx_to_max", "js_anim_tools",
                                "ping", "reload", "rename_tool", "skin_weights"], maya)
check("扫到 max 插件", mx == ["auto_setup", "fbx_to_maya",
                              "ping", "reload", "rename_tool", "rigging_toolbox",
                              "skin_weights", "vertex_color"], mx)
check("扫到 Blender 插件", {p.id for p in w._all_plugins if p.dcc == "blender"}
      == {"auto_setup", "fbx_to_maya", "ping", "reload", "rename_tool", "skin_weights"})
check("复合键无歧义", len({p.key for p in w._all_plugins}) == len(w._all_plugins))

# 2 activity 切换
w._activity.show_panel(0)
pump(4, 20)
check("资源面板可见", not w._side.isHidden())
# 2026-10-19 搜索移到画布 Ctrl+F 浮层，侧栏只剩 2 个 tab
w._activity.show_panel(1)
pump(4, 20)
check("设置面板 index（搜索已移除）",
      w._side._stack.currentIndex() == 1)
check("★ 侧栏只剩 2 个 tab（无搜索）",
      w._side._stack.count() == 2, w._side._stack.count())
w._activity.show_panel(-1)
check("side panel 可隐藏", w._side.isHidden())
w._activity.show_panel(0)

# 3 双桥接并列
check("maya 桥接存在", "maya" in w._bridges)
check("max 桥接存在", "max" in w._bridges)
check("maya 端口 17023", w._bridges["maya"].port == 17023, w._bridges["maya"].port)
check("max 端口 17024", w._bridges["max"].port == 17024, w._bridges["max"].port)
check("两个 client 各自独立",
      w._clients["maya"] is not w._clients["max"]
      and w._clients["maya"].dcc == "maya"
      and w._clients["max"].dcc == "max")

# 4 节点树分支
tree = w._side._tree
top = tree.topLevelItemCount()
labels = [tree.topLevelItem(i).text(0) for i in range(top)]
check("树有三个平级分支", len(labels) == 3, labels)
check("maya 分支", any("maya" in s.lower() for s in labels), labels)
check("max 分支", any("max" in s.lower() for s in labels), labels)
check("Blender 分支", "Blender (6)" in labels, labels)

# 5 同名插件可同时打开（核心验证）
rename_maya = next(p for p in w._all_plugins if p.key == ("maya", "rename_tool"))
rename_max = next(p for p in w._all_plugins if p.key == ("max", "rename_tool"))
w._canvas.open_plugin(rename_maya, 0, 0)
w._canvas.open_plugin(rename_max, 400, 0)
check("同名插件共存", len(w._canvas._proxies) == 2, list(w._canvas._proxies.keys()))
p_maya = w._canvas._proxies[("maya", "rename_tool")]
p_max = w._canvas._proxies[("max", "rename_tool")]
check("两个 client 分别注入",
      p_maya.widget().content()._qz_dcc == "maya"
      and p_max.widget().content()._qz_dcc == "max",
      (getattr(p_maya.widget().content(), "_qz_dcc", "?"),
       getattr(p_max.widget().content(), "_qz_dcc", "?")))
check("node 存的复合键",
      p_maya.widget().plugin_id() == ("maya", "rename_tool")
      and p_max.widget().plugin_id() == ("max", "rename_tool"),
      (p_maya.widget().plugin_id(), p_max.widget().plugin_id()))
# 同一插件重复打开 = 聚焦，不新增
w._canvas.open_plugin(rename_maya, 0, 0)
check("重复打开不新增节点", len(w._canvas._proxies) == 2)

# 6 关闭只关一个
w._canvas.close_plugin(("max", "rename_tool"))
check("关闭 max 不影响 maya", len(w._canvas._proxies) == 1
      and ("maya", "rename_tool") in w._canvas._proxies)

# 7 全量打开 + 锁定 + fit
for i, info in enumerate(w._all_plugins):
    w._canvas.open_plugin(info, (i % 4) * 360, (i // 4) * 260)
check("全量打开", len(w._canvas._proxies) == len(w._all_plugins),
      len(w._canvas._proxies))
w._canvas.set_locked(True)
check("锁定生效", w._canvas.is_locked())
check("锁定指示器可见", w._canvas._lock_indicator.isVisible())
w._canvas.set_locked(False)
w._canvas.fit_all()
check("fit_all 有效", 0.05 < w._canvas.transform().m11() < 20,
      "%.3f" % w._canvas.transform().m11())

# 8 持久化
w.close()
pump(4, 20)
path = layout_path()
data = json.load(open(path))
check("layout v4", data["version"] == 4, data["version"])
pairs = sorted((p["dcc"], p["id"]) for p in data["open_plugins"])
check("持久化带 dcc", len(pairs) == len(w._all_plugins), pairs)

# 9 重启恢复
w2 = MainWindow()
pump()
check("重启后节点全部恢复", len(w2._canvas._proxies) == len(w._all_plugins),
      len(w2._canvas._proxies))
keys = set(w2._canvas._proxies.keys())
check("恢复的键都是复合键",
      all(isinstance(k, tuple) and len(k) == 2 for k in keys))

# 10 截图
w2.show()
pump(4, 20)
_shot = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'build', 'test-artifacts', 'app-2.png')
os.makedirs(os.path.dirname(_shot), exist_ok=True)
w2.grab().save(_shot, 'PNG')
check("截图", os.path.isfile(_shot))

print()
print("=" * 50)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：%s" % FAIL)
print("=" * 50)

# ⚠️ **失败必须返回非零退出码**（2026-10-22 补）。
#   只打印"失败：..."而退出码仍为 0 的话，所有回归工具都会把它报成 OK。
sys.exit(1 if FAIL else 0)
