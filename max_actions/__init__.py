# -*- coding: utf-8 -*-
"""
max_actions/__init__.py
自动导入所有 action 模块，触发 @register_action 装饰器。

与 maya_actions 同构。注意：max_actions/*.py 跑在 Max 进程的主线程里，
可以 import pymxs；UI 按宿主延迟加载 PySide2/PySide6，不能使用中台的 Qt 运行库。
"""

import glob
import os

_current_dir = os.path.dirname(__file__)

for _f in glob.glob(os.path.join(_current_dir, "*.py")):
    _name = os.path.basename(_f)
    if _name.startswith("_") or _name == "__init__.py":
        continue
    _module = _name[:-3]
    __import__("max_actions.%s" % _module, fromlist=[""])
