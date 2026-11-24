# 构建与发布操作

## 构建

项目统一使用 `.venv/Scripts/python.exe`。依赖由 `requirements.txt` 和 `requirements-build.txt` 管理，不另选系统 Python。

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
build.bat
```

不需要每次重新安装依赖。自动化使用 `build.bat --no-pause`，退出码保留构建结果；也可直接运行 `.venv/Scripts/python.exe -B build.py`。批处理保持 ASCII 和 CRLF，以兼容 Windows 命令解释器和带空格的项目路径。

## 版本

`version.json` 是唯一产品版本源，窗口标题、设置页、发布清单和更新包名称均读取它。本轮保持既有 0.1.0，不自动增加版本号。

```powershell
.venv/Scripts/python.exe -B tools/set_version.py
.venv/Scripts/python.exe -B tools/set_version.py 0.1.1
```

正式发布前整理 CHANGELOG.md、检查源码差异并完成测试。版本工具不自动提交或打 Git 标签。构建报告记录当前 Git 提交和工作区是否有未提交修改，不能把 dirty 构建误称为已提交源码快照。

## 自动流程

1. 主程序以 onedir 构建到 `build/staging/QzTools/`。
2. 更新助手以 onefile 单独构建，不依赖主程序的 Qt/NumPy 运行库。
3. 复制官方插件、桥接、动作、默认配置、版本及完整资源目录（包括 controls/ 图标）。不复制用户数据、本地开发笔记 `memory/`、.pyc 或 __pycache__。
4. 创建带文件大小和 SHA256 的 release-manifest.json。
5. 运行更新助手自检、真实中台 exe 的 --selftest、源码侧分发检查。各步骤失败返回非零，不继续发布。
6. 通过后将暂存目录提升为 dist/QzTools，生成完整更新 ZIP、SHA256 和 latest.json。

生成的 spec 位于 `build/specs/`，PyInstaller 工作目录和缓存位于 build/。上一份官方构建保留到 build/previous-dist。若 dist/QzTools 已含个人设置、用户插件或运行数据，构建会停止并保留原目录，不将其当作可清理产物。

## 产物

```text
dist/
├─ QzTools/
│  ├─ QzTools.exe
│  ├─ QzUpdater.exe
│  ├─ _internal/
│  ├─ defaults.json
│  ├─ version.json
│  ├─ release-manifest.json
│  ├─ plugins/
│  ├─ maya_bridge/ / maya_actions/
│  ├─ max_bridge/ / max_actions/
│  └─ ui/resources/
└─ releases/
   ├─ QzTools-<版本>-windows-x64.zip
   ├─ QzTools-<版本>-windows-x64.zip.sha256
   └─ latest.json
```

首次交付发送整个 QzTools 目录，或将更新 ZIP 解压到一个固定的空目录。不要只发主 exe。用户运行后会自行生成 data/、user_plugins/、temp/ 等目录，这些不属于官方更新内容。

后续交付发送更新 ZIP，用户从中台设置页选择更新包；也可以先关闭中台，再运行 QzUpdater.exe 选择 ZIP。来源和使用方式见 [packaging.md](packaging.md)。

## 日志与验证

- 构建及检查：`build/logs/build-app.log`、`build-updater.log`、`updater-selftest.log`、`app-selftest.log`、`dist-check.log`。
- 发布摘要：`build/reports/release.json`。
- 中台 --selftest 的完整 UTF8 报告：个人设置目录下 logs/selftest.log；构建时使用 build/test-data 内的隔离设置目录，不污染发布包或个人设置。

自检不会操作用户现有 Maya/Max 场景。构建通过不等于所有 DCC 插件已在每种宿主版本验证，原生专项见各插件文档。

相关专项：
```powershell
.venv/Scripts/python.exe -B run_tests.py release_pipeline -v
.venv/Scripts/python.exe -B run_tests.py update_ui -v
.venv/Scripts/python.exe -B tools/verify_packaged_update.py
```

最后一项仅复制发布产物到 build/test-artifacts 的临时安装目录，验证真实更新助手的成功更新、等待进程退出、自检失败恢复和用户文件保留，不更新开发目录或用户正在使用的安装。
