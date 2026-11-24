# 工作目录约定

完整目录见 [README](../README.md)，文件归属、清理和版本管理见 [开发目录与版本管理](development.md)。

- **开发源码**位于项目根目录、`ui/`、`plugins/` 和 Maya、Max、Blender 各自的 `*_bridge/`、`*_actions/`。官方默认配置在 `defaults.json`，个人覆盖在 `data/settings.json`。
- **本地笔记**放在根目录的 `memory/`，入口是 `memory/MEMORY.md`。Git、默认源码搜索、缓存清理和发布都跳过该目录。正式项目约定和协作说明维护在 `docs/`。
- **构建与验证产物**放在 `build/`，发布安装目录和更新包在 `dist/`。默认搜索跳过这些目录，避免混淆外置源码副本和实际开发文件。测试截图写入 `build/test-artifacts/`。
- **用户内容**在 `data/`、`user_plugins/`、`temp/` 和 `backups/`。清理或重建项目时保留这些文件。

中台运行时的项目路径统一由 `paths.py` 解析。开发时以源码目录为根，打包后以 exe 所在目录为根；外置资源不要用打包模块的 `__file__` 定位。给外置插件新增中台侧依赖时，同步核对 `build.py` 的 PyInstaller 导入清单。

日常使用项目自己的 Python：

```powershell
.venv/Scripts/python.exe -B main.py
.venv/Scripts/python.exe -B run_tests.py
.venv/Scripts/python.exe -B run_tests.py zorder -v
```

当前 Windows 开发环境使用 Python 3.11。迁移电脑时，先保留旧的 `.venv` 以便回退，再在新电脑用对应版本重建；旧环境的解释器路径可能仍指向原用户目录。运行依赖见 `requirements.txt`，构建依赖见 `requirements-build.txt`，具体命令见 [构建与发布操作](build-guide.md)。

测试放在 `tests/`。失败必须返回非零退出码；交互问题用实际 Qt 事件覆盖。Maya 和 Max 原生测试用隔离进程和测试场景。打包通过 `build.bat` 执行，脚本保留 ASCII 和 CRLF。
