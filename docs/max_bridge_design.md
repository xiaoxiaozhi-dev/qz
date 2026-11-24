# 3ds Max 接入设计（实测结论版）

> 本文档的所有结论都来自在 3ds Max 2027 build 3590 上的实机探测，
> 不是文档推断。探测共 9 轮，前 8 轮的「结论」全部作废 —— 原因见文末复盘。

---

## 一、实测环境

| 项 | 值 |
|---|---|
| Max 版本 | 2027 build 3590（`#(29000, 70, 0, 29, 0, 0, 3590, 2027, "")`） |
| Python | 3.13.9（Max 内置） |
| pymxs | 只能 Max 进程内 import |
| 端口 | `17024`（Maya 用 17023，按 DCC 区分） |

---

## 二、四条硬约束（实测，不可违反）

### 1. pymxs 只能在主线程调用
子线程写操作被硬拒：
```
RuntimeError: MAXScript call is only allowed on Python main thread
```

### 2. 后台线程调 pymxs 会破坏 Max 内部状态
不只是抛异常，还会污染通知机制：
```
-- Error: DirtyNotificationEventMonitor call depth count < 0,
          DirtyNotificationEventMonitor terminated
```
**推论：socket 线程只能碰 socket 和 JSON，绝不能 import/调用 pymxs。**

### 3. Max 空闲时确实会正常泵消息循环 —— 但前提是主线程没被占住
这是八轮探测翻车的唯一原因，单独强调：
> **不要在主线程 `sleep` 等结果。** Max 的主循环是 C++ 代码、就跑在 Python
> 主线程上，主线程卡在 Python 里，Max 就没机会泵消息。
> 等待与统计必须放后台线程，且主线程要**立即返回**。

### 4. 定时器本身完全正常
两条路都实测通过，频率都是 ~9.1 次/秒（interval=100ms，期望 10），
回调线程均为 `MainThread`：

| 方案 | tick 次数 | 频率 | 线程 | 备注 |
|---|---|---|---|---|
| **A. MAXScript rollout timer** | 75 / 8.11s | 9.1/s | MainThread ✅ | 推荐 |
| B. Win32 SetTimer + WndProc 子类化 | 74 | 9.1/s | MainThread ✅ | 备份 |

---

## 三、最终架构（已定）

```
中台 (PySide6, 独立进程)
    │  socket 127.0.0.1:17024
    ▼
Max 进程
├─ socket server 线程        accept/send/recv + JSON 解析   【绝不碰 pymxs】
├─ queue.Queue               请求入队
└─ MAXScript rollout timer   主线程 tick，interval 100ms
    └─ python.ExecuteFile(max_bridge/worker.py)
         └─ worker 从队列取任务 → pymxs 执行      （主线程，操作合法）
```

**与 Maya 侧同构**，唯一差别是调度原语：

| | Maya | Max |
|---|---|---|
| 调度原语 | `maya.utils.executeInMainThreadWithResult` | MAXScript rollout timer |
| 端口 | 17023 | 17024 |
| action 表 | `maya_bridge/server.ACTIONS` | `max_bridge/server.ACTIONS` |

---

## 四、目录结构

```
qz/
├─ maya_bridge/server.py      # Maya 侧 socket（已有）
├─ maya_actions/*.py          # Maya 侧 action（已有）
├─ max_bridge/
│  ├─ server.py               # socket 线程 + ACTIONS 表 + 队列
│  ├─ worker.py               # 被 MAXScript timer 拉起，跑在主线程
│  └─ bootstrap.ms            # 建 rollout timer 的 MAXScript 启动代码
├─ max_actions/*.py           # Max 侧 action
├─ plugins/<id>/manifest.json # dcc: maya / max / both
└─ ui/maya_setup.py           # Maya 侧安装脚本（max 侧对应物待写）
```

---

## 五、MAXScript 主线程定时器

以 `max_bridge/bootstrap.ms` 为唯一实现，由 `start_max.py` 替换路径后执行。
startup脚本调用`start_max.py`，不要另行复制旧版不带异常保护的timer示例。

每次tick先设busy并停用timer，成功返回后才恢复；MAXScript层捕获ExecuteFile的
原生异常，或发现非success结果后保持停用，只向Listener输出一次错误。
worker同时用持久状态阻止嵌套消费队列，并保存Python错误完整堆栈。

ExecuteFile失败分支另外向`@FAILURE@`写本进程专用标记，由socket线程每0.1秒检查；
因此原生异常绕过Python时，等待请求也能返回失败，而不只停timer后让请求等待300秒。
故障结果包含导入动作最后记录的API阶段；这不是分阶段耗时日志。待执行旧任务在
超时或重启后作废。启动时渲染标记路径并清理旧状态，旧核心需重启Max加载。

要点：
- `timer` 控件**必须带 name 参数**（`timer tmrTick "qzClock"`），否则语法错误
- tick 处理器写成独立的 `on tmrTick tick do (...)`，不能内联
- `@WORKER@` 替换为 `worker.py` 的绝对路径（正斜杠）
- 窗口用 `#style_no_caption` 做成不可见，避免打扰用户

---

## 六、已踩过的 MAXScript / ctypes 坑（存档）

