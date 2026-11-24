# 添加 Maya 插件操作手册

Blender 5.2 LTS 示例见 `plugins/blender/read_selection/`。它沿用同一份 manifest 和 `create_widget(client, log, parent=None)` 工厂签名，清单的 `dcc` 使用 `blender`。宿主动作放在 `blender_actions/`，在模块中用 `ACTIONS` 字典登记动作名和函数，由 Blender 主线程定时器执行。Reload Plug 重新扫描该目录的公开 Python 模块。示例卡片把请求交给 Qt 线程池，再在 UI 线程用 `BasePluginWidget.log_result` 写日志。连接步骤见 [桥接连接与断开](bridge-connections.md)。

> **本文档写给 Agent 看**：拿到一个「把某个 Maya 脚本/功能加进中台插件库」的任务时，
> 照本文档走即可，不需要重新探索代码库。
> 最近一次完整案例：rename_tool（批量重命名，2026-11-02），过程见
> 本地笔记 `memory/2026-11-02-rename-plugin.md`（若本机保留）。

---

## 一、要动/要新建的文件（共 3 + 2 个测试）

普通用户新增Maya工具可直接使用“Maya → 用户自定义 → 添加脚本…”，支持选择Python/MEL文件、整个工具目录或直接粘贴代码，来源类型自动判断，工具目录按插件名称保存（支持中文）。粘贴Python代码会尝试建议无参数入口，保存时不执行；不需编写manifest/widget/action。操作和边界见[user-scripts.md](user-scripts.md)。下文仍适用于需要自定义中台参数界面的正式插件开发。

```
maya_actions/<id>.py                    ← 新建：Maya 进程内执行的 action
plugins/maya/<id>/manifest.json         ← 新建：插件清单
plugins/maya/<id>/widget.py             ← 新建：中台侧卡片 UI
tests/test_<id>.py                      ← 新建：该插件的测试
tests/test_app_2.py + tests/test_plugin_tree.py   ← 修改：两处计数断言
```

**命名**：`id` 全小写下划线，三处（目录名 / manifest.id / `@register_action("...")`）必须一致。

### manifest.json 模板

```json
{
  "id": "<id>",
  "name": "中文名",
  "dcc": "maya",
  "icon": "",
  "widget_factory": "widget.create_widget",
  "shortcut": "",
  "category": "settings"      ← 可选，仅工具型插件（ping/reload/auto_setup）才加
}
```

⚠️ **`dcc` 必须与所在目录名（`maya`）一致，不一致会被加载器静默跳过**（无任何报错）。

---

## 二、动笔前要读的文件（按此顺序，读完再写）

| 顺序 | 文件 | 读它为了什么 |
|---|---|---|
| 1 | `plugins/maya/bone_mirror/widget.py` + `maya_actions/bone_mirror.py` | **主要参考**。复杂卡片（多操作共用范围开关、结果进日志）的标准写法 |
| 2 | `plugins/maya/create_box/widget.py` + `maya_actions/create_box.py` | 最简参考，认识最小可用结构 |
| 3 | `ui/plugin_widget.py` | `BasePluginWidget.log_run(action, **params)` —— 调 action + 写日志的统一出口，卡片里**不要自己** `client.call` 后手写日志 |
| 4 | `maya_bridge/__init__.py` | `@register_action("name")` 的注册机制 |
| 5 | `ui/plugin_loader.py` | manifest 怎么被扫描、`load_factory()` 怎么被调（只扫 `plugins/<dcc>/<id>/`） |
| 6 | `tests/test_rename_tool.py` | 新测试照它的骨架写：纯函数单测 + manifest 断言 + 工厂签名断言 |

用户给的是**独立脚本**时（如 rename_tool 的做法）：逻辑搬进 `maya_actions/<id>.py`
并按 action 协议改写（返回 `{"count":…, "names":…}` 这类可日志化的 dict），
UI 部分用 PySide6 重写成卡片，**不要原样嵌套原脚本的 UI 代码**。

## 三、不用读 / 不用管的

- `ui/canvas.py`、`ui/plugin_node.py`、`ui/main_window.py`、`ui/plugin_tree.py`
  —— 画布/树是通用设施，加插件不碰它们。
- `dcc_bridge.py`、`maya_bridge/server.py`、`max_bridge/`、`max_actions/`、`plugins/max/`
  —— 桥接与 3ds Max 侧全部无关。**UI 不与 max 共用**，即使两边都要同类插件也是各写各的。
- `ui/theme.py`、QSS、`paths.py`、`defaults.json` —— 除非插件要新增资源文件，否则无关。个人设置位于 data/，不随官方更新替换。
- `build/`、`dist/`、`QzTools.spec`、`selftest.py`、`selfcheck_dist.py`
  —— 打包与分发自检，加插件不需要（它们是白名单式的，新插件不会也**不需要**进自检）。

