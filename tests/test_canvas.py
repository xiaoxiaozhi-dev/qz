"""测试画布节点交互：拖动、删除、锁定、焦点。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets, QtGui
app = QtWidgets.QApplication(sys.argv)

from ui.main_window import MainWindow
from ui.layout_state import _default_path as layout_path

_layout = layout_path()
if os.path.isfile(_layout):
    os.remove(_layout)
w = MainWindow()
w.show()
for i in range(15):
    QtCore.QThread.msleep(20)
    QtWidgets.QApplication.processEvents()

# mock 同步 client
class OkClient:
    def __init__(self):
        self.host = '127.0.0.1'
        self.port = 17023
    def call(self, action, timeout=10, **params):
        return {'ok': True, 'data': {'mock': action}}
    def ping(self):
        return {'ok': True, 'data': {}}
ok_client = OkClient()
# 双 DCC：两个 client 都要 mock，否则 widget 调 max client 会真去连 17024
w._clients["maya"] = ok_client
w._clients["max"] = ok_client

# 打开 3 个插件
for info in w._all_plugins:
    w._canvas.open_plugin(info)
for i in range(15):
    QtCore.QThread.msleep(20)
    QtWidgets.QApplication.processEvents()

print(f'[1] 节点数: {len(w._canvas._proxies)}')

# 记录初始坐标
positions = w._canvas.get_node_positions()
print(f'[2] 初始坐标: {[(pid, f"({int(x)},{int(y)})") for pid, (x, y) in positions.items()]}')

# 移动一个节点
pid_to_move = list(w._canvas._proxies.keys())[0]
proxy = w._canvas._proxies[pid_to_move]
old_x, old_y = proxy.pos().x(), proxy.pos().y()
proxy.setPos(old_x + 200, old_y + 100)
moved = [(pid, int(p.pos().x()), int(p.pos().y())) for pid, p in w._canvas._proxies.items()]
print(f'[3] 移动后: {moved}')

# 锁定后节点 ItemIsMovable 取消
w._canvas.set_locked(True)
for pid, p in w._canvas._proxies.items():
    movable = bool(p.flags() & QtWidgets.QGraphicsItem.ItemIsMovable)
    selectable = bool(p.flags() & QtWidgets.QGraphicsItem.ItemIsSelectable)
    print(f'[4] {pid} locked: movable={movable} selectable={selectable}')

# 解锁 + 删除一个节点
w._canvas.set_locked(False)
to_delete = list(w._canvas._proxies.keys())[2]
w._canvas.close_plugin(to_delete)
print(f'[5] 删 {to_delete} 后剩余: {list(w._canvas._proxies.keys())}')

# fit_all
w._canvas.fit_all()
print(f'[6] fit scale: {w._canvas.transform().m11():.3f}')

# focus
w._canvas.focus_plugin(list(w._canvas._proxies.keys())[0])
print(f'[7] focus OK')

# 测试关闭 → save → 重启
w.close()
import json
data = json.load(open(layout_path()))
print(f'[8] save open: {[(p["dcc"], p["id"], int(p["x"]), int(p["y"])) for p in data["open_plugins"]]}')

w2 = MainWindow()
w2.show()
for i in range(20):
    QtCore.QThread.msleep(20)
    QtWidgets.QApplication.processEvents()
print(f'[9] 重启后节点数: {len(w2._canvas._proxies)}')

# 截图最终（存到 tests/ 下，不要污染项目根）
_w2 = w2.grab()
_shot = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "build", "test-artifacts", "canvas.png")
os.makedirs(os.path.dirname(_shot), exist_ok=True)
_w2.save(_shot, 'PNG')
print('[10] 截图 OK ->', _shot)

# ── 冒烟断言 ────────────────────────────────
# ⚠️ 原来这个文件**只有 print、没有断言**，退出码恒为 0 ——
#   也就是说它「永远通过」，回归时提供不了任何保护。
#   2026-10-22 补上关键断言 + 非零退出码。
print()
_fail = []


def _check(label, cond, detail=""):
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", label,
                           (" — " + detail) if detail else ""))
    if not cond:
        _fail.append(label)


_check("节点已打开", len(w2._canvas._proxies) > 0,
       "%d 个" % len(w2._canvas._proxies))
_check("布局已落盘", os.path.isfile(layout_path()))
_check("截图已生成", os.path.isfile(_shot))
_check("fit_all 产生有效缩放", 0 < w._canvas.transform().m11() < 100,
       "%.3f" % w._canvas.transform().m11())

print()
if _fail:
    print("=== HAS FAILURES ===")
    sys.exit(1)
print("=== ALL PASS ===")
sys.exit(0)