### MAXScript
- ⚠️ **bootstrap 类 .ms 必须纯 ASCII**。MAXScript / Listener 不能可靠处理 UTF-8 源码，
  非 ASCII 字符在报错里会变成乱码
- ⚠️ **没有隐式字符串续行**。把一次调用拆成两行、第二行以 `+` 开头 → 
  `语法错误: 位于 +，需要 <因子>`。不要在 .ms 里拼多行字符串
- ⚠️ **`try` / `catch` 必须是两个独立的块**：
  ```
  try ( ... ) catch ( ... )          -- 对
  try(...) catch()                   -- 语法错误
  ```
- `rollout` 声明放脚本顶层，不要嵌在 `if` / `else` 块里
- **`timer` 控件必须带 name 参数**：`timer tmrTick "qzClock" interval:100`。
  漏掉 name 会报「位于 keyword parameter，需要 name」，timer 根本建不起来
- **tick 处理器要写成独立的 `on tmrTick tick do (...)`**，不能内联
- 对话框要 `#style_no_caption + #style_toolwindow`，但**光靠样式去不掉那个小框**。
  MAXScript **没有 `#style_hidden`**，这两个样式在 2027 上仍会在视口留一个 1×1 透明边框框。
  ★ **唯一的可靠做法是 `start_max.py` 里用 Win32 `ShowWindow(hwnd, SW_HIDE)`**
  （`FindWindowW(None, "Qz Bridge")` 找句柄）。实测隐藏后 worker ticks 继续增长，
  58 个任务全处理、0 错误 —— **隐藏不影响 timer**
  - ⚠️ **绝不能用 `destroyDialog` 来"隐藏"** → 它会停 timer，bridge 立刻死
  - ⚠️ **`rollout.size` 属性不存在**（2027 报「未知属性:"size"」）。赋值抛异常会
    **中断 bootstrap 后续所有语句**；再叠加 `start()` 在 except 里提前 `return`，
    `ShowWindow` 从未执行 → 「报了个错 + 小框还在」是**同一个 bug 的两个现象**
  - ⚠️ **`rollout.pos = [-32000,-32000]` 无效**，Max 会把窗口拉回可见区域（实测）
  - 因此 `start()` 里隐藏调用**必须放在 bootstrap 的 except 分支之后、return 之前**，
    且 bootstrap 失败时也要执行一次（此时 `CreateDialog` 已成功，窗口已存在）
- ⚠️ **pymxs 没有 `python.UpdateSysPath`**（我凭空编的，被 Max 打脸两次）。
  而且也**不需要** —— `start_max.py` 在执行 .ms 之前已经把项目根加进 sys.path，
  worker 的 `import max_bridge.server` 自然能解析
- ⚠️ 别在 MAXScript 字符串里嵌多行 Python 源码：换行、转义、编码三重脆弱。
  `bootstrap.ms` 只做纯 MAXScript 的事（建 rollout + timer），
  所有 Python 侧逻辑（sys.path、起 server、加载 action）都由 `start_max.py` 负责
- 💡 这些坑高度可枚举，已固化成 `test_max_ms.py`（27 项静态校验）。
  **改完 .ms 先跑它，别开 Max 试错** —— MAXScript 的报错几乎不给上下文
| 坑 | 正确写法 |
|---|---|
| `timer new:...` | 2020+ 已废弃，用 rollout 里的 `timer` 控件 |
| `timer` 缺 name | `timer tmrTick "name" interval:100`（name 必填） |
| tick 内联写法 | `on tmrTick tick do (...)` 独立块 |
| `callbacks #timer` / `#Timer` / `#idle` | **全部非法**。合法 callbacks 仅 4 个：`#preRender` `#postRender` `#filePreOpenProcess` `#filePostOpenProcess`，均非周期性，不可用作调度器 |
| `openFile mode:"append"` | 非法字符串，会抛异常。MAXScript 用符号 `mode:#append` |
| `maxGetFileVersion()[2] + ' '` | 整数与字符串 `+` 报类型错误 |
| `python.QzModule.fn()` | `Interface:python` 查找不到注入的模块，**改用 `python.ExecuteFile`** |

### ctypes（Win32 调用三条铁律）
1. 指针参数**必须声明 `argtypes`**，否则 64 位下 `OverflowError: int too long to convert`
2. `argtypes` 声明为 WINFUNCTYPE 后，第三参数要传**回调对象本身**，
   传 `ctypes.cast(cb, c_void_p)` 会报 `expected WinFunctionType instance`
3. `restype` 用 `c_ulonglong` 承接指针返回值，返回 0 即失败

### Python
- ⚠️ **`print` 和 `pymxs.runtime.format` 在 Max 里都落到 Listener** ——
  两个都写会让每条日志出现**两遍**。只走 `pymxs.runtime.format`
- ⚠️ **Windows 上 `SO_REUSEADDR` 会静默抢占已 listen 的端口**。
  bind 成功、listen 成功、日志照打 `listening`，但新 socket **根本不服务**，
  新连接全被旧 socket 吃掉 —— **零报错**。
  表现就是「中台永远连不上，但服务端看起来一切正常」。
  实测被一个残留 socket 占住 17024 坑过。
  **两端 server 都用 `SO_EXCLUSIVEADDRUSE`**，端口被占时 bind 直接抛 10048。
  ⚠️ 这两个选项在 Windows 上**互斥**，别试图「先 REUSEADDR 再用 EXCLUSIVE 探测」——
  探测必然失败，会变成永远误报端口被占。
