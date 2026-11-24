"""多选 + 框选 + 批量移动 测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest

app = QtWidgets.QApplication(sys.argv)

from ui.canvas import Canvas
from ui.plugin_loader import PluginInfo


def factory(info):
    w = QtWidgets.QWidget()
    w.setMinimumSize(300, 150)
    b = QtWidgets.QPushButton(f"BTN-{info.id}")
    QtWidgets.QVBoxLayout(w).addWidget(b)
    return w


# ★ 画布用 (dcc, id) 复合键索引，所以测试里也必须用复合键访问，
#   不能再用裸 'A' —— 否则 c._proxies[KA] 直接 KeyError。
KA, KB, KC = ('maya', 'A'), ('maya', 'B'), ('max', 'C')


def build():
    c = Canvas(factory=factory)
    c.resize(1000, 800)
    c.show()
    for _ in range(10):
        QtCore.QThread.msleep(5); QtWidgets.QApplication.processEvents()
    # 故意跨 DCC：maya/maya/max —— 验证多选/框选对复合键一视同仁
    for key, pos in [(KA, (0, 0)), (KB, (500, 0)), (KC, (0, 400))]:
        # PluginInfo 收裸 id + dcc，key 由它自己合成
        c.open_plugin(PluginInfo(id=key[1], name=key[1], dcc=key[0],
                                 icon='', widget_factory_path='x:y'),
                      x=pos[0], y=pos[1])
    for _ in range(5):
        QtCore.QThread.msleep(5); QtWidgets.QApplication.processEvents()
    c.fit_all()
    for _ in range(5):
        QtCore.QThread.msleep(5); QtWidgets.QApplication.processEvents()
    return c


def vp(c, sx, sy):
    p = c.mapFromScene(QtCore.QPointF(sx, sy))
    return QtCore.QPoint(int(p.x()), int(p.y()))


def title_vp(c, pr):
    return vp(c, pr.scenePos().x() + 150, pr.scenePos().y() + 12)


def drag(c, p0, p1, mods=QtCore.Qt.NoModifier):
    QTest.mousePress(c.viewport(), QtCore.Qt.LeftButton, mods, p0)
    for _ in range(3):
        QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
    QTest.mouseMove(c.viewport(), p1)
    for _ in range(3):
        QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
    QTest.mouseRelease(c.viewport(), QtCore.Qt.LeftButton, mods, p1)
    for _ in range(3):
        QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()


ok = True


def check(label, cond, detail=""):
    global ok
    if not cond:
        ok = False
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")


# ── 1. 框选 ────────────────────────────────
print("[1] 空白处框选")
c = build()
print(f"    dragMode = {c.dragMode()} (RubberBandDrag={QtWidgets.QGraphicsView.RubberBandDrag})")
check("开启 RubberBandDrag", c.dragMode() == QtWidgets.QGraphicsView.RubberBandDrag)
print(f"    selectionMode = {c.rubberBandSelectionMode()} (期望 IntersectsItemShape)")

c.clear_selection()
drag(c, vp(c, 1100, 750), vp(c, 250, 60))
sel = sorted(c.selected_ids())
print(f"    框选结果 = {sel}")
check("框选到多个节点", len(sel) >= 2, f"选中 {sel}")

# ── 2. 批量移动 ────────────────────────────
print("\n[2] 框选后拖其中一个 → 全部一起移动")
c = build()
c.clear_selection()
drag(c, vp(c, 1100, 750), vp(c, 250, 60))
sel = c.selected_ids()
print(f"    选中 = {sorted(sel)}")
snap = {pid: (c._proxies[pid].pos().x(), c._proxies[pid].pos().y()) for pid in sel}
mover = c._proxies[sel[0]]
p0 = title_vp(c, mover)
p1 = QtCore.QPoint(p0.x() + 120, p0.y() + 80)
drag(c, p0, p1)

moved = {}
for pid, (ox, oy) in snap.items():
    p = c._proxies[pid]
    moved[pid] = (p.pos().x() - ox, p.pos().y() - oy)
    print(f"    {pid}: ({ox:.0f},{oy:.0f}) → ({p.pos().x():.0f},{p.pos().y():.0f})")

check("所有选中节点都移动了", all(abs(d[0]) > 20 or abs(d[1]) > 20 for d in moved.values()))
deltas = list(moved.values())
same = all(abs(d[0] - deltas[0][0]) < 1.5 and abs(d[1] - deltas[0][1]) < 1.5 for d in deltas)
check("位移量一致（保持相对位置）", same, f"deltas={[(round(d[0]),round(d[1])) for d in deltas]}")

# ── 3. Ctrl + 点击加选 ─────────────────────
print("\n[3] Ctrl + 点击 切换选中")
c = build()
c.clear_selection()
tA = title_vp(c, c._proxies[KA])
QTest.mouseClick(c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tA)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
check("普通点击 → 独占选中 1 个", len(c.selected_ids()) == 1, f"={sorted(c.selected_ids())}")

tB = title_vp(c, c._proxies[KB])
QTest.mouseClick(c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, tB)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
print(f"    Ctrl 点 B 后 = {sorted(c.selected_ids())}")
check("Ctrl 点击 → 变 2 个", len(c.selected_ids()) == 2)

QTest.mouseClick(c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, tB)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
print(f"    再 Ctrl 点 B 后 = {sorted(c.selected_ids())}")
check("Ctrl 再点 → 减选回 1 个", len(c.selected_ids()) == 1)

# ── 4. 批量删除 ────────────────────────────
print("\n[4] Del 批量删除")
c = build()
c.clear_selection()
c.select_all()
print(f"    全选 = {sorted(c.selected_ids())}")
check("Ctrl+A 全选 3 个", len(c.selected_ids()) == 3)
before = len(c._proxies)
ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Delete, QtCore.Qt.NoModifier)
c.keyPressEvent(ev)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
print(f"    删除后剩 {len(c._proxies)} 个")
check("Del 删掉全部选中", len(c._proxies) == 0)

# ── 5. 锁定时禁用框选 ──────────────────────
print("\n[5] 锁定 → 框选禁用")
c = build()
c.set_locked(True)
print(f"    locked={c.is_locked()}, dragMode={c.dragMode()} (NoDrag={QtWidgets.QGraphicsView.NoDrag})")
check("锁定时 dragMode=NoDrag", c.dragMode() == QtWidgets.QGraphicsView.NoDrag)
check("锁定时清空选中", len(c.selected_ids()) == 0)
c.set_locked(False)
print(f"    解锁后 dragMode={c.dragMode()}")
check("解锁后恢复 RubberBandDrag", c.dragMode() == QtWidgets.QGraphicsView.RubberBandDrag)

# ── 6. 批量移动时 z 序不变 ─────────────────
print("\n[6] 批量移动保持相对 z 序")
c = build()
c.clear_selection()
drag(c, vp(c, 1100, 750), vp(c, 250, 60))
sel = c.selected_ids()
if len(sel) >= 2:
    z_before = {pid: c._proxies[pid].zValue() for pid in sel}
    mover = c._proxies[sel[0]]
    p0 = title_vp(c, mover)
    p1 = QtCore.QPoint(p0.x() + 100, p0.y() + 60)
    drag(c, p0, p1)
    z_after = {pid: c._proxies[pid].zValue() for pid in sel}
    same_order = (sorted(z_before, key=lambda k: z_before[k]) ==
                  sorted(z_after, key=lambda k: z_after[k]))
    print(f"    before z = {[(k, round(z_before[k])) for k in sel]}")
    print(f"    after  z = {[(k, round(z_after[k])) for k in sel]}")
    check("相对 z 序不变", same_order)

print()
print("=== ALL PASS ===" if ok else "=== HAS FAILURES ===")
# ── 7. 边界：点未选中节点不应拖动整组 ─────────
print("\n[7] 边界：多选状态下点未选中节点")
c = build()
c.clear_selection()
c.toggle_select(KA); c.toggle_select(KB)
print(f"    预选 = {sorted(c.selected_ids())}")
snap = {pid: (c._proxies[pid].pos().x(), c._proxies[pid].pos().y()) for pid in c.selected_ids()}
pC = title_vp(c, c._proxies[KC])
drag(c, pC, QtCore.QPoint(pC.x()+120, pC.y()+80))
print(f"    A = ({c._proxies[KA].pos().x():.0f},{c._proxies[KA].pos().y():.0f})  (应不变)")
print(f"    C = ({c._proxies[KC].pos().x():.0f},{c._proxies[KC].pos().y():.0f})  (应移动)")
check("已选中的 A/B 未被误拖",
      abs(c._proxies[KA].pos().x()-snap[KA][0]) < 1 and abs(c._proxies[KA].pos().y()-snap[KA][1]) < 1)
check("点选的 C 移动了",
      abs(c._proxies[KC].pos().x()-snap.get(KC,(c._proxies[KC].pos().x(),))[0]) > 20 or
      abs(c._proxies[KC].pos().y()-0) > 20)
check("点 C 后选中变为只有 C", c.selected_ids() == [KC], f"={c.selected_ids()}")

# ── 8. 边界：锁定后不能框选也不能移动 ─────────
print("\n[8] 边界：锁定状态下不可交互")
c = build()
c.set_locked(True)
before = {pid: (c._proxies[pid].pos().x(), c._proxies[pid].pos().y()) for pid in c._proxies}
drag(c, vp(c,1100,750), vp(c,250,60))
check("锁定后框选无效", len(c.selected_ids()) == 0, f"={sorted(c.selected_ids())}")
pA = title_vp(c, c._proxies[KA])
drag(c, pA, QtCore.QPoint(pA.x()+120, pA.y()+80))
check("锁定后节点不移动",
      all(abs(c._proxies[p].pos().x()-before[p][0])<1 for p in before))
ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Delete, QtCore.Qt.NoModifier)
c.keyPressEvent(ev)
check("锁定后 Del 无效", len(c._proxies) == 3, f"剩 {len(c._proxies)}")

# ── 9. Esc / Ctrl+D 取消选择 ─────────────────
print("\n[9] Esc / Ctrl+D 取消选择")
c = build()
c.select_all()
c.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
check("Esc 取消全部选中", len(c.selected_ids()) == 0)
c.select_all()
c.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_D, QtCore.Qt.ControlModifier))
check("Ctrl+D 取消全部选中", len(c.selected_ids()) == 0)

# ── 10. 方向键多选微调 ──────────────────────
print("\n[10] 方向键批量微调")
c = build()
c.select_all()
snap = {p: (c._proxies[p].pos().x(), c._proxies[p].pos().y()) for p in c.selected_ids()}
c.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Right, QtCore.Qt.NoModifier))
moved_all = all(abs(c._proxies[p].pos().x()-snap[p][0]-5) < 1 for p in snap)
check("方向键让所有选中节点各右移 5px", moved_all)

print()
print("=== FINAL: ALL PASS ===" if ok else "=== FINAL: HAS FAILURES ===")

# ── 11. 连续拖动（回归：曾出现"只能微微拖动"）────
print("\n[11] 连续拖动 → 节点应跟手（不分步的回归测试）")
c = build()
pa = c._proxies[KA]
p0 = title_vp(c, pa)
QTest.mousePress(c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p0)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()
start = (pa.pos().x(), pa.pos().y())
# 20 小步模拟真实鼠标
cur = QtCore.QPoint(p0.x(), p0.y())
for step in range(1, 21):
    cur = QtCore.QPoint(p0.x() + step * 8, p0.y() + step * 4)
    QTest.mouseMove(c.viewport(), cur)
    QtWidgets.QApplication.processEvents()
QTest.mouseRelease(c.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, cur)
for _ in range(3):
    QtCore.QThread.msleep(4); QtWidgets.QApplication.processEvents()

moved = (pa.pos().x() - start[0], pa.pos().y() - start[1])
# 鼠标移动了 (160, 80) 屏幕像素，换算成 scene 单位要除以缩放
scale = c.transform().m11()
expect = (160 / scale, 80 / scale)
print(f"    鼠标移动 (160,80) 屏幕px，缩放 {scale:.3f}")
print(f"    节点实际移动 ({moved[0]:.1f},{moved[1]:.1f}) scene 单位")
print(f"    期望 ({expect[0]:.1f},{expect[1]:.1f}) scene 单位")
check("节点跟手（分步拖动也能累计）",
      abs(moved[0] - expect[0]) < 2 and abs(moved[1] - expect[1]) < 2,
      f"差值 ({moved[0]-expect[0]:.1f},{moved[1]-expect[1]:.1f})")
check("不是只挪一个增量（旧 bug 特征）", moved[0] > expect[0] * 0.9)

print()
print("=== FINAL2: ALL PASS ===" if ok else "=== FINAL2: HAS FAILURES ===")

# ⚠️⚠️ **失败必须返回非零退出码**。
#   这条是 2026-10-22 补的 —— 之前这个文件只打印 HAS FAILURES，
#   退出码仍然是 0，导致「相对 z 序不变」的真实回归
#   （z 序重构引入的）被所有回归脚本报成 OK —— 连续骗过了好几轮。
#   ⇒ **判定失败只靠打印 = 没有判定**。任何回归工具都只看退出码。
sys.exit(0 if ok else 1)