---

## 四、硬性规则

1. **Maya 侧一律 Py3**（用户拍板：Maya 都用 Python 3，不需要兼容 Py2）：
   - f-string、类型注解等随便用；
   - 唯一注意点：`import maya.cmds` 放**函数内**（本地单测环境没有 Maya，模块顶层 import 会让测试直接挂）；
   - `maya_bridge/`（socket worker）同理无 Py2 限制；Py2 兼容只剩 `max_bridge/` `max_actions/`（3ds Max 2020 及更早）。
   - **Max兼容范围（用户2026-11-12明确）**：Max2020/Python2.7是当前必须可用的基线，不是唯一目标。Max端公共代码同时考虑Python3，版本/API差异按实际宿主适配；明确区分设计兼容与原生验证过的版本，不能把中台Max插件限定为2020/Python2。
2. 卡片工厂签名固定：`create_widget(client, log, parent=None)`，返回 widget 实例；
   推荐 `class X(QtWidgets.QWidget, BasePluginWidget)` 并在 `__init__` 设
   `self.client / self.log / self.action_label`。
3. 结果统一走 `self.log_run("action名", **params)`，日志前缀用 `action_label`。
   action 返回 dict，失败/无操作时给 `{"count":0, "warning":"原因"}`，**别静默返回 None 不带原因**。
4. 需要感知连接状态的卡片：`getattr(self.client, "connected", False)` + QTimer 轮询
   （卡片拿不到 MainWindow 信号）。

## 五、用户偏好（必须遵守）

- **卡片按钮不加 tooltip**；按钮文字本身要能说明行为（含副作用，如「检查重名（选中冲突物体）」）。
  用户明确要求的例外（2026-11-09）：Max 顶点颜色的纯色预设按钮不显示数字文字，RGB 数值使用 tooltip；「应用缩放」按钮也使用 tooltip 显示原按钮下方说明「1.00 保持原值；每次点击按当前颜色乘倍率。」。其余业务按钮继续遵守本条。
- **卡片宽度不限制**：画布卡片会随内容自适应（QGraphicsProxyWidget 按 sizeHint 撑开），
  排版以清晰为先，不需要迁就 320px，也不用写宽度相关测试。
- 结果**进日志面板**，不要在卡片里弹窗/弹结果区（bone_mirror 模式）。
- 测试脚本简单直接，不过度抽象。

---

## 六、测试

### 要新写 `tests/test_<id>.py`，至少覆盖

1. `maya_actions/<id>.py` 里的**纯函数**（把可离线算的逻辑抽成不依赖 maya.cmds 的函数再测）；
2. manifest 字段齐全且 `dcc == "maya"`、`id == 目录名`；
3. `widget.py` 能被扫描到、工厂可实例化（传假 client/log，`client.call` 返回 `{"ok":True,"data":{}}`）；
4. 卡片交互至少点一遍核心按钮，断言 `client.call` 收到的参数拼装正确
   （含范围开关 `hierarchy` 这类全局参数）。

### 要同步修改的两处既有断言

- `tests/test_app_2.py` ~L42：`扫到 maya 插件` 清单里加入新 id（列表按字母序）；
- `tests/test_plugin_tree.py` ~L273：`★ maya 分组计数`（普通插件 +1；若 `category:"settings"` 则括号里两组数字都要核对）。

### 怎么跑

```bash
cd /c/Users/Administrator/Documents/qz
.venv/Scripts/python.exe tests/test_rename_tool.py     # 单个，先跑新写的
.venv/Scripts/python.exe run_tests.py                  # 全量（25 个文件），交付前必跑
.venv/Scripts/python.exe run_tests.py plugin_tree      # 过滤参数 = 文件名子串
```

### 测试方法论红线（违者测了等于没测）

- ★★★ **失败必须体现在退出码**：`sys.exit(1 if FAIL else 0)`。只打印不算判定。
- GUI 测试**先建 QApplication 且持有引用**（局部变量挂住），否则静默崩溃/segfault。
- 测交互**必须走完整事件路径**（`QTest.mouseClick`），不能直接调处理函数。
- 管道看结果时 `$?` 是 `tail` 的 → 重定向到文件再判断。
- 图形断言要剥注释再搜代码（tokenize / 正则去 `<!-- -->`），否则自我命中。

---

## 七、交付前验证清单