- **socket 排查第一步：`netstat -ano | findstr <端口>`**。
  别先怀疑协议 / 队列 / worker
- `pymxs.runtime.format()` 用 MAXScript 的 `%` 占位符，**不是** Python 的 `{}`
- 嵌套字符串写入外部文件时，里面的 `\'` `\\` 转义会原样落盘，
  必须对子源码单独 `ast.parse` 校验
- ⚠️ **`exec(open(path).read())` 里没有 `__file__`**（跑在 `<string>` 命名空间）。
  `max_bridge/start_max.py` 和 `print_status.py` 都要靠它推导路径，
  直接 `os.path.abspath(__file__)` 会 `NameError: name '__file__' is not defined`。
  - **粘贴用的 snippet 使用 `runpy.run_path(..., run_name="__main__")`**，设置真实 `__file__` 并调用脚本入口，兼容 Python 2.7 / 3。旧方案直接依赖 `importlib.util`，不支持 2.7，已于 2026-11-05 替换。
  - 脚本内部再加一层兜底 `try: __file__ except NameError:` → 退而扫 `sys.path` 找 `max_bridge/`
  - `python.ExecuteFile` 同样不可靠地给 `__file__`，所以 `print_status.py` 也做了兜底
  - 规律：**只要脚本可能被"当文本执行"，就不能裸用 `__file__`**，应使用明确设置它的标准执行入口，或自己兜底。

---

## 七、九轮探测复盘（为什么前八轮全错）

| 轮次 | 测出「结论」 | 实际原因 |
|---|---|---|
| v1 | QTimer 超时 | 断言只查异常不查超时 + 主线程 `done.wait()` 阻塞 |
| v2 | 写操作被线程拒 | ✅ 这条是对的 |
| v3 | timer 不 tick | 写 C:/ 根目录 PermissionError，脚本崩了 |
| v4 | T1 timer 不 tick | **timer 控件缺 name，压根没建起来** |
| v4 | T5 QTimer 19 次 tick | **假阳性** —— 手动 `processEvents()` 转出来的 |
| v5 | QTimer 不自发 tick | **主线程 sleep 堵死消息循环** |
| v5/v6 | rollout timer 不 tick | `openFile mode:"append"` 抛异常打断 tick（堆栈里 `gP6Tick:1` 就是证据，当时读错了重点） |
| v6/v7 | Win32 SetTimer 0 回调 | ctypes `argtypes` 未声明 → 子类化失败 |
| v7 | A/B 全 0 | **主线程 sleep 堵死消息循环**（SetTimer 挂在 Max 自己的窗口上都收不到消息，是铁证） |
| **v8** | **ticks: 4254** | ✅ **方法论修正后，timer 正常** |
| **v9** | **A/B 双通过，9.1 次/秒，主线程** | ✅ **结案** |

**核心教训**：探测「对方进程的事件循环是否运转」时，
**绝不能在主线程 sleep 等结果**。等待必须放后台线程 + 主线程提前返回。
在此之前所有「定时器不 tick」的结论都不可信 —— 它们测的是我的阻塞，不是 Max 的行为。

---

## 八、实现状态（已完成）
```
qz/
├─ dcc_bridge.py              DCC 无关客户端（DccClient，向后兼容 MayaClient）
├─ max_setup.py               Max startup 脚本管理（对应 ui/maya_setup.py）
├─ max_bridge/
│  ├─ server.py               socket 线程 + ACTIONS + 队列 + drain()
│  ├─ worker.py               主线程消费者，被 timer 拉起
│  ├─ bootstrap.ms            建 rollout timer 的 MAXScript
│  ├─ start_max.py            Max 里一键启动入口
│  └─ print_status.py         排障：打印全部状态
├─ max_actions/
│  ├─ ping.py                 连通性 + 线程自证
│  ├─ create_box.py           建 Box
│  ├─ move_selected.py        移动选中（与 Maya 同名同参）
│  └─ reload.py               重载动作库
├─ test_max_bridge.py         架构本地验证（18 项）
└─ test_max_setup.py          startup 脚本管理验证（18 项）
```

### 跨线程协议
```
socket 线程                          主线程（worker）
──────────                          ──────────────
recv → JSON 解析
_Task 入队        ──────────────▶   drain() 取队列
                                       dispatch() → pymxs
submit() 等 Event ◀──────────────   task.set_result()
send JSON
```
- socket 线程**只**碰 socket / JSON / queue，代码里没有一处 `import pymxs`
- `REQUEST_TIMEOUT`（默认 10s）到期返回明确错误，不会永久挂死

### 中台侧改动
- `config.json`：`bridge.dcc` + `bridge.ports.{maya,max}`
  （注：2026-10-19 配置已从 YAML 迁到 JSON，见 docs/config.md）
- `ui/main_window.py`：按 `dcc` 过滤插件（`plugin_loader.filter_for_dcc`）
- `ui/async_bridge.py`：`AsyncBridge` 接受 `dcc` 参数
- 端口策略：maya 17023 / max 17024，同 DCC 多版本共用端口、先开先得

