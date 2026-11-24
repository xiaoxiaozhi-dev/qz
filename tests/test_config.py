"""
配置读取测试（config.json）。

为什么单独测：
    2026-10-19 从 config.yaml 迁到 config.json，动机是**去掉 PyYAML 依赖**
    （全项目 11 个配置文件里只有 1 个是 YAML，不值当）。
    迁移的真正风险不是「读不读得到」，而是**读不到时不报错**：
    `_load_config()` 任何异常都吞掉返回 {}，所有设置静默回落默认值 ——
    界面上看不出任何异常，只有"我明明改了端口怎么没生效"。
    所以这里重点覆盖「坏输入不炸 + BOM 能读 + 删文件也不炸」。

跑法：
    .venv/Scripts/python.exe test_config.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ui"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[ok]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if detail and not cond else ""))


def main():
    # ── 1. 真的不再依赖 yaml ──
    print("1. 依赖干净度")
    import ui.main_window as mw
    src = io.open(mw.__file__, encoding="utf-8").read()
    check("★ main_window 不再 import yaml", "import yaml" not in src)
    check("★ 改用标准库 json", "import json" in src)
    check("★ 用户配置名是 settings.json", mw.CONFIG_FILE == "settings.json",
          mw.CONFIG_FILE)
    check("★ config.yaml 已删除",
          not os.path.isfile(os.path.join(HERE, "config.yaml")))
    # ⚠️ 只看**依赖行**（非注释、非空行）。
    #    我们故意在注释里写了「不需要 PyYAML」来说明为什么去掉它，
    #    所以不能全文搜 "pyyaml"，否则自己的说明会被自己判为失败。
    req_lines = [l.strip().lower() for l in
                 io.open(os.path.join(HERE, "requirements.txt"),
                         encoding="utf-8").read().splitlines()]
    deps = [l for l in req_lines
            if l and not l.startswith("#")]
    check("★ requirements 里没有 pyyaml 依赖",
          not any("pyyaml" in l or l.startswith("pyyaml") for l in deps),
          deps)
    check("★ PySide6 还在", any(l.startswith("pyside6") for l in deps), deps)

    # ── 2. 真实 config.json 能读出四个字段 ──
    print("\n2. 真实 config.json")
    cfg = mw._load_config()
    check("读到 bridge 段", isinstance(cfg.get("bridge"), dict), cfg)
    check("★ 没有遗留的 dcc 字段（双 DCC 改造后已删除）",
          "dcc" not in cfg.get("bridge", {}), cfg.get("bridge"))
    check("★ 没有遗留的 port 单数字段",
          "port" not in cfg.get("bridge", {}), cfg.get("bridge"))
    ports = cfg.get("bridge", {}).get("ports", {})
    check("★ ports 包含 Maya、Max 和 Blender",
          set(ports) == {"maya", "max", "blender"}, ports)
    check("★ 端口值是 int（不是字符串）",
          isinstance(ports.get("maya"), int)
          and isinstance(ports.get("max"), int)
          and isinstance(ports.get("blender"), int), ports)
    check("★ 端口号正确", ports.get("maya") == 17023
          and ports.get("max") == 17024 and ports.get("blender") == 17025, ports)
    check("timeout 是 int", isinstance(cfg["bridge"].get("timeout"), int),
          cfg["bridge"].get("timeout"))
    check("interval 是 int", isinstance(cfg["bridge"].get("interval"), int),
          cfg["bridge"].get("interval"))

    # 消费端能不能真的拿到值（main_window 的读法）
    bridge_cfg = cfg.get("bridge", {}) or {}
    check("★ 消费端读法可用：host",
          bridge_cfg.get("host", "127.0.0.1") == "127.0.0.1")
    check("★ 消费端读法可用：interval",
          int(cfg.get("bridge", {}).get("interval", 10)) == 10)

    # ── 3. BOM / 坏输入 / 缺文件都不炸 ──
    print("\n3. 坏输入静默降级（关键：不能抛异常打断启动）")
    tmp = tempfile.mkdtemp(prefix="qz_cfg_")
    try:
        bad = os.path.join(tmp, "config.json")

        # BOM —— Windows 记事本存 JSON 的默认行为
        with io.open(bad, "w", encoding="utf-8-sig") as f:
            f.write('{"bridge": {"ports": {"maya": 1, "max": 2}}}')
        got = mw._load_config(bad)
        check("★ 带 BOM 的 JSON 能读出来（utf-8-sig）",
              got.get("bridge", {}).get("ports", {}).get("max") == 2, got)

        # 空文件
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write("")
        check("★ 空文件回落 {}（不是 None，也不是抛异常）",
              mw._load_config(bad) == {}, mw._load_config(bad))

        # 只含空白
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write("   \n\n  ")
        check("★ 纯空白回落 {}", mw._load_config(bad) == {})

        # 截断的 JSON —— 必须吞掉，不能打断启动
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write('{"bridge": {"ports": {"maya": 1,')
        check("★ 截断的 JSON 静默回落 {}（不抛异常）",
              mw._load_config(bad) == {})

        # 语法完全错
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write("这不是 JSON")
        check("★ 非 JSON 内容静默回落 {}", mw._load_config(bad) == {})

        # 文件缺失
        os.remove(bad)
        check("★ 文件缺失回落 {}（不抛异常）",
              mw._load_config(bad) == {})

        # 路径指向目录（少见但也不能炸）
        check("★ 路径是目录时回落 {}",
              mw._load_config(tmp) == {})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 4. 文档已建，注释不再塞在配置里 ──
    print("\n4. 注释外置")
    doc = os.path.join(HERE, "docs", "config.md")
    check("docs/config.md 存在", os.path.isfile(doc))
    if os.path.isfile(doc):
        d = io.open(doc, encoding="utf-8").read()
        check("文档解释了为什么去掉 YAML", "为什么" in d and "JSON" in d)
        check("★ 架构关键点写进文档了（不需要指定当前连哪个）",
              "不需要指定当前连哪个" in d)
        check("★ 端口冲突限制写进文档了",
              "同时只能开一个" in d)

    print("\n" + "=" * 58)
    print("PASS %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAIL: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
