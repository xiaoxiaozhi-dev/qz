# Maya / Max / Blender 用户自定义脚本

Maya、Max 和 Blender 资源分支均固定显示带数量的“用户自定义”组，排在普通插件之后、“设置”之前。单击组内的“添加脚本…”打开对应宿主的导入窗口。也可以通过软件分组、用户自定义组或已有用户脚本的右键菜单添加。添加窗口与卡片复用公共主题。

Maya 支持 `.py` / `.mel`，Max 支持普通 `.py` / `.ms`。Max2020 / Python2.7 是必须兼容的当前环境，不是支持上限；执行器同时兼容 Python3，用户脚本由所连接 Max 的实际 Python 解释器执行。脚本自身仍需与目标软件版本/API/第三方依赖匹配，不自动转换 Python 语法。

## Blender 5.2 LTS

在「Blender → 用户自定义」点击「添加脚本…」，选择 Python 文件、包含 Python 入口的工具文件夹，或粘贴代码。入口留空时执行整个文件，填函数名时调用该无参数函数。导入副本保存在 `user_plugins/blender/`，添加完成即更新组内数量和搜索。右键脚本可以改名、修改入口、打开脚本目录或移除导入副本。

连接后点击卡片上的「运行脚本」。Blender 主线程使用 Blender 自带的 Python 编译并执行，输出和异常进入中台日志。等待主线程调度和等待脚本完成分开计时。脚本执行中，同一个 Blender 桥接仍回复心跳并报告「繁忙」。

## 添加与使用

1. 填写插件名称，也用于保存工具的文件夹名称，可使用中文。
2. 路径选择和代码输入在同一页显示，不使用 Tab 分页。选择该宿主支持的脚本文件，或者整个工具文件夹，也可以直接粘贴路径。系统按路径类型判断：文件只复制该脚本，文件夹复制完整工具并保留资源及目录结构，不再单独设置“导入内容”。只有一个脚本时自动选为入口；有多个脚本时优先选择根目录唯一的 main.py/main.mel（Maya）或 main.py/main.ms（Max），否则需选择入口。忽略 .git、.venv、venv、__pycache__ 和编译缓存，目录中的链接拒绝导入。Maya Python 添加时只检查语法、不执行；Max Python 在运行时由目标解释器编译检查，中台Python3不会误拒绝合法Python2语法，添加时也不执行。
3. 没有文件时直接在代码输入框粘贴完整代码，无需导入任何文件。“脚本语言”为 Python/MEL 单选，默认 Python（.py）；导入文件或选择目录入口时按扩展名自动同步语言。粘贴代码保存为工具的 files/main.py 或 files/main.mel。Python 自动建议唯一的公开无参数函数或 main 作为入口；代码含顶层执行语句时不自动建议，防止重复执行。入口建议可以手动修改。路径和代码同时填写时优先导入文件或文件夹，忽略粘贴代码，不检查该代码的语法；来源路径无效时报告路径问题，不回退为粘贴代码。
4. “入口名称”留空时执行整个文件，包括 Python 的 `if __name__ == '__main__'`；仅定义函数的工具填写无参数函数名，如 `main` 或 `tools.run`。MEL 填写无参数全局过程名，如 `openTool`。名称不包含括号、参数或额外命令。例如粘贴 `import maya.cmds as cmds` 和 `def create_cone(): cmds.polyCone()` 后，入口建议为 create_cone，保存时不会创建物体，运行工具时才创建圆锥。
5. 添加成功后立即出现在树和搜索中，并自动打开统一样式的运行卡片。添加、扫描、恢复布局和打开卡片均不执行用户脚本。
6. 连接 Maya 后点击“运行脚本”。中台通过现有 Maya bridge 主线程调度，脚本自带窗口在 Maya 中打开。Python print/stderr、完成耗时和异常堆栈进入中台日志；长输出最多保留前 32 KB。MEL 原生输出可能仍显示在 Maya Script Editor，完成与异常由中台报告。