### 验证结果
```
test_max_bridge.py     18/18   架构、超时、异常隔离、并发 20、reload
test_max_setup.py      18/18   标记块增删改、用户内容零污染、路径正确
test_max_ms.py         17/17   bootstrap.ms 静态校验（MAXScript 语法坑）
test_bridge_client.py  15/15   客户端收发 / 延迟 / 超时 / 双 DCC 并发
test_app_2.py          30/30   中台侧（双桥接、同名共存、v4 持久化）
test_canvas / multiselect  ALL PASS
test_maya_bridge.py    PASS
```

### ⚠️ 桥接协议约定：客户端必须自己判断结束
协议是「一行 JSON + `\n`」，**客户端要 `while b"\n" not in result:`**。

早先两端都写成 `while True: recv()`，等对端 close 才 break：
- Maya 侧因为 handler 每次响应后都 `conn.close()`，侥幸能 break → 一直没人发现
- Max 侧要保持连接等响应，**必然踩满 timeout**：实测 `ping` 耗时 **3.111 秒**
  （而 `ping()` 的 timeout 正好 3 秒）

协议自带结束标记，就不该依赖对端 close —— 那是隐式耦合，
换个服务端实现就炸，且症状不是报错而是「莫名其妙卡满 timeout」。
修完 **3.111s → 0.098s**。这类问题只能靠 `test_bridge_client.py` 里
**断言耗时**来抓，光断言结果永远发现不了。

## 九、怎么在 Max 里用

**临时启动（一次性，先验证）**
1. 中台 → 设置（齿轮）→「连接 3ds Max」分区 → 点「复制启动代码」
2. Max → 脚本编辑器 → 新建 → 粘贴 → 运行（Python）
3. 回中台，桥接区的 Max 行应变为已连接（**Maya 那行不受影响**）

粘贴的代码长这样（注意**不是** `exec(open(...).read())`）：

中台复制的是下面这样的多行代码，请在 Max Python 脚本编辑器中整段运行。
```python
import sys, runpy
sys.path.insert(0, u"C:/path/to/qz")
_qz_start = u"C:/path/to/qz/max_bridge/start_max.py"
if sys.version_info[0] < 3:
    _qz_start = _qz_start.encode(sys.getfilesystemencoding() or "mbcs")
_qz_bridge = runpy.run_path(_qz_start, run_name="__main__")
```
若看到 `NameError: name '__file__' is not defined`，说明贴的是旧版代码 ——
重新从中台复制一次即可。

**常驻（自动启动）**
1. 设置页「安装」→ 写入 Max startup 脚本
2. 重启 Max 即可
3. 「卸载」可完全移除

**排障**
在 Max 主线程跑 `max_bridge/print_status.py`，会打印 socket 状态、
队列深度、action 列表、worker tick 次数。若 `ticks = 0` 说明 timer 没跑起来。
注意这个文件同样不能用 `exec(open(...).read())`（`print_status.py` 已做兜底，
但优先照下面用 runpy）：
```python
import sys, runpy
_path = u"C:/path/to/qz/max_bridge/print_status.py"
if sys.version_info[0] < 3:
    _path = _path.encode(sys.getfilesystemencoding() or "mbcs")
runpy.run_path(_path)
```

若timer报`Unknown exception thrown executing script`，且伴随持续警告声，先在
MAXScript Listener执行`qzBridgeRollout.tmrTick.active = false`并关闭中台，
不要连续重试发送；若仍响，检查隐藏提示窗口，必要时正常保存场景并退出Max。
用户2026-11-06反馈退出Max后声音停止。此堆栈不能确定具体SDK触发点，
防重入和异常停止保护不能被当作已复现并解决原生异常的证明。
重新打开Max后需重新运行临时启动完整代码或自动启动，才能加载新bootstrap；
只重载动作库不会更新已创建的rollout。

### Python 2.7 启动兼容（2026-11-05）

中台自身仍使用 Python 3；只要求复制到 Max 执行的代码、`max_bridge/` 与
`max_actions/` 能在旧版 Max 的 Python 2.7 中运行。`ui/max_setup.py` 的类型注解
和字符串生成代码运行于中台，不应被粘贴到 Max。

临时启动的路径文字使用转义后的 Unicode 字面量。Python 2.7 的 runpy 路径钩子
对非 ASCII Unicode 路径会抛异常，因此在 Max 端用文件系统编码转换后再执行，
启动脚本将 `__file__` 解码回 Unicode，供路径拼接与 MAXScript 文本替换使用。
这仍受 Windows / Python 2.7 文件系统编码本身的字符范围限制。

必要兼容点包括：`max_bridge/__init__.py`、UTF-8 源码声明、移除 Max 端函数类型注解、
`io.open`、`Queue` 回退、可选 `invalidate_caches`、创建线程后设置 `.daemon`。
ping 在动作库加载的主线程保存线程对象，不再调用 2.7 没有的 `threading.main_thread()`。
默认等待仍为10秒，单次传输的 `execution_timeout` 分支保留。

