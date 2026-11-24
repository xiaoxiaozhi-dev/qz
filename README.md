# Qz Tools

Qz Tools 是面向 Maya、3ds Max 和 Blender 的桌面工具中台。各软件独立连接；插件卡片在中台显示，场景操作通过对应软件的桥接执行。产品版本读取根目录 `version.json`。

支持跨软件发送选中物体、批量重命名、蒙皮权重检查与交换，以及添加用户脚本。功能入口按宿主分组。

## 快速开始

- **使用 Windows 程序包**：获取发布版 ZIP 后完整解压，双击 `QzTools.exe`。已有发布版可在设置中选择「从更新包更新…」。
- **从源码运行**：按下方「运行与测试」安装 Python 依赖并启动。
- **连接宿主**：在中台的桥接设置中复制启动代码，到对应软件执行。详见[桥接连接说明](docs/bridge-connections.md)。

## 开发目录

```text
qz/
├─ main.py / paths.py / dcc_bridge.py
├─ build.py / build.bat          构建入口
├─ version.json                 唯一产品版本源
├─ defaults.json                官方默认配置
├─ requirements*.txt
├─ ui/                          中台界面和公共计算
├─ plugins/                     官方插件
├─ maya_bridge/ / maya_actions/
├─ max_bridge/ / max_actions/
├─ blender_bridge/ / blender_actions/
├─ maintenance/                 版本、配置、发布清单与更新核心
├─ tools/                       开发清理、版本设置和更新助手入口
├─ tests/ / docs/
├─ memory/                      本地开发笔记，从 MEMORY.md 查阅，不入 Git 或发布包
├─ user_plugins/                用户脚本，保留且不入 Git
├─ data/                        个人设置、布局、主题、日志，不入 Git
├─ temp/ / backups/             用户权重、交换文件、备份，不入 Git
├─ build/                       截图、构建日志、spec、测试证据，不入 Git
└─ dist/                        发布目录和更新包，不入 Git
```

开发截图统一放在 `build/test-artifacts/`，不得输出到根目录或 tests/。缓存清理工具先预览再执行，保留虚拟环境、用户资产、备份及已有测试证据。

## 运行与测试

在 Windows 上用 Python 3.11 创建项目虚拟环境并安装依赖。构建依赖见 `requirements-build.txt`。启动后可在中台的桥接设置里复制对应宿主的连接代码。

```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -B main.py
.venv/Scripts/python.exe -B run_tests.py
.venv/Scripts/python.exe -B run_tests.py release_pipeline -v
```

测试入口为每个测试文件提供临时个人设置目录。宿主原生测试需要安装对应的 Maya、3ds Max 或 Blender，并在独立进程中运行。

## 打包与更新

双击 `build.bat`；自动化可执行 `build.bat --no-pause`。流程构建两个 exe、复制官方资源、运行产物自检，最后生成：

- `dist/QzTools/`：完整安装目录，用户双击其中的 `QzTools.exe`。
- `dist/releases/QzTools-<版本>-windows-x64.zip`：完整离线更新包。
- 同目录的 SHA256 文件和 `latest.json`：发布信息。
- `build/logs/`、`build/reports/release.json`：构建日志与来源记录。

`QzTools.exe` 保持 onedir；`QzUpdater.exe` 是不依赖中台运行库的独立助手。设置页“从更新包更新…”在发布版启用；也可关闭中台后双击更新助手。更新只替换官方清单文件，保留 data/、user_plugins/、temp/、backups/。暂未接在线发布站点或自动下载。

官方插件只提供“打开插件位置”；用户自定义插件继续支持改名、删除与固定到宿主工具栏。当前插件业务仍有部分打包在中台内，首版统一更新、重启中台，不宣称插件界面热更新。

## 设置与兼容

官方默认配置在 `defaults.json`，个人覆盖在 `data/settings.json`，布局与主题分别为 `data/layout.json`、`data/theme.json`。首次启动复制旧 `config.json` 和 `~/.qz-tool/` 配置，原文件不改、不删；新目录已有设置时不覆盖。用户脚本按宿主存放在 `user_plugins/` 下。

Maya/Max 启动入口与固定按钮依赖项目的固定路径，更新保留原桥接和动作目录位置。更新中台不会强制关闭 Maya/Max；桥接核心变化需按发布说明重新启动桥接或宿主。Max2020/Python2.7 为当前必须兼容环境，不限制其他版本。

文档：[构建操作](docs/build-guide.md)、[打包与更新](docs/packaging.md)、[配置](docs/config.md)、[开发与版本管理](docs/development.md)、[用户脚本](docs/user-scripts.md)、[Maya 原生测试](docs/maya-native-tests.md)。