运行时按钮禁用防止重复提交，中台线程池等待响应，不在后台线程调用 DCC API。脚本需能在目标软件环境中正常执行，第三方库仍须在对应宿主环境可用。参数控件生成、自带窗口嵌入中台及自动安装第三方依赖均不在此版范围。

Max 的同页语言单选为 Python / MAXScript，默认 Python；文件或目录入口选中 `.ms` 后自动同步语言。粘贴内容保存为 `files/main.py` 或 `files/main.ms`。粘贴的 Python 文本按 UTF8 保存并添加或规范化编码声明以兼容Python2；MAXScript粘贴内容按UTF8 BOM保存以支持Max2020中文。导入现有文件保留原字节，不改原编码。Python入口可为无参数函数（支持属性路径），MAXScript入口为无参数全局函数，留空均执行整个文件。无法自动推断入口的旧语法或复杂脚本可手动填写入口。`.mcr`、`.mse`、`.mzp` 安装包不在普通脚本导入范围。

Max 执行通过主线程 action `user_script_max`，首次运行前只读预检并在需要时刷新旧动作库，实际运行不重试。Python读取文件字节后交给当前解释器编译，不调用存在Unicode编码声明问题的ExecuteFile；MAXScript通过原生fileIn加载。脚本自带窗口留在Max中，Python模块保留用于后续回调；Python输出及异常回中台，MAXScript原生print可能仍在侦听器，完成或失败由中台报告。单次执行最多等待600秒，关闭中台或请求超时不表示Max中已经开始的脚本被取消。

## 管理与数据保留

### 固定到 Maya 当前工具架

先连接 Maya，在 Maya 中选中要放置按钮的工具架；回中台右键 Maya“用户自定义”组内的具体脚本，选择“固定到 Maya 当前工具架”。支持Python和MEL，只安装一键执行入口，固定时不执行用户脚本。操作异步完成，日志报告实际工具架和新增/更新状态。Max 使用下述独立工具栏入口。

同一脚本在同一工具架重复固定会更新其已有按钮，不修改其他按钮；可以分别固定到多个工具架。按钮按插件稳定ID标记，保存至Maya当前版本的用户工具架目录，保存失败会撤销本次按钮改动并报告错误，已有工具架文件通过临时目录与原子替换保护。文件编码沿用Maya原生保存方式，不强制转UTF8。

点击工具架按钮直接在Maya主线程读取最新登记与脚本，使用已填写的函数/过程入口，不需要中台保持运行，也不要求桥接端口开启。改名、入口修改、脚本内容修改以及同根下工具文件夹改名均按ID读取，功能更新无需重新固定；按钮显示名称需再次固定更新。用户脚本窗口仍在Maya中打开。移除脚本后不会继续执行旧代码，原工具架按钮点击时提示登记已移除，可使用Maya原生方式删除该按钮。搬动整个中台项目目录后需重新固定以更新路径。

实现为 `maya_actions/user_shelf.py`（`user_script_shelf` version1预检/固定及独立点击入口）。中台首次固定会预检并在需要时刷新旧动作库，安装不自动重试。分发检查已包含此模块，未打包。

验证：`tests/test_user_shelf.py` 7项覆盖实际Qt右键异步路径、只固定当前工具架且不执行、重复固定、目录/入口更新、移除反馈、保存/替换失败回退、预检刷新、断连和失败不重试。Maya2024独立GUI功能报告 `build/maya-native/maya_user_shelf_integration-c62c841b/result.json` 为ok=true，原生Python/MEL、保存MEL重新加载后的按钮执行、原有按钮保留、重复固定、中文目录改名及移除提示均通过。该环境独立GUI进程退出会挂起，启动器在60秒截止只清理自建进程，因此launch.ok=false，不能声称宿主退出验证通过；不加载本功能的空GUI对照也出现同样退出挂起（`maya-gui-exit-probe-589d8afb`）。用户原Maya进程未操作。批处理不提供真实工具架UI，不能用batch验证此功能。