`tests/test_max_start_compat.py` 在 Python 3.11 和本机 Max 2020 的真实 Python
2.7.15 运行库中各通过5项运行时回归。2.7运行库由独立测试进程加载，Max界面/SDK
使用替身；覆盖源码编译、模块/动作注册、重复启动、中文/空格/引号路径、队列/worker
ping，以及默认/自定义等待时间。这不等于已验证真实 Max 的 timer、pymxs 或场景导入。
完整开发回归另外运行，后续从开发中台重新复制临时启动代码；本轮不打包。

2026-11-06 实机补充：Max 2020 的 `python.ExecuteFile` 会将 worker 源码作为
Unicode 交给 Python 2.7。即使字节源码能编译，Unicode 源码若包含编码声明仍会抛出
`SyntaxError: encoding declaration in Unicode string`。因此 `worker.py` 必须保持
纯 ASCII 且**不要加编码声明**；其余经普通 import / runpy 字节加载的 Max 模块继续
保留 UTF-8 声明。回归增加直接将 worker 作为 Unicode 源码 compile/exec 的路径，
不能只用字节文件编译来代替 ExecuteFile 行为。

## 十、中台侧双 DCC 改造（2026-10-18 定稿）

> ⚠️ 本节之前的规划里曾假设「中台全局单选一个 DCC」（`config.dcc` + `filter_for_dcc`）。
> **这个设计已被推翻**，下面是实际落地的方案。

### 需求原话

> 就是在之前那个 maya 中台的基础上加上可连接 max 的功能。可以同时连接一个 maya 和一个 max。
> 插件库不共用，节点树上有个 3dsmax 的分支，插件放里面就好。
> 右侧卡片面板不切换，maya、max 插件可同时打开。

### 关键决策

**1. 根本不存在「切换 DCC」**

`config.json` 里的 `dcc` / `port` 字段已删除，换成：

```json
{
  "bridge": {
    "ports": { "maya": 17023, "max": 17024 }
  }
}
```

中台同时持有两条独立连接，各自心跳、各自连接状态、各自 Reload。
Maya 没开不影响 Max，Max 没开也不影响 Maya。Max 没启动时它的插件卡片照样能打开，
只在真的调用时报错 —— 与 Maya 卡片的行为完全一致。

**2. 唯一键用 `(dcc, id)` 复合键，不用前缀**

maya 和 max 都有 `create_box` / `move_selected`，id 必然重名。
前缀（`max_create_box`）是第三份重复信息（目录名、manifest 的 `dcc` 字段已经各说了一遍），
容易与前两者不一致。复合键把「归属」集中在内部数据结构里，**对外一律裸 id**：

| 位置 | 形式 |
|---|---|
| 目录 | `plugins/max/create_box/` |
| manifest | `"id": "create_box", "dcc": "max"` |
| 树 / 卡片显示 | name |
| 内部键 | `("max", "create_box")` |

复合键贯穿 `plugin_tree` / `plugin_search` / `canvas` / `layout_state`。

**3. 插件库物理隔离**

```
plugins/
├─ maya/  create_box  create_sphere  bone_mirror  move_selected
└─ max/   create_box  move_selected  ping  reload
```

**4. UI 不共用**（用户明确否决了共享 widget.py）

理由：容易搞混，且不利于后期继续加 DCC。
代价：以后改一个通用交互（比如加「批量执行」按钮）要改两个文件 —— 这是隔离换来的，已知并接受。
目前两边 UI 看起来一样，只是因为还在测试阶段；实际业务里同名插件很少。

### 配套改动

| 文件 | 改动 |
|---|---|
| `ui/plugin_loader.py` | 扫两层目录；`PluginInfo` 加 `key` 属性；工厂模块名带 dcc（`plugins.{dcc}_{id}_{module}`，防同名 `widget.py` 撞车）；校验 manifest.dcc 与目录名一致 |
| `ui/canvas.py` | `_proxies` / `_info_by_key` 改复合键；三个节点信号改 `Signal(object)` |
| `ui/plugin_node.py` | `plugin_id()` 返回复合键 |
| `ui/layout_state.py` | schema 升 **v4**，`open_plugins` 项加 `dcc`；v3 记录自动补 `"maya"`，无缝迁移 |
| `ui/main_window.py` | 单例 `_bridge` → `_bridges` / `_clients` 字典；`_make_widget` 按 `info.dcc` 注入对应 client（widget 自身不感知 DCC，签名不变） |
| `ui/settings_panel.py` | 新增「连接 3ds Max」分区（复制代码 / 安装 / 卸载 / Reload）；桥接区改两行 |
| `ui/async_bridge.py` | `ping_async` 带回 dcc，用 `Q_ARG("QString", dcc)` 跨线程 |
| `ui/max_setup.py` | 新增，对应 `maya_setup.py`（原写在项目根，已移入 `ui/` 与 maya 侧对齐） |
| `dcc_bridge.py` | 新增，DCC 无关客户端，`DCC_PORTS = {"maya": 17023, "max": 17024}`；`MayaClient` 保留为兼容别名 |

### PySide6 坑位（复合键相关）

- **`Signal(str)` 不能传 tuple** —— emit 时报 `Shiboken::Conversions::_pythonToCppCopy:
  Cannot copy-convert (tuple) to C++`。信号必须声明成 `Signal(object)` 走 PyObject 通道。
- 但 **`QVariant` 本身能装 Python tuple**（PySide6 会包成 PyObject 变体），
  所以 `setData(UserRole, tuple)` / `setProperty(name, tuple)` 都是安全的。
  实测确认过，别误判成「Qt 一律不支持 tuple」。
