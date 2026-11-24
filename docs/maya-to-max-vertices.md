# Maya 世界顶点位置发送到 Max

在现有「选中物体发送到Max」卡片下方新增「发送顶点位置到 Max」按钮。先在Maya选择模型，再点击按钮，将模型自身缩放视为 1 后的全部世界顶点位置按编号应用到Max同名模型。带蒙皮时读取 skinCluster 输入源网格，不发送当前姿势下的蒙皮输出。不发送FBX、不新建模型。

## 已确认的规则

- 两端顶点数量与编号应一致，仅校验数量，不做空间匹配或重新排序；数量相等并不能证明拓扑编号对应，编号一致由用户制作流程保证。
- 名称使用Maya所属transform短名，保留namespace；沿用既有权重传输约定，只将FBXASC032解码为空格。源列表解码后重名、Max同名不唯一或找不到时跳过并记日志。
- Max写入Editable Poly/Editable Mesh的基础网格。保留Skin等修改器及其参数，不塌陷、不自动转换基础对象。仍由原堆栈继续求值；带变形修改器时最终显示形状可能继续变形，本功能不反求Skin/Bend等修改器的逆变形。
- Maya使用API2.0读取对象空间顶点：带单个 skinCluster 时读取其对应几何索引的 inputGeometry，保留蒙皮上游的建模/变形结果，排除蒙皮及其下游变形；没有 skinCluster 时读取当前求值网格。多层 skinCluster 无法唯一确定源网格、或蒙皮输入/输出顶点数不同，跳过并记录原因。选组件时仍发送所属模型全部顶点。
- 坐标计算视为所属transform的局部 scaleX/Y/Z 均为 1，自动抵消实际缩放（包括 2.54、非均匀及负缩放），不硬编码英寸厘米比例。保留位移、旋转、枢轴、剪切及父级/offsetParentMatrix的实际贡献，不清除父级缩放。零或无效局部缩放跳过。这里只计算导出坐标，不改Maya缩放、绑定、姿势、选择、当前帧或场景。
- 缩放补偿按Maya矩阵顺序：原局部矩阵以 `SP^-1 * S * SH * SP ...` 开始，导出矩阵为 `SP^-1 * S^-1 * SP * worldMatrix`，等效替换自身缩放矩阵S为单位矩阵。围绕实际缩放枢轴计算，不能将世界XYZ整体除以2.54，否则位移也会被错误缩放。
- 不改两端场景单位、不另做跨软件单位/上轴换算。Maya API距离值使用内部单位；这里不另转成UI显示单位。Max将补偿后的数值视为其世界坐标，再通过objectTransform的逆矩阵转为基础对象局部坐标，包含Max父级、旋转、缩放和对象偏移的影响。
- Max基础对象若被其他实例共用，跳过以免修改其他模型；不复制/解除实例。不可逆或精度不足的变换、非有限数、错误文件均在写入前拒绝。操作不会选择其他Max对象、切换视口或修改帧率/时间范围。

## 实现

- `maya_actions/vertex_positions.py`：Maya主线程预检、蒙皮输入采集与自身缩放抵消，按顶点编号写little-endian float64 XYZ文件。源协议版本2，`positions=skin_input_or_evaluated`、`scale_policy=local_scale_one`；Max接收协议仍为1。
- `max_actions/vertex_positions.py`：Max2020/Python2.7主线程预检、同名与数量检查、世界→局部坐标换算、批量setVert写入基础对象。使用getVerts/setVert的#all批量路径，不依赖当前坐标系统或修改面板。
- `ui/vertex_positions.py`：借用MainWindow现有客户端，后台串行协调两端；大数组走本机二进制文件，JSON仅传尺寸与名称。两端动作缺失、旧版或采集策略不匹配时在采集/写入前重载并核对能力；采集返回策略再次检查，避免缓存的旧世界坐标写入Max。
- `plugins/maya/fbx_to_max/widget.py`：新增按钮独立一行，沿用公共样式、无tooltip、后台执行，运行期间禁用卡片操作，结果由UI线程进入中台日志。原FBX打开/导入/设置继续使用原流程。