资源树右键用户脚本，可改名、修改入口、打开脚本目录或移除。改名不改变内部编号、卡片位置或布局标识；标题、卡片信息和搜索立即同步。运行中的卡片拒绝改名、入口修改和移除，等待本次请求结束后操作。

“移除”会弹出确认框，列出实际工具目录并说明永久删除范围，默认选中“取消”。确认后删除该插件整个工具目录（manifest、脚本副本、资源和生成缓存），关闭卡片，更新树及搜索，并立即清理该 ID 的持久布局记录。导入前的原始脚本/工具目录和其他插件保留；不撤销脚本已对 Maya 场景或窗口进行的操作。取消则保留全部内容。删除前验证最终绝对路径属于用户插件根目录内的直接子目录，并拒绝目录链接；文件占用等错误会报告到日志，登记最后删除，以便失败后重试，界面不会显示移除成功。

导入的文件副本会解除继承的只读属性，原始来源属性保留；移除时也会处理旧副本、资源及登记上的只读属性。只在已校验的该工具目录内处理，不绕过真实文件占用或权限限制；遇到这些错误仍保留登记供重试。

脚本副本按宿主保存在 `user_plugins/maya/`、`user_plugins/max/` 或 `user_plugins/blender/` 下，由 `paths.user_plugins_dir(dcc)` 提供，打包运行时位于 exe 旁边。目录名使用插件名称，支持中文；Windows禁用字符替换为下划线，去掉首尾空格和点，空名称回退为plugin，保留名称加tool_前缀。重名加_2、_3等序号，源脚本及资源文件名保持不变。每项含manifest.json与files/，新登记记录dcc；旧Maya登记缺dcc字段仍可读取。内部ID保持稳定，同宿主重复ID拒绝加载，读取、管理和布局均按宿主区分，改名不改变已有目录或ID。旧英文目录和user_<唯一编号>目录继续兼容。

复制中台到其他电脑时一并复制 user_plugins/；更新或替换程序时应保留此目录，用户脚本不纳入官方分发包。原始脚本不被修改或删除；需要修改运行副本时使用“打开脚本目录”。移除为确认后的永久删除，不再只隐藏登记或保留导入副本。无需修改官方 plugins/、maya_actions/ 或重新打包。

入口每次运行读取当前文件。Python 使用每个工具独立的执行模块，保留模块供窗口回调使用；不会把变量写入 Maya 原有 __main__ 全局字典。工具目录加入 Maya 当前进程的 Python 搜索路径，MEL 工具目录加入当前进程 MAYA_SCRIPT_PATH 并 rehash，供工具的辅助脚本和延迟回调使用，不写操作系统永久环境变量。同名依赖和依赖模块缓存遵循 Maya 的正常导入规则；复杂 Python 包、写死的外部路径或依赖已有 Script Editor 全局变量的工具，可能仍需调整入口。资源文件建议基于 `__file__` 定位。

畸形登记单独记录日志，不阻断其他插件。入口文件被用户删除时条目仍保留，可打开目录修复或移除。执行失败/网络超时不会自动重试，也不会回滚用户脚本本身的场景操作；关闭中台不代表 Maya 中已开始的脚本被取消。执行器捕获 SystemExit 和 KeyboardInterrupt，避免脚本通过普通退出异常结束桥接。

### 固定到 Max 工具栏

连接 Max 后，右键 Max“用户自定义”组中的具体脚本，选择“固定到 Max 工具栏”。支持普通 `.py`、`.ms`，在 Max 底部创建“Qz 自定义工具”工具栏，使用宿主原生 Qt 样式，可以拖动停靠或浮动。同一脚本重复固定更新原按钮，不重复添加，不执行用户脚本，也不修改其他工具栏。