- `setProperty` 存复合键虽可行，但走元对象系统有开销，内部记键用普通属性更直接。
- ⚠️ **`@Slot` 的参数列表必须和 `invokeMethod` 的 `Q_ARG` 列表逐个对齐** ——
  签名不匹配时 `QMetaObject.invokeMethod` **静默失败**（返回 False，不抛异常）。
  踩过：`@QtCore.Slot(bool)` 配 `Q_ARG("bool")+Q_ARG("QString")` 两个参数，
  于是 ping 回调永不触发 → `_bridge_ok` 恒为 False → **Maya 和 Max 同时显示未连接**，
  而 socket 其实完全正常、DCC 端日志一切正常。
  这个 bug 的表象和「端口被占 / 服务端没起 / 协议错」几乎一样，光读代码看不出来。
  两道防线：
  1. `async_bridge` 里检查 `invokeMethod` 的返回值，失败就打日志（原来 `except: pass` 全吞了）
  2. `test_ping_callback.py` 起两个假服务端做端到端断言，含一条**反向验证**
     （故意把槽写成 `@Slot(bool)`，断言回调确实不落地）—— 证明断言本身有效
- ⚠️ **`@Slot` 装饰的函数必须定义在类体里**。局部函数 + `@Slot` 再挂到实例上，
  PySide6 不会把它注册进 metaObject，`invokeMethod` 一样静默失败。

### ⚠️ pymxs action 写法：不要拼字符串给 `rt.execute()`

`create_box` 曾 100% 失败（不是偶发）：

```python
rt.execute('delete (getNodeByName "%s")' % name)   # ← 元凶
```

`getNodeByName` 找不到同名节点时返回 `undefined`，而 `delete undefined`
直接抛 MAXScript 异常：

```
RuntimeError: MAXScript exception raised.
-- 没有与以下项对应的"delete"函数 undefined。
```

**首次创建必然找不到同名节点** → 所以这个 action 从来没成功过。

**统一改用 pymxs 原生 API**，顺带消掉两个隐患：

1. `name` 来自 widget 的用户输入，拼进脚本带引号/空格就会破坏语句，
   极端情况能注入任意 MAXScript。原生 API 无此问题。
2. 原生 API 找不到返回 `None` 而非抛异常，分支显式可控。

```python
old = rt.getNodeByName(name)      # 原生：找不到就是 None，不抛
if old is not None:
    rt.delete(old)                # 只在存在时删
box = rt.Box(name=name,
             boxSize=rt.Point3(size, size, size),
             pos=rt.Point3(pos[0], pos[1], pos[2]))
```

注意 `Point3` 要用 `rt.Point3` 构造，不能直接传 Python list。

`move_selected` 同理改成遍历 `rt.selection` 逐个改 `position`，
并按物体分别 try —— 一个坏物体不再让整批失败，错误信息能指出是哪个物体。

### ⚠️ Max startup 目录：唯一正确做法是让 Max 自报

「安装」按钮把 .ms 写到 Max 的 startup 目录，写错位置的症状是
**写入成功、提示正常，但重启 Max 后压根不自动连** —— 极难定位。

**✅ 权威答案（3ds Max 2027 实测，由用户在 Max 里执行 getDir 得到）：**

| 查询 | 返回 |
|---|---|
| `getDir #userStartupScripts` | `%LOCALAPPDATA%\Autodesk\3dsMax\<版本>\<语言>\scripts\startup`<br>→ `C://Users//<u>//AppData//Local//Autodesk//3dsMax//2027 - 64bit\CHS\scripts\startup` |
| `getDir #startupScripts` | `%ProgramFiles%\Autodesk\3ds Max <版本>\scripts\startup` |

**装我们的脚本用 `#userStartupScripts`** —— 用户可写、不用管理员权限、
升级不会覆盖。`#startupScripts` 是安装目录，别用。

**我错了三次，三次都是"用目录内容猜语义"，一次都没查证：**

1. 看到 `%LOCALAPPDATA%\...\3dsMax\<版本>\` 下只有 `cache/CHS/ENU`，
   断定"这是缓存目录" → **错**。`CHS`/`ENU` 是**语言子文件夹**，
   脚本就在 `CHS\scripts\` 下。我把整棵树当缓存砍了。
2. 看到 `Documents\3ds Max <版本>\` 下有 scenes/export/previews，
   推断 startup 跟着场景目录搬了 → 也错，那是**场景目录**，不执行 .ms。
3. 凭空编出 `getDir #systemScripts` 和 `#startup` 两个 key，
   Max 直接报「该函数需要带有值的参数」并列出全部合法值。

**教训（比路径本身重要）：软件把配置放哪，用它的 API 问它，别看目录猜。**
有 `getDir #xxx` 就用；没有就查该版本官方文档。老版本经验在厂商改过布局后
是负资产 —— Autodesk 从 Max 2022 起就把整个用户目录从 `%APPDATA%` 迁到了
`%LOCALAPPDATA%`，网上所有旧教程都失效了。

`max_bridge/print_status.py` 已内置这批 getDir 查询（只用合法 key）。
**排障第一步永远是跑它**，不要在 Python 侧拼路径试。