- [ ] `run_tests.py` 全量绿（EXIT=0）
- [ ] `test_<id>.py` 的断言数 > 0 且确实覆盖新逻辑
- [ ] manifest 三处 id 一致、`dcc` 与目录一致
- [ ] `maya_actions/<id>.py` 里 `import maya.cmds` 在函数内（本地单测能 import）
- [ ] 卡片截图过一眼排版（offscreen 渲染，见 `2026-11-02-rename-plugin.md` 里的做法）——可选项，卡片宽度无硬性要求
- [ ] 提醒用户：在 Maya 里点一次「重载动作库」或重启 Maya，卡片才会生效

## 数值计算插件的补充约定（2026-11-05）

蒙皮权重插件使用中台 NumPy，纯数值逻辑位于 `ui/skin_weights.py`，Maya action 负责场景采集、选择、写入及校验。大数据用本机临时二进制文件交换，JSON 只传任务及摘要。后台任务完成后由 UI 线程调用 `BasePluginWidget.log_result()`，与同步 `log_run()` 共用日志出口；后台线程不能直接操作日志控件。

新增 NumPy 等二进制依赖属于前述“不用管打包”的例外：需更新 requirements、hidden-import，并在实际 exe 内执行数值自检。构建时仅在构建进程内把 System32 放到 PATH 首位，避免收进其他工具的同名 ICU；不修改用户永久 PATH。离屏截图出现方框时，先检查 Qt 字体映射，验证进程可显式加载本机中文字体。无箭头 QSpinBox 在当前 Qt 样式下自定义 border/padding 会压缩文字编辑区，保留默认边框与留白，仅设置前景和背景色。

## 插件控件公共样式（2026-11-05）

所有 Maya / Max 插件卡片在 `PluginNode` 挂载内容时自动应用
`ui/resources/controls.qss` 与 `plugin.qss`，入口为 `ui/plugin_widget.py::apply_plugin_style()`。
无需在每个 `widget.py` 调用 `setStyleSheet()`，也无需继承公共 widget 类才能获得样式。
按钮统一采用 Maya「重载动作库」的灰色背景、圆角、边框与悬停样式；输入框、
数字框、下拉框、分组等采用蒙皮插件的深灰风格。其他标准控件沿用同一配色。
单选/复选指示器使用统一的深色空心轮廓，选中分别显示圆点/勾号，禁用标记单独变暗。
图形位于 `ui/resources/controls/`，公共 QSS 的 `@UI_RESOURCES@` 在加载时经
`paths.resource_dir()` 替换为绝对路径，不依赖启动工作目录，也不依赖系统字体字形。
插件附加样式仅挂到内容 widget，DCC标题栏与选中边框仍单独管理。Maya / Max 的业务 UI
继续分别编写，共用控件外观不会把它们的业务逻辑合并。

普通标签不需声明颜色。状态文字使用语义角色，颜色在公共 QSS 中维护：

```python
from ui.plugin_widget import set_role
set_role(label, "hint")     # 辅助说明
set_role(label, "muted")    # 次要状态
set_role(label, "success")  # 成功
set_role(label, "warning")  # 警告
set_role(label, "error")    # 错误
```

后续调整通用外观只改 `controls.qss`；`plugin.qss`仅定义内容区底色。仅特殊控件需要局部样式，避免再次整段复制通用
按钮/输入框 QSS。数字框保持 Qt 原生边框和留白，特别是 `NoButtons` 模式，不套用
普通文本框的 border/padding；其内部编辑器由公共样式清除这些属性。按钮不再使用
各插件散落的固定高度，随字体和公共留白计算合适尺寸。

`tests/test_plugin_style.py` 覆盖全部插件的实际按钮/输入框、Reload 状态切换、蒙皮
数字输入与弹窗选择、单选/复选真实点击及像素差异，以及样式范围。滚轮规则仍由 `test_canvas_wheel.py` 验证。
开发版更新后重启中台即可生效，不需要重载 Maya 动作库。本轮仅开发目录测试，未打包。

## 应用主题与外围组件（2026-11-06）

`ui/app_style.py::apply_application_style(app)`在主入口创建窗口前统一应用Fusion、
深色调色板与公共QSS；直接构造MainWindow也调用同一入口，重复调用不会重设主题。
Inactive/Disabled颜色均明确设置，提示框不再依赖系统黑色文字。

- `controls.qss`：按钮、紧凑按钮、输入框、数字框、单选/复选、菜单、提示框、滚动条和语义标签。
- `style.qss`：设置面板、资源树、搜索、活动栏、日志和分隔条，按控件ID或属性限定作用范围。
- `plugin.qss`：插件内容区底色；独立预览卡片仍加载同一份controls.qss，无需全局启动主窗口。
- `theme.py`：继续负责动态DCC配色；卡片标题尺寸与画布拖动判定保持原规则。