点击按钮在 Max 主线程按稳定 ID 读取最新 manifest、源文件和函数入口，沿用现有 Python/MAXScript 执行器。中台可以关闭，桥接可以断开；项目脚本目录仍需保留。工具文件夹在同根下改名不影响执行。脚本名称在重复固定或下次 Max 启动恢复时更新；入口及内容每次点击立即读取。移除脚本后保留的按钮只提示已移除，不执行缓存旧代码。按钮右键“取消固定”仅移除按钮，不删除脚本；工具栏右键也可隐藏，重新固定可显示。

固定列表保存到当前 Max 用户脚本目录下 `qz_user_tools/toolbar.json`，启动恢复文件为当前用户启动脚本目录下 `qz_user_tools.ms`。不改全局 CUI 或桥接自动启动文件；不同 Max 用户配置各自保存。启动脚本只恢复按钮、不执行用户工具、不启动桥接。重启后恢复固定列表，首次创建默认停靠底部，不单独保存用户拖动后的工具栏位置。Max 禁用自动加载启动脚本时不能自动恢复，可重新固定显示。整个中台目录搬动后须重新固定对应工具以更新路径。

实现为 `max_actions/user_toolbar.py`，注册 `user_script_toolbar` version2。Qt/PySide 在使用 UI 时延迟加载，Max2020 通过 MaxPlus/PySide2，新版优先 qtmax 并按宿主选择 PySide2/PySide6；同时支持 Python2/3语法。中台预检需要时重载动作库，实际固定只请求一次，不因超时重复安装。配置原子替换，配置写入失败恢复启动文件及按钮；不覆盖同名非 Qz 启动文件。启动恢复异常只输出日志，不弹模态窗口阻断启动。

`tests/test_max_user_toolbar.py` 9项通过：实际Qt右键异步固定、重复固定不运行、其他工具栏保留、最新入口/目录读取、动作模块重载后单次回调、移除拒绝旧代码、按钮实际右键取消固定及恢复后不重现、保存失败恢复、执行中/错误配置/越界保护及断连/失败不重试。原用户脚本42、Max管理11、Maya工具架7、插件管理8、插件树44及Python3执行器7项回归通过。

右键修复：Max会在应用层接管工具栏右键，仅重写QToolBar.contextMenuEvent无效。version2在应用层仅拦截本工具栏的有效脚本QToolButton右键按下/释放及上下文事件，避免进入Max原生工具栏菜单；其他工具栏不拦截。每次刷新移除旧过滤器，已有工具栏重复固定也能更新。原生Max2020/Py2实际按钮右键与菜单取消固定通过（build/rigging-native-db4002b6/result.json，right_click_unpin=true，脚本保留，宿主0）。更新后重启中台，再将任意已有脚本固定一次，会自动刷新旧动作并安装按钮右键处理；也可保存后重启Max由启动恢复加载新实现。

独立Max2020/Python2.7原生验证 `tests/max_user_toolbar_integration.py` 通过：真实Qt工具栏按钮运行Python与MAXScript函数、中文及带引号路径、目录改名、重复固定、原生启动文件重新加载；同一隔离配置重启第二个进程后确认 `automatic_startup=true`，按钮无需桥接运行。两次宿主退出码均0，证据 `build/rigging-native-40cd15ac/{result,launch,result-restore,launch-restore}.json`。不操作现有交互场景或真实用户工具栏。新版Max尚无原生验证，Python3/Qt6测试不等于所有新版宿主均已实测。

