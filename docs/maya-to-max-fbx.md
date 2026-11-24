# Maya 发送 FBX 到 Max

中台 Maya 分组新增「选中物体发送到Max」，参考现有 Max→Maya 工具。仅开发源码接入 `<qz-root>`，未打包。

## 使用

1. 重启实际开发目录中的中台；在 Maya 与 Max 分别重载动作库，确认两个连接正常。
2. 在 Maya 选择要发送的模型或骨骼，在 Maya 分组打开「选中物体发送到Max」。
3. 「导入到 Max」追加到当前 Max 场景；「打开到 Max」直接放弃当前 Max 场景未保存修改，新建未命名场景后导入，不询问、不自动保存，与参考工具的打开行为一致。
4. 齿轮浮层只有「导出动画」，默认开启，关闭浮层自动保存。导入完成后选中新增加的对象；按用户2026-11-11后续要求，不再执行任何视口框选。

所有结果、错误和总耗时进入中台日志，按钮不加 tooltip。2026-11-12按用户要求移除“Max实际设置：系统单位、帧率、播放范围”状态输出，避免被理解为工具修改了这些设置；内部响应字段保留。界面沿用公共 QSS 和原卡片的三按钮布局、浮层定位及后台执行方式。下方新增独立的「发送顶点位置到 Max」按钮，详见[顶点位置发送](maya-to-max-vertices.md)。

## 内容和导出

Maya 主线程动作 `fbx_export_max` 将选中组件/shape 归到所属 transform 并去重，附加选中模型 skinCluster 中全部影响节点，包括零权重影响。只检查原选择的模型，不遍历其他模型或自动选入子层级；不主动补父级，FBX 自身可能写出维持层级所需的祖先节点。不发送控制器重建、Shape、摄像机、灯光、约束或点缓存，不嵌入贴图。

使用原生 FBX exportSelected，路径通过 cmds.file 参数传递，支持中文、空格及合法引号路径；成功或失败后恢复原选择（包括组件）。关闭 IncludeChildren 与 InputConnections，避免把其他关联角色整体带出。开启 Skin、平滑组与切线，关闭三角化；使用 FBX201800 兼容 Max2020。Maya导出端保留当前单位和上轴，不手工旋转、缩放或添加补偿组。

动画开启时烘焙当前播放范围、步长1、全重采样，子帧边界向外取整并报告；关闭动画时同时关闭 FBX Animation 分组、烘焙与重采样，模型和蒙皮照常发送。FBX 导出设置在操作后保留，与参考工具一致。

## Max 接收

Max2020/Python2.7 动作 `fbx_max_preflight` 与 `fbx_receive_maya` 经既有主线程 worker 执行 pymxs。预检只加载FBX插件并核对能力，不修改导入选项。接收时只设置对象内容、动画/Take及新增模式，不调用ResetImport。使用 `Mode=#create` 追加对象，不按同名更新场景已有对象；如仍有重复名称，仅为本次新节点分配 uniqueName，保留旧对象。

用户2026-11-11后续要求取消工具对单位、上轴、帧率和动画范围的额外设置。接收端不再设置AxisConversion、UpAxis、ScaleConversion、ScaleFactor、ConvertUnit、KeepFrameRate或FillTimeline，也不重置FBX导入器以免间接覆盖上述选项。这些行为沿用Max当前FBX导入设置，插件不强制开启、关闭或另行恢复；FBX自身仍可能按现有选项转换数据或处理时间。打开模式仍按按钮含义新建场景，使用Max的新场景默认设置。

「导出动画」只控制导出动画数据以及接收端Animation和TakeIndex；不再附加“动画时采用文件帧率/范围、静态时保留时间设置”的规则。导入后仍选择新对象，不调用zoomext，不调整视口。

MainWindow 继续提供现有两个客户端，插件不新建连接。中台后台线程只协调文件和桥接，DCC API 全在各自进程执行。执行等待沿用现有长任务协议，Max接收最多600秒，客户端等待605秒。当前接收协议版本2、策略existing_import_settings；旧接收动作在导出前尝试重载并核对版本。Maya 导出预检要求 exporter_version=2、settings_policy=animation_only；旧导出动作同样在发送前重载。

2026-11-12移除根目录FBX共享设置模块：中台只把animation布尔开关传给Maya导出与Max接收；平滑组、切线、三角化、烘焙、全重采样、当前播放范围和采样步长由Maya导出动作直接设置。界面仍只保存“导出动画”，忽略旧高级选项，不新增公共目录或用户设置。Max接收的单位、轴向、时间及视口策略保持上述行为。

本轮FBX专项87/87通过；独立Max2020/Python2.7导出/接收结果断言通过，未原生复验Maya。开发全量45/47文件通过，另外两项为已有记录的旧动画数据及Max旧替身问题。详细证据、批处理外部启动错误限制见[Max→Maya验证记录](max-to-maya-fbx.md)。仅开发测试、未打包。