顺带记录几条查证到的事实：
- `%LOCALAPPDATA%\Autodesk\3dsMax\<版本>\CHS\scripts\startup` **确实存在**
  （Max 2026-06-29 建的，空的），可作为路径正确性的旁证
- `%ProgramFiles%\Autodesk\3ds Max 2027\scripts\Startup\` 下有 10 个
  Autodesk 自带 .ms（CivilView.ms、Material_PBR_*.ms 等），证明该目录确实被执行
- `stdplugs` 是**二进制插件目录**（71 .dlo / 59 .dlm / 56 .dlu，**0 个 .ms**），
  不是脚本目录 —— 用户曾猜它，实测排除

### ✅ 2026-10-19 定案：安装/卸载改成插件卡片，猜路径的逻辑已删除

前面那一大段「三次连错」的教训，最终落成了一个架构改动：

**卡片插件天然要求 DCC 已连接 → 所以路径可以直接问 DCC 本人。**

于是安装路径不再由中台推导，而是新增 action `max_actions/get_startup_dir.py`：

| | |
|---|---|
| action | `rt.execute("getDir #userStartupScripts")` |
| 依据 | 用户 2027 实测：`rt.getDir("#userStartupScripts")` **抛 MAXScript 异常**（Python 字符串到 name 字面量转换不成立），`rt.execute` 才通 |
| 中台侧 | 拿路径 → `max_setup.write_script(path)` 写文件 |
| 为什么中台写 | Max 侧写文件要多绕 pymxs 文件 API，且 .ms 里有中文注释（实测 393 字节 3 行非 ASCII），UTF-8 + 路径反斜杠转义的坑已吃过三回 |

**已删除**：`max_setup.max_startup_dir()` / `_sorted_dirs()` /
`_max_version_dirs()` / `_tail_num()`。
`script_path()` / `write_script()` / `remove_script()` / `has_script()`
全部改成**必传路径、无默认值** —— 宁可让调用方显式传，
也不在工具模块里藏一个「猜的」fallback。

> 顺带修掉一个潜伏 bug：旧逻辑版本层和语言层都用 `reverse` **字符串**排序，
> 而 ASCII 下大写字母 < 小写，所以语言层会把 `ENU` 排在 `CHS` 前面 ——
> 哪天 ENU 下建出了 `scripts\startup`，装的位置会悄悄跳走。

**但「卸载所有版本」仍然扫目录**，这是有意的区分：

| 操作 | 路径来源 | 需要 Max 连接 | 为什么 |
|---|---|---|---|
| 安装 | `getDir`（问 Max） | 是 | **写**错版本代价大，必须权威 |
| 卸载当前版本 | `getDir`（问 Max） | 是 | 同上，要精确命中"当前这个实例" |
| 卸载所有版本 | `scan_startup_dirs()` 扫 | 否 | **删**不存在的目录无害；且它不针对某个实例 |

⚠️ 已知噪音：`%LOCALAPPDATA%` 下卸载 Max 后不会清目录，
所以会扫到已卸载版本的目录，删除时说「该目录没有 qz 配置」并跳过。这是噪音不是 bug。

**卡片形态**（用户明确要求：不做安装/卸载切换，每个动作一个独立按钮）：

| 卡片 | 按钮 | 需要连接 |
|---|---|---|
| `plugins/max/auto_setup/` | 安装（当前版本）/ 卸载（当前版本）/ 卸载（所有版本） | 前两个要 |
| `plugins/maya/auto_setup/` | 安装 / 卸载 | **不要**（`userSetup.py` 路径是常量） |

**Max 未连接时不预判状态**，统一走 `log_run` 让 action 自己报错，
这样 widget 和其他 Max 插件完全同构，不引入额外的连接状态判断。

**设置面板**里 Maya/Max 两边都只剩「临时启动」一个按钮，
原「安装」「卸载」及其 4 个 signal、4 个 slot 全部移除。

> 📌 **2026-10-21 更新**：设置面板进一步精简 ——
> - 两个「临时启动」合并成**同一行**（一个 section「连接」，
>   按钮文案带 DCC 名：`Maya 临时启动` / `Max 临时启动`）
> - **两个 Reload 按钮也移除了** → Reload 现在是画布插件卡片
>   （`plugins/<dcc>/reload/`），跟 ping / auto_setup 一起在资源树的
>   「设置」组里。卡片版多做了两件设置面板做不到的事：
>   未连接时按钮置灰 + 显示「未连接到 Maya」；
>   识别 `unknown action: reload_actions` 这个**循环依赖**并给出
>   「用临时启动重跑一次」的可操作指引。
>
> **关于「循环依赖」**（不是死锁）：
> `reload_actions` 自己也是一个 action，要先进 action 表才能被调用。
> 所以 DCC 端从没加载过它时，调用会返回 `unknown action`。
> 这不是线程死锁，而是「想用它来修它、但它自己就是坏的那个」的循环依赖。
> 唯一的出路是**重跑一次启动代码**让它重扫一遍。

**多版本语义**：装给当前连着的那个 Max。装的时候连 2027 就只对 2027 生效，
以后真要用 2025 再连 2025 装一次 —— 没有"应该装哪个"的歧义。

**测试**：`test_auto_setup.py`（21 项，新增）覆盖卡片三条路径 + 两份 UI 独立性；
`test_max_setup.py` 39 → 44 项，第 7 节从「测路径推导」改成「测接口收窄」。

> ⚠️ 写这类 GUI 测试必须先建 `QApplication`，
> 否则 `QWidget()` 构造直接**静默崩溃**（exit 127，不抛异常不打栈），
> 看起来像"测试文件坏了"。

### ⚠️ Python `re.sub` 会吃掉替换串里的反斜杠（startup 脚本装完是坏的）

症状链：安装 → 重启 Max → `语法错误: 位于 catch，需要 <因子>`。

**这是两层 bug 叠在一起，第二层更隐蔽：**

**第一层（肉眼可见）**：`build_block()` 生成的是 `try(...) catch(...)`。
MAXScript 要求 try / catch 是**两个独立块**，写成一个表达式就报上面那个错。
→ 改成裸的 `try` / `catch` 各自独占一行。

**第二层（改完第一层仍然坏）**：
`_PATTERN.sub(block, existing)` —— **`re.sub` 的「替换串」会把反斜杠
当转义序列处理**。我们块里有 MAXScript 需要的 `
`（`format` 字符串里），
传字符串时被解成**真实换行**，于是生成的 `.ms` 里字符串跨行，
而 MAXScript **没有隐式续行** → 又是语法错。

```python
# ✗ 错：
 被解成真实换行
