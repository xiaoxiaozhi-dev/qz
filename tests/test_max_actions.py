"""
max_actions 的纯逻辑测试（不起 Max，用假 pymxs 替身）。

为什么必须专门测这个：
    create_box 曾在真机上 100% 失败 —— `rt.execute('delete (getNodeByName "%s")')`
    在节点不存在时抛 MAXScript 异常：
        没有与以下项对应的"delete"函数 undefined。
    首次创建必然找不到，所以这个 bug 表现为「这脚本从来没成功过」。

    而现有 test_max_bridge.py 走真 socket 但**不装 pymxs**，
    action 函数体根本没被执行 → 这类"函数体里的运行时错误"它一项都抓不到。
    这里用假 pymxs 把 action 真正跑一遍。

跑法：
    .venv/Scripts/python.exe test_max_actions.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 假 pymxs ────────────────────────────────────────
class FakePoint3:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __repr__(self):
        return "Point3(%g,%g,%g)" % (self.x, self.y, self.z)

    def as_list(self):
        return [self.x, self.y, self.z]


class FakeNode:
    def __init__(self, name, pos=(0, 0, 0)):
        self.name = name
        self.position = FakePoint3(*pos)
        self.boxSize = None

    def __repr__(self):
        return "Node(%s@%r)" % (self.name, self.position.as_list())


class FakeRuntime:
    """模拟 pymxs.runtime 里我们用到的那几个成员。"""

    def __init__(self):
        self.nodes = {}          # name -> FakeNode
        self.created = []
        self.deleted = []
        self._selection = []
        # record execute() calls so tests can assert we DON'T rely on it
        self.execute_calls = []

    # -- API --
    def Point3(self, x, y, z):
        return FakePoint3(x, y, z)

    def getNodeByName(self, name):
        # 原生 API 语义：找不到返回 None，不抛异常（这是关键差异）
        return self.nodes.get(name)

    def delete(self, node):
        if node is None:
            raise RuntimeError(
                '没有与以下项对应的"delete"函数 undefined。')
        self.nodes.pop(node.name, None)
        self.deleted.append(node.name)
        if node in self._selection:
            self._selection.remove(node)

    def Box(self, name=None, boxSize=None, pos=None):
        n = FakeNode(name, pos.as_list() if pos else (0, 0, 0))
        n.boxSize = boxSize
        self.nodes[name] = n
        self.created.append(n)
        return n

    def execute(self, script):
        # 真 pymxs 会把脚本交给 MAXScript。这里只记录，不执行。
        self.execute_calls.append(script)
        if self.initializing and script.strip().splitlines() == [
            'global qz_vc_numbers_stop',
            'if qz_vc_numbers_stop != undefined do qz_vc_numbers_stop()',
        ]:
            return
        raise AssertionError(
            "action 不该再依赖 rt.execute() 拼字符串：%r" % script)

    @property
    def selection(self):
        return self._selection


def install_fake_pymxs():
    rt = FakeRuntime()
    mod = types.ModuleType("pymxs")
    mod.runtime = rt
    sys.modules["pymxs"] = mod
    return rt


# ── 测试框架 ────────────────────────────────────────
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def main():
    rt = install_fake_pymxs()
    rt.initializing = True

    # action 模块用 `from max_bridge.server import register_action`，
    # 这里只需要 register_action 能装饰并返回原函数。
    import max_bridge.server as srv
    if not hasattr(srv, "register_action"):
        # 真模块有的话直接用；没有就临时补一个
        _reg = {}
        srv.register_action = lambda name: (
            lambda fn: (_reg.__setitem__(name, fn), fn)[1])
    import max_actions.create_box as cb
    import max_actions.move_selected as ms
    rt.initializing = False
    import importlib
    cb = importlib.reload(cb)
    ms = importlib.reload(ms)
    # Package startup may reset the independent vertex-color viewport callback.
    rt.execute_calls.clear()

    print("=" * 58)
    print("max_actions 逻辑测试（假 pymxs，不起 Max）")
    print("=" * 58)

    print("\n1. create_box：节点不存在时不能炸（这次的实机 bug）")
    rt.nodes.clear(); rt.created.clear(); rt.deleted.clear()
    r = cb.create_box(size=5, pos=[1, 2, 3], name="qz_box")
    check("首次创建成功", not r.get("error"), r)
    check("标了 replaced=False", r.get("replaced") is False, r)
    check("Box 被真正建出来", "qz_box" in rt.nodes)
    n = rt.nodes.get("qz_box")
    check("位置正确", n and n.position.as_list() == [1, 2, 3],
          n.position.as_list() if n else None)
    check("尺寸正确", n and n.boxSize.as_list() == [5, 5, 5],
          n.boxSize.as_list() if n else None)

    print("\n2. create_box：同名物体存在时先删再建（不报重名）")
    rt.created.clear(); rt.deleted.clear()
    r2 = cb.create_box(size=8, name="qz_box")
    check("重建成功", not r2.get("error"), r2)
    check("标了 replaced=True", r2.get("replaced") is True, r2)
    check("旧物体被删了", rt.deleted == ["qz_box"], rt.deleted)
    check("尺寸已更新为 8",
          rt.nodes["qz_box"].boxSize.as_list() == [8, 8, 8])

    print("\n3. create_box：参数校验")
    check("size 非数字被拒", bool(cb.create_box(size="abc").get("error")))
    check("size<=0 被拒", bool(cb.create_box(size=0).get("error")))
    check("pos 非三元素被拒", bool(cb.create_box(pos=[1, 2]).get("error")))
    check("pos 含非数字被拒", bool(cb.create_box(pos=[1, "a", 3]).get("error")))
    check("name 空串被拒", bool(cb.create_box(name="  ").get("error")))

    print("\n4. create_box：名字带引号也不破坏脚本（不拼字符串）")
    # 旧实现会把 'evil" ; delete $' 拼进 MAXScript 语句里
    rq = cb.create_box(size=1, name='we"ird')
    check("带引号的名字正常建出", not rq.get("error"), rq)
    check('we"ird 存在于场景', 'we"ird' in rt.nodes)
    check("全程没有调用 rt.execute", len(rt.execute_calls) == 0,
          rt.execute_calls)

    print("\n5. move_selected：无选择时给友好提示")
    rt._selection = []
    r = ms.move_selected(axis="x", distance=1)
    check("报告 moved=0", r.get("moved") == 0, r)
    check("带可读 error", "没有选中" in str(r.get("error")), r)

    print("\n6. move_selected：三个轴 + 负方向")
    for axis, expect in (("x", [2, 0, 0]), ("y", [0, 2, 0]),
                         ("z", [0, 0, 2]), ("-x", [-2, 0, 0])):
        n1 = FakeNode("n1", (0, 0, 0))
        rt.nodes = {"n1": n1}
        rt._selection = [n1]
        rr = ms.move_selected(axis=axis, distance=2)
        check("沿 %s 移动正确" % axis,
              n1.position.as_list() == expect,
              "%s -> %r" % (axis, n1.position.as_list()))
        check("沿 %s 无 error" % axis, not rr.get("error"), rr)

    print("\n7. move_selected：批量移动与部分失败")
    a, b = FakeNode("a", (0, 0, 0)), FakeNode("b", (10, 10, 10))
    rt.nodes = {"a": a, "b": b}
    rt._selection = [a, b]
    r = ms.move_selected(axis="x", distance=1)
    check("两个都移动了", r.get("moved") == 2, r)
    check("a 位置正确", a.position.as_list() == [1, 0, 0])
    check("b 位置正确", b.position.as_list() == [11, 10, 10])

    # 造一个 position 不可写的对象，验证是「部分失败」而不是整体抛异常
    class Bad:
        name = "bad"
        @property
        def position(self):
            return FakePoint3(0, 0, 0)

    ok = FakeNode("ok", (0, 0, 0))
    rt._selection = [ok, Bad()]
    r = ms.move_selected(axis="y", distance=5)
    check("部分失败时 moved 仍正确", r.get("moved") == 1, r)
    check("部分失败时带 error", "移动失败" in str(r.get("error")), r)
    check("好对象照样移动了", ok.position.as_list() == [0, 5, 0])

    print("\n8. move_selected：参数校验")
    check("非法 axis 被拒", bool(ms.move_selected(axis="w").get("error")))
    check("distance 非数字被拒",
          bool(ms.move_selected(distance="abc").get("error")))

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