2026-11-12按用户要求，FBX直接位于 `paths.local_data_dir('maya_to_max')/selection-<唯一标识>.fbx`，即 `qz/temp/maya_to_max/` 内，不再为每次FBX发送创建send-*目录。独立文件名避免并行卡片或失败任务互相覆盖，无需手选路径。成功删除，失败时保留已存在的FBX并记录完整文件路径；导出尚未产生文件时不保留空目录，也不输出无用的保留路径。之前的send-*目录不自动删除。操作不自动重试；超时不意味着宿主任务已取消，失败也可能已有部分导入内容。

## 精简接收设置后的验证（2026-11-11）

`tests/test_fbx_to_max.py` 更新后24项通过，新旧两个FBX工具的专项均通过。覆盖预检不修改现有导入选项、两种动画模式均不写单位/轴向/时间设置、不重置导入器、不调用视口框选；旧接收版本1会在发送前重载，版本2才继续执行。

独立Max2020/Python2.7再次通过原生接收。测试主动分别准备转换开启/关闭、Z/Y轴、ScaleFactor=1/2及不同时间选项，接收前后逐项比较选项不变；动画与静态均通过。原有8顶点权重、11帧位置与不同系统单位测试也通过，但其中转换/时间选项由测试场景主动设置，不表示正式插件仍会设置这些选项。新结果为 `build/maya-to-max-options-20261111/max-receive-result.json`，专项日志为 `build/maya-to-max-options-specialized.log`。批处理仍有本机外部工具启动错误，结论依据功能结果文件；未操作当前工作场景，未打包。Maya独立进程的初始化限制未改变。

重启开发中台、在Max重载动作库后使用新版；Maya若仍提示unknown action: fbx_export_max，也需在Maya重载动作库。

## 第一版验证记录与限制（2026-11-11，含已移除的设置）

- `tests/test_fbx_to_max.py`：23项离线测试通过，覆盖选中组件/完整骨骼列表、无历史模型、异常恢复选择、动画开关、坏文件/参数在清场前拒绝、旧桥拒绝、追加同名保护、缓存版本检查、超时文件保留，以及真实 Qt 点击、后台客户端复用、UI线程日志和设置保存。
- 完整开发回归44/46文件通过。`test_js_anim_tools.py` 旧动作文件的time_unit与测试场景不符；`test_max_actions.py` 的旧替身拒绝现有顶点颜色模块导入时的rt.execute。排除本任务新增的两个DCC动作后，两项仍返回失败，详见下列baseline日志。没有改动这两个模块来掩盖失败。
- 独立 Max2020.3.7 / Python2.7.15 原生验证通过：追加、重复发送同名保护、打开后未命名、8顶点Skin与普通权重、动画30fps与0～10帧、静态发送保留25fps/5～90帧。全11帧顶点世界坐标以0.001厘米容差比较通过；厘米、默认英寸、2英寸/单位、米制均通过，目标系统单位比例保留。测试源FBX由既有Max导出器生成，验证的是Max接收链路，不代表Maya导出已原生通过。
- 批处理环境仍有本机SEASUN外部工具启动报错并以非零退出；上述原生结论来自结果文件逐项断言，不能宣称整个批处理环境无错误。
- Maya2024 mayapy在import maya.standalone时报DLL初始化失败；独立mayabatch返回0xC0000142，未进入本工具。Maya原生导出及完整Maya→Max数值验证尚未完成，不能用离线替身或Max自导自入替代此结论。已提供 `tests/maya_fbx_send_integration.py` 供独立batch环境继续验证，禁止在交互工作场景运行。
- 未操作用户当前 Maya/Max 场景，未打包、提交或覆盖其他任务功能。

证据位于实际开发目录：

- `build/maya-to-max-specialized.log`
- `build/maya-to-max-regression.log`
- `build/maya-to-max-native-20261111/max-receive-result.json`
- `build/maya-to-max-native-20261111/fps-probe.json`
- `build/maya-to-max-native-20261111/baseline-test_max_actions.py.log`
- `build/maya-to-max-native-20261111/baseline-test_js_anim_tools.py.log`
- `build/maya-to-max-native-20261111/maya-startup.json`
- `build/maya-to-max-native-20261111/mayabatch-startup.json`

参数参考：[Maya FBX导出命令](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Interoperability/files/GUID-6CCE943A-2ED4-4CEE-96D4-9CB19C28F4E0.htm)、[Max2020 FBX导入参数](https://help.autodesk.com/cloudhelp/2020/ENU/3DSMax-MAXScript/files/GUID-77CE3E5A-7AB1-4048-BD80-B127E0231FFA.htm)。帧率与单位结论优先采用本机原生结果。