新增普通控件优先继承应用样式。小按钮设置`qzDensity="compact"`；状态标签使用
`set_role`；动态状态使用`set_style_property`。固定颜色不再写进组件的setStyleSheet。
仅DCC色块和卡片标题等由用户数据决定的颜色仍保留必要的动态样式。
容器禁止使用无选择器的`background: ...`，否则会覆盖后代控件和提示框背景。
选色采用Qt对话框跟随暗色主题；设置启动提示保留文字说明，但插件按钮仍不加tooltip。

测试组件时先创建并持有QApplication，再调用应用主题入口；不要通过复制局部QSS
模拟运行环境。`test_app_style.py`覆盖真实提示框、内置编辑菜单、数字框箭头/键盘、
内部滚动条、活动栏图标状态及QGraphicsProxyWidget卡片。搜索与资源树另有像素/交互回归。
离屏预览若系统字体未被枚举，只在预览进程加载本机字体，不修改产品字体或系统设置。

## 八、收尾

官方插件右键仅保留打开位置，改名和删除入口已取消；用户自定义插件管理不变。见 [plugin-management.md](plugin-management.md)。构建和更新流程见 [build-guide.md](build-guide.md)。

Maya 原生验证优先使用 `tests/run_maya_native.py <提供run函数的测试脚本>`，通过 `maya.exe -batch` 启动隔离进程，细节见 [maya-native-tests.md](maya-native-tests.md)。本机 mayapy/直接 mayabatch 存在 DLL 启动异常，Maya 主程序批处理入口已实测可用。不要因旧入口失败就把所有原生验证标为不可运行，也不要在用户现有交互场景运行会重建场景的测试。

可在本地 `memory/` 追加日期笔记。需要给其他开发者使用的新约定，直接更新 `docs/` 中对应文档。

## 画布内滚轮交互（2026-11-05）

画布统一按鼠标所在区域分配滚轮，各插件使用标准 Qt 控件即可，无需自行拦截画布缩放。空白、标题、普通标签或按钮区域仍以鼠标为中心缩放。QSpinBox/QDoubleSpinBox 与收起的 QComboBox 只有先点击获得焦点后才处理滚轮；未聚焦时不改参数，滚轮继续缩放画布（2026-11-05用户后续确认的新规则）。QAbstractScrollArea（列表、文本、滚动容器）及滑块/滚动条处理自己的滚轮；展开的下拉弹窗优先，不滚动它后面的卡片。没有新增 Ctrl＋滚轮专用缩放规则，修饰键保留控件的原生含义。

已聚焦的参数控件或滚动区域即使没有产生数值/滚动变化，也不回退到画布缩放或平移。实现只做命中、控件类型与焦点判断，不检查数值上下限、滚动条末尾；这些由 Qt 自己处理。只向画面最上层命中的代理转发，不穿透到下层卡片，坐标经 scene/proxy 映射，兼容画布缩放。布局锁定仍阻止节点选择/拖动，但允许内嵌控件获得焦点并编辑参数。

tests/test_canvas_wheel.py 通过真实视口 QWheelEvent 与 QTest 点击测试控件分配、失焦后悬停、缩放坐标、滚动区、弹窗及遮挡，另直接加载蒙皮插件检查 max_inf_sbox。测试不调用 wheelEvent 私有处理函数，也不操作 Maya/Max 场景。仅在开发目录运行，不打包；修改画布后需重启开发目录中的中台程序。

真实蒙皮插件还覆盖 mode_cbox 的完整鼠标展开/选择/收起路径，保持焦点时上下滚轮切换两种分配方式，点击数字框使其失焦后滚轮仅缩放画布。测试只发UI事件，不调用Maya动作。

## 跨 DCC 插件按需获取客户端（2026-11-05）

既有工厂签名不变。需要协调两个DCC的卡片可声明 set_dcc_clients(clients)，MainWindow在实例化后注入现有_clients字典；卡片借用其中同一客户端及connected状态，不自行创建另一份端口/连接配置。蒙皮卡片使用这一入口发送权重到Max，处理流程位于ui/skin_transfer.py；不把pymxs或Maya API导入中台UI。


## Max 顶点颜色（2026-11-09）

`max_actions/vertex_color.py` 与 `plugins/max/vertex_color/` 现支持 Color、Alpha、Illumination、M17、M18。除 Alpha 使用独立 A 数值外，其他通道使用 RGB 分量开关。顶部单选框实际切换时调用 vertex_color_display 自动切换 Max 显示，不写映射数据；卡片创建时不调用。20 个纯色预设直接应用且不改滑块，RGB 值通过 tooltip 显示（用户明确要求的例外）。Max2020/Python2.7 宿主与中台 PySide6 继续分离。原脚本的低层映射写入不能直接依靠 `pymxs.undo` 完整撤销，首版明确不支持 Ctrl+Z；失败采用显式颜色/映射恢复。范围与原生验证见 `docs/max-vertex-color.md`。
