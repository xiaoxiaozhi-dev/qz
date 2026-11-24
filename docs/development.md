# 开发目录与版本管理

源码、测试和正式文档进入 Git；本地开发笔记、构建缓存、发布包、用户脚本、个人设置、交换数据和备份不进入 Git。

| 位置 | 用途 |
|---|---|
| 根目录源码、version.json、defaults.json、CHANGELOG.md | 应跟随版本控制 |
| ui/、plugins/、Maya／Max／Blender 的 actions 和 bridge、maintenance/、tools/ | 产品和开发工具源码 |
| tests/、docs/、requirements*.txt、.gitattributes、.gitignore | 测试、文档与构建约定 |
| memory/ | 本地开发笔记，入口为 memory/MEMORY.md；实际项目约定以 docs/ 为准 |
| build/ | spec、图标、构建日志、测试截图、原生证据、历史构建 |
| dist/ | 发布目录与更新包 |
| data/、user_plugins/、temp/、backups/、updates/ | 本地设置、用户资产或运行记录 |

不在根目录生成 test_screenshot.png、.spec 或调试日志。两个旧截图测试已改为输出 build/test-artifacts。新增测试不要依赖用户真实布局、主题或现有 Maya/Max 场景；run_tests.py 会为每个文件设置独立 QZ_DATA_DIR。

Git 和默认源码搜索都跳过根目录的 `memory/`；直接指定该目录仍可查阅。历史笔记保留当时的文件路径和结论。需要跟随代码发布的说明写在 `docs/`，产品变更写入 `CHANGELOG.md`。

## 清理

```powershell
.venv/Scripts/python.exe -B tools/clean_development.py
.venv/Scripts/python.exe -B tools/clean_development.py --apply
```

先预览并核对清单，再执行。工具只删除源码范围内的 Python 缓存、已知测试截图和工具缓存；跳过虚拟环境、Git、本地记忆、用户数据、备份、build/、dist/。不按扩展名删除 spec，旧根目录构建配置保留到 build/specs/QzTools-legacy.spec。历史 build 测试证据未批量删除。

## 版本与 Git

- 产品版本只修改 version.json，可用 tools/set_version.py；窗口和发布信息自动同步。
- CHANGELOG.md 记录待发布和正式发布内容。
- .gitattributes 规定 Python/JSON/Markdown 为 LF，bat 为 CRLF，图片和压缩包按二进制处理。
- 构建报告记录 Git HEAD 与 dirty 状态，方便追溯来源；没有自动提交、暂存或创建标签。
- 正式发布前检查 git diff，选取本次已审阅文件提交，通过验证后再为对应提交建立与产品版本一致的标签。
- 公开仓库只收录有权公开的测试样本。Windows 程序包单独发布，发布前核对版本标签、构建来源和 SHA256。