每个目标在写入前快照原基础顶点，临时关闭动画上下文，批量写入后读回校验；普通异常时恢复原顶点并再次校验。恢复失败明确报告目标可能部分更新，不伪装成功、不自动重试。原生宿主崩溃、强制结束或无法捕获的SDK故障不能保证回滚。本功能不承诺Ctrl+Z撤销。

交换目录为 `qz/temp/vertex_positions/qz-vertex-transfer-*`，每个模型一个 `mesh-N.points`。这里需要多文件任务目录，与FBX单文件直接放在 `temp/maya_to_max/` 的规则分开。全部成功时清理本次目录；部分失败/超时则保留并写入日志，等待用户确认后再发送。Max执行等待300秒、客户端305秒；超时不代表宿主已停止写入。

## 验证（2026-11-12）

- `tests/test_vertex_positions.py`：22项通过。新增覆盖2.54缩放补偿且不缩放位移、非均匀/负缩放/枢轴/旋转/剪切/父级保留、蒙皮输入而非姿势输出、离线双端基础网格读回、多层蒙皮/输入数量错误/零缩放跳过、源策略刷新和采集返回二次校验。继续覆盖float64顺序、Max仿射换算、Mesh/Poly与保留修改器、缺失/重名/数量错误、实例和不可逆矩阵拒绝、坏文件/非有限数、写入恢复、超时保留、真实Qt按钮点击及线程状态。
- 独立Max2020.3.7/Python2.7.15原生测试通过：Mesh、Poly、带Skin；父级变换、旋转、非均匀/负缩放、objectOffsetPos；基础网格世界坐标读回容差0.001，原变换/修改器/选择/AutoKey状态保留；错误数量与实例拒绝。零缩放被Max钳制后造成精度不足时，正式动作拒绝写入且原基础顶点不变。
- 10201顶点网格原生批量写入与读回通过，单轮Max动作约0.355秒，不含Maya采集、桥接或中台开销，不作为用户模型的总耗时保证。批处理仍带本机外部工具启动报错并非零退出；上述结论来自逐项校验结果文件，不代表整个宿主环境无错。
- Maya2024独立mayapy仍在import maya.standalone时DLL初始化失败，未进入采集代码；尚未完成真实Maya采集或完整双端原生验证。已更新独立 `tests/maya_vertex_positions_integration.py`：仅允许隔离batch进程，使用实际把fixture缩放设1、关闭蒙皮envelope再恢复的方式建立独立参照，覆盖2.54、负缩放、父级、枢轴、rotateAxis、剪切、offsetParentMatrix、inheritsTransform、蒙皮及其上游blendShape，并检查正式动作不改场景。此原生fixture尚未运行到断言，不宣称原生通过。没有操作用户当前场景来绕过此限制。
- 全量开发回归45/47个文件通过，两项仍是此前已确认的旧动作时间单位测试和Max动作旧替身/顶点颜色加载冲突。此次没有修改相关模块。未打包。

本轮证据：工作区 `build/vertex-scale-specialized.log`、`build/vertex-scale-regression.log`；实际运行目录 `build/vertex-positions-20261112/maya-startup.json`。原Max验证仍见 `build/vertex-positions-20261112/max-vertex-result.json`，本轮未改Max接收端。

生效：重启实际开发目录的中台；在两端重载动作库，也可由新按钮预检自动刷新缺失动作。Max基础网格需已存在。

API参考：[Maya MFnMesh](https://help.autodesk.com/cloudhelp/2024/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_fn_mesh.html)、[Maya MDistance](https://help.autodesk.com/cloudhelp/2026/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_distance.html)、[Max Poly顶点方法](https://help.autodesk.com/cloudhelp/2023/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-07318A19-8091-4B3E-BB46-463942B0F39B.html)、[Max2020 Mesh顶点方法](https://help.autodesk.com/cloudhelp/2020/ENU/3DSMax-MAXScript/files/GUID-483BD7AC-832C-442C-8820-65EEC52815C4.htm)。

缩放与蒙皮源几何参考：[Maya transform矩阵顺序](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Tech-Docs/Nodes/transform.html)、[MFnTransform](https://help.autodesk.com/cloudhelp/2024/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_fn_transform.html)、[MFnSkinCluster](https://help.autodesk.com/cloudhelp/2025/ENU/MAYA-API-REF/cpp_ref/class_m_fn_skin_cluster.html)。
