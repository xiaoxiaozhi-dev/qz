"""
自动导入所有 action 模块，触发 @register_action 装饰
"""

import os
import glob

_current_dir = os.path.dirname(__file__)
for f in glob.glob(os.path.join(_current_dir, "*.py")):
    name = os.path.basename(f)
    if name.startswith("_") or name == "__init__.py":
        continue
    module_name = name[:-3]
    __import__(f"maya_actions.{module_name}", fromlist=[""])