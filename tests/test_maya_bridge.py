"""
test_maya_bridge.py — 本地无 Maya 环境验证 client ↔ server 链路。
用法：
    python test_maya_bridge.py
说明：
    启动一个真实的 maya_bridge.server（mock actions 直接走 register_action），
    然后用 MayaClient 测 ping + 一个有参数的业务 action，验证整条链路。
"""
import threading
import sys
import os
import time
import socket

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import maya_bridge.server as srv
import maya_bridge
import maya_actions          # 触发自动扫描注册 ping / bone_mirror / create_box / create_sphere

# ── mock：覆盖真实的 bone_mirror，让测试在无 Maya 环境能跑通 ──
from maya_bridge import register_action


@register_action("bone_mirror")
def _mock_bone_mirror(prefix="L_", axis="X"):
    return [f"{prefix}joint_01", f"{prefix}joint_02"]


def _free_port() -> int:
    """问系统要一个空闲端口。

    ⚠️ 绝不能用固定的 17023 ⚠️
    真 Maya 一旦开着 bridge，那个端口就被占着，而 server 用的是
    SO_EXCLUSIVEADDRUSE（这是正确做法）→ 测试起不来服务，
    报错还长得像协议有问题。测试环境冲突不该伪装成代码 bug。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ⚠️ 用空闲随机端口，避免和真 Maya / Max 撞端口
PORT = _free_port()


def _start_mock_server(port=None):
    port = port or PORT
    srv.MAYA_PORT = port   # 让 server 用我们指定的端口
    t = threading.Thread(target=srv.start_server, args=(port,), daemon=True)
    t.start()
    return t


def _run_client_tests(port=None):
    port = port or PORT
    from maya_bridge import MayaClient
    client = MayaClient(port=port)

    print("→ ping()")
    r = client.ping()
    assert r["ok"], r
    print(f"  {r}")
    assert r["data"]["pong"] is True

    print("→ call('bone_mirror', prefix='L_', axis='X')")
    r = client.call("bone_mirror", prefix="L_", axis="X")
    assert r["ok"], r
    print(f"  {r}")
    assert r["data"] == ["L_joint_01", "L_joint_02"]

    print("\n[PASS] client ↔ server 链路验证通过")


if __name__ == "__main__":
    _start_mock_server()
    time.sleep(0.3)
    _run_client_tests()
