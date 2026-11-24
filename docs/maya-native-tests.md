# Maya 原生测试

本机 Maya2024 的 `mayapy.exe` 加载 `maya.standalone` 时会在 mayaLib0.dll 内访问冲突；此前直接运行 mayabatch.exe 也失败。已验证 `maya.exe -batch` 可以正常启动并执行 Maya 命令，后续原生验证优先使用此入口，无需修改安装 DLL。

## 运行

从项目根目录执行：

```powershell
.venv/Scripts/python.exe tests/run_maya_native.py tests/maya_user_scripts_integration.py
```

可通过 `--maya` 指定其他 Maya 主程序路径，通过 `--timeout` 设置 1～60 秒的超时。默认 Maya2024 安装在 `C:/Program Files/Autodesk/Maya2024/`。

真实工具架等UI命令需要使用 `--gui`，例如 `tests/run_maya_native.py tests/maya_user_shelf_integration.py --gui`。该模式仍新建独立配置和进程，在GUI完成初始化后延迟执行fixture；报告batch=false，测试按运行器令牌及进程身份校验，不能连接现有交互场景。普通场景测试默认仍为batch，原有调用不变。

当前环境的独立GUI模式在功能报告写出后退出会挂起，空GUI对照也能复现；60秒截止只清理该次新建进程。此时即使result.ok=true，launch.ok仍为false，应分别报告功能断言通过与宿主退出未通过，不宣称整个运行器成功。工具架验证细节见 [user-scripts.md](user-scripts.md)。

每次测试创建一个独立的 Maya 批处理进程。临时配置位于本次结果目录的 profile/，设置 MAYA_APP_DIR、跳过 userSetup.py、禁止插件自动加载，不读取原有 Maya 用户配置，不连接中台桥接，也不访问现有交互 Maya 场景。启动参数和配置隔离依据 [Autodesk 命令行说明](https://help.autodesk.com/cloudhelp/2023/ENU/Maya-GettingStarted/files/GUID-2E5D1D43-DC3D-4CB2-9A35-757598220F22.htm)及[环境路径说明](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-EnvVar/files/GUID-228CCA33-4AFE-4380-8C3D-18D23F7EAC72.htm)。

## 测试脚本约定

该入口适用于提供 `run()` 函数并返回 `dict(ok=True, ...)` 的独立场景测试。由主程序提前初始化 Maya，运行器加载脚本后调用 run()；测试函数内无需调用 maya.standalone.initialize() 或退出 Maya。测试需检查 `cmds.about(batch=True)`，只在新进程中创建可丢弃的场景和临时数据。带额外输入要求的测试仍需事先配置对应 QZ 环境变量；不自动遍历执行所有测试。

直接依赖 unittest 的 `__main__`、依赖交互 UI 或需要单独命令行参数的脚本，需适配 run() 后使用，不应直接混入该入口。

## 结果

输出目录为 `build/maya-native/<测试名>-<本次编号>/`：

- result.json：真实 Maya 版本、批处理状态、进程编号及测试结果。
- launch.json：退出码、耗时、超时状态及汇总。
- process.log：启动和退出输出。
- profile/、worker.py、worker.mel：本次独立配置与执行入口。

只有测试进程正常退出、报告成功、批处理状态正确、报告中的进程编号和本次标识匹配时才判定通过。没有生成结果文件时，即使启动器返回 0 也不算成功。超时只结束本次新建的测试进程及子进程。

2026-11-12 用户脚本原生验证通过，结果位于 `build/maya-native/maya_user_scripts_integration-66cd5bc7/`，整次约 5.7 秒，退出码 0。覆盖 Python 文件执行、函数入口、MEL source/过程调用、中文插件路径、粘贴圆锥代码真实创建 21 顶点模型（保存不执行），以及插件文件副本删除。未验证交互式 Maya UI 的窗口回调或完整中台网络链路。
