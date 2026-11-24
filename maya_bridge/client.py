"""
MayaClient - 外部程序调用 Maya 的封装
纯 socket + json，无 Maya 依赖
"""

import socket
import json
import os
import sys

def _get_project_dir():
    """兼容打包后的路径"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，数据文件跟 exe 同目录
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class MayaClient:
    def __init__(self, host='127.0.0.1', port=17023):
        self.host = host
        self.port = port

    def call(self, action, timeout=10, **params):
        """发送 action + params，返回结果 dict

        ⚠️ 收到完整响应后必须立刻停止 recv ⚠️
        协议是「一行 JSON + \\n」，读到换行就结束。
        早先写成「一直 recv 到 socket 超时」，靠服务端主动 close 才 break ——
        一旦服务端改成保持连接（Max 端就是这个行为），
        每次调用都会白等满 timeout。协议自带结束标记就别依赖对方实现细节。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((self.host, self.port))
        payload = json.dumps({"action": action, "params": params}) + "\n"
        s.sendall(payload.encode('utf-8'))

        result = b""
        while b"\n" not in result:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                # 对端关闭：数据可能已经收全，也可能没收全
                break
            result += chunk
        s.close()

        if not result:
            raise RuntimeError("Maya 未响应，请确认 Maya 已启动且 bridge 已运行")

        return json.loads(result.decode('utf-8').strip())

    def ping(self):
        """快捷 ping"""
        return self.call("ping", timeout=3)