new_text = _PATTERN.sub(block, existing)

# ✓ 对：传 lambda 时 re 不做任何转义解释
new_text = _PATTERN.sub(lambda _m: block, existing)
```

**为什么测试没抓住**：`build_block()` 的单元断言全部通过，
因为它直接返回 Python 字符串（`
` 是对的）——
**坏在写文件那一步**，而那一步当时没有任何断言。
这类 bug 必须做**端到端**：真的写文件、读回来字节级校验。

现在 `test_max_setup.py` 有 39 项，其中：
- 3b 节：MAXScript 语法断言（try/catch 独立块、括号配平、无裸换行、
  format 的 `
` 是字面两字符）
- **★ 元凶级回归**：`_PATTERN.sub(lambda)` vs `_PATTERN.sub(str)` 对比断言，
  并带一条**反向确认**（证明断言真的抓得住，不是恒真）
- 断言必须基于**剥掉注释**的代码 —— 注释里为了记录这条坑本身就写着
  `try(...) catch(...)`，直接搜会自我误判（`test_max_ms.py` 犯过同款）

### ⚠️ 测试端口：永远用空闲随机端口

`test_max_bridge.py` / `test_maya_bridge.py` 原来硬编码 17024 / 17023。
server 改用 `SO_EXCLUSIVEADDRUSE` 之后（这是**正确**的改动），
真 Maya/Max 一开着，那些端口就被占着，测试会报「端口已被占用」或
`ConnectionRefused` —— 长得像代码坏了，其实是环境冲突，容易误判。

```python
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
```

另外 `start_server` 不传参会用默认 17024，**必须显式传端口**。

### ⚠️ action 函数体需要有独立测试

`test_max_bridge.py` 走真 socket 但**不装 pymxs**，action 的函数体
根本没被执行过 —— 这类「函数体里的运行时错误」它一项都抓不到
（create_box 那个 bug 就是这么漏到实机上的）。

`test_max_actions.py` 用假 pymxs 模块把 action 真正跑一遍。假 runtime 的
`execute()` 直接抛 `AssertionError`，从机制上禁止再退回拼字符串的做法。

### 回归结果

```
test_app_2.py       30/30   双桥接并列 / 双分支 / 同名共存 / v4 持久化 / 幂等
test_canvas.py      ALL PASS  8 节点增删锁定 fit focus 保存重启
test_multiselect.py ALL PASS  框选 / 批量移动 / Ctrl 加选 / z 序 / 跨 DCC 多选
test_max_bridge.py  18/18
test_max_setup.py   39/39  标记块 + 路径(Max自报) + MAXScript语法 + re.sub转义  标记块策略 + startup 路径探测
test_bridge_client.py 15/15  耗时断言（ping 延迟 / 超时 / 双 DCC 并发不串行）
test_max_actions.py    35/35  action 函数体逻辑（假 pymxs 真执行，含反向验证）
test_ping_callback.py 11/11  ping 回调链路端到端 + 反向验证（本次新增）
test_max_ms.py        18/18  bootstrap.ms 静态校验（含挪出屏幕防小框）
test_maya_bridge.py  PASS
```

`test_app_2.py` 重写为双 DCC 版（覆盖了原 `test_app.py`，后者已删 —— 它引用的
`_open_plugin` 从不存在，且断言已被 test_app_2 完整覆盖）。
集成测试跑法：`QT_QPA_PLATFORM=offscreen python test_app_2.py`。
注意 **GUI 测试前要删掉 `~/.qz-tool/layout.json`**，否则上轮保存的节点会被恢复、污染断言。

### 还没做的

- `plugins/__init__.py` 目前是空文件；工厂加载走 `spec_from_file_location`，理论上不需要它，
  但保留无害（也方便以后 `import plugins.maya.xxx`）。
- `"dcc": "both"`（真正共用的插件）已支持字段读取，但当前没有插件用它。