实现依据：[Autodesk Max2020 工具栏示例](https://help.autodesk.com/cloudhelp/2020/ENU/Max-Python-API/files/py_ref/demo_py_side_tool_bar_q_widget_8py-example.html)、[qtmax 主窗口接口](https://help.autodesk.com/cloudhelp/2024/ENU/MAXDEV-Python/files/MAXDEV_Python_qtmax_module_html.html)、[启动脚本顺序](https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/MAXScript-Introduction/General-MAXScript-Topics/GUID-615D14FB-0F2D-4801-B381-1128C4128C70.html)。重启开发中台显示新菜单，首次固定会刷新Max动作库，无需提前重启Max；本次未打包。

## 实现与验证

- `ui/user_scripts.py`：副本导入、原子发布/登记更新、扫描及后台调用协调。
- `ui/user_script_dialog.py`、`ui/user_script_widget.py`：添加对话框和公共运行卡片。
- `maya_actions/user_scripts.py`：Python/MEL 执行器；`max_actions/user_scripts.py`：兼容Python2/3的Python/MAXScript执行器。两者均提供version=1只读预检，旧动作库在首次运行前重载，实际运行不重试。
- 插件树、主窗口、画布和搜索支持动态更新；官方 manifest 的加载方式保持原有入口。`build.py` 已登记中台新模块依赖，未执行打包。

`tests/test_user_scripts.py` 41项通过，包括中文插件目录与重名保留、目录改名后按 ID 读取管理、旧 ID 目录兼容、重复 ID 隔离、文件夹自动判断与入口选择、粘贴 Python/MEL 代码、文件/文件夹优先于代码（粘贴语法错误也忽略）、路径无效不回退、语法错误不发布、自动入口建议与手动修改、添加不执行、单文件/整目录副本、登记异常隔离、缺失入口移除、Python/MEL、函数入口、窗口回调真实点击、异常行号与输出上限、SystemExit、旧动作重载及超时不重试；实际 Qt 路径覆盖添加、语言单选默认 Python 与 MEL 切换、文件夹选择、粘贴圆锥代码（Maya cmds 替身验证保存不执行、运行时调用一次）、改名、修改入口、打开目录、移除确认/取消/失败、搜索与卡片清理和持久布局删除。存储测试验证删除整个导入目录、保留源文件与其他工具、删除失败保留登记，以及拒绝越界删除。

Maya2024 自带 Python3.10.8 中的纯 Python 执行与回调验证通过，证据 `build/maya-user-scripts-host-result.json`。此前通过 mayapy 导入 maya.standalone 遇到 DLL 初始化失败（历史证据 `build/maya-user-scripts-native-result.json`）；现已改用 `tests/run_maya_native.py` 启动 Maya 主程序的独立批处理进程，原生 Python/MEL、中文路径、粘贴圆锥代码创建真实模型和导入副本删除均已通过，整次约5.7秒、退出码0。最新证据 `build/maya-native/maya_user_scripts_integration-66cd5bc7/result.json`，启动方式见 [maya-native-tests.md](maya-native-tests.md)。未修改 Maya 安装DLL，未操作用户交互场景；交互式 Maya UI 回调和完整桥接链路不由此测试覆盖。

此前Maya首版完整开发回归46/48文件通过（`build/user-scripts-regression.log`）；未通过项为已记录的旧动画数据测试 test_js_anim_tools.py 和 Max旧替身 test_max_actions.py。此次Max扩展运行相应专项，不将此前全量结果作为本次全量验证。

Max扩展验证：原用户脚本42项（包含两端完整添加/改名/搜索/重启/移除确认、取消、失败流程）、Max管理9项、通用执行器Python3及本机Max Python2.7.15运行库各7项通过；插件树44项、公共主题7项、连接10项、置顶5项回归通过。实际Max2020独立batch验证Python/MXS创建对象、显式函数入口、中文路径与内容、PySide回调和SystemExit捕获通过，退出0，证据 `build/rigging-native-cc08ff56/{result,launch}.json`。Maya2024原生回归通过并退出0，最新证据 `build/maya-native/maya_user_scripts_integration-374874ab/result.json`。

新版Max实际宿主尚未原生验证；Python3执行器兼容测试不等于所有新版Max均已实测。预览 `build/max-user-scripts-preview.png`、`build/max-user-scripts-dialog-preview.png` 使用隔离目录与布局生成。重启中台后可见，Max首次运行会预检刷新新增动作，或手动重载动作库，无需重启DCC。仅开发测试，未打包/提交，未操作用户现有场景。
