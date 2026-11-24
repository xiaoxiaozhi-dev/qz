# Max 蒙皮权重插件

目标宿主为 3ds Max 2020 / Python 2.7；不扩展 Python 3 宿主支持。中台继续使用既有 Python 3、PySide6 和 NumPy，Max 端无 NumPy / PySide6 依赖。

## 功能和界面

插件位于 `plugins/max/skin_weights/`，内部键 `(max, skin_weights)`，显示“蒙皮权重”。独立 Max 卡片，沿用公共 QSS；包含分配方式、最大影响数、当前检测值、检测/选择/修改三个按钮、文件导出/导入两个按钮及导入方式下拉框（默认按顶点编号，可选按最近表面点）。无 tooltip 或跨 DCC 发送按钮。耗时工作使用中台线程池，日志在 UI 线程写入；忙碌期间禁用相关控件。

Max `skin_weights_max` 动作按当前选中模型采集单个 Skin 的权重到 little-endian float64 密集交换文件。只统计正权重；全零、负数或非有限数由中台共用 `ui.skin_weights.process_snapshot` 报告。零基编号只用于交换，用户日志及 Skin 选择均转换为 Max 的一基编号。检测不会写权重。

限制操作复用中台现有比例/平均分配及三位小数整数余量归一化，仅对有效超限顶点调用 `ReplaceVertexWeights`，不初始化整个 Skin；其他顶点与所有 DQ 保留。临时放宽写入上限、暂停被修改顶点自动归一化，写入后恢复该顶点原标记；成功后设置 Skin.bone_limit 为界面上限。操作只应用任务开始时捕获的模型/Skin，不跟随途中选择变化。异常时尝试从初始快照恢复已尝试顶点和原上限，明确报告恢复错误；正常成功不读回逐点复查，不增加 Undo 命令。

选择功能为各成功模型设置 Skin 顶点选择，包括无超限时清空旧选择。最后一个成功模型保持激活的 Skin 和子对象级别 1，便于直接查看；其他模型的选择保存在各自 Skin 中，切换模型即可查看。检测/限制/文件操作恢复原对象选择，但不承诺恢复操作前修改面板、当前骨骼或子对象编辑状态。

## 文件导出与导入

缓存为中台项目根目录的 `temp/skin_weights/模型名.qzskin`，与 Maya 共用同一目录及同名权重文件，持久保留，两边导出均对同名文件原子覆盖；不弹目录框，不读取 Max 场景目录。重启中台后扫描到新插件，Max 重新连接并重载动作库后可使用。

保存沿用现有 `.qzskin` ZIP/CSR 格式，Max 当前写版本 3（兼容旧 v1/v2 按编号导入）：完整普通权重和每顶点 DQ 均为 float64 容器，不受界面最大影响数或分配方式影响，不量化、不裁剪、不额外归一化。它保存的是 Max API 返回的原数值，不能超越宿主自身的浮点精度。元数据 `source=max`，`skinning_method=0/2` 分别记录 Max enableDQ 关闭/开启；另保存 `max_bone_limit` 和零基 `max_unnormalized_vertices`，保证非单位和的原始权重可以恢复。开启/关闭 DQ 时均保存及恢复潜在 DQ 数组。另保存Skin输入的object空间坐标和三角形，用于可选最近表面点导入；坐标不量化。

导入按操作开始时选中模型名读取文件；默认按顶点编号要求顶点数一致，可选按最近表面点允许顶点数变化。复用中台完整文件/数值检查，支持现有 v1/v2/v3 的按编号数据，最近表面点要求含几何数据的v3。所需正权重骨骼必须在场景唯一存在；只兼容 FBXASC032 空格编码，不删除命名空间或模糊匹配。检查通过后，无 Skin 则创建，缺影响骨骼则添加；按骨骼名称重新映射并清零目标旧影响。Skin 的列表编号与系统骨骼编号不同，`GetBoneIDByListID` 转换后再读写，支持删除骨骼后产生的编号空洞。

文件完整导入按条件选择批量或逐点写入，暂时暂停归一化，恢复源未归一化标记；旧文件没有该记录时，非单位和行保持为未归一化。完成后恢复源 DQ 开关；已有Skin恢复文件记录的bone_limit，新建Skin按用户要求固定为4（两者均不在导入计算中裁剪源数据）。旧 v1 没记录方式时保留目标开关并提示。Max2020中不少于256点、骨骼系统ID连续、源与目标均归一化且容量合适时，复用已验证的两次SetWeight初始化和按骨骼批量覆盖；其他情况使用缓存接口的完整逐点兼容路径。

缺骨骼、重名、按编号顶点不匹配或多 Skin 在写入前跳过该模型并报告。已有 Skin 中途写失败可能部分更新，报告已尝试数量，不声称恢复完整旧数据；当前顶点的临时归一化标记和旧上限会尝试恢复。新建 Skin 导入失败删除本次 Skin。其他模型独立处理，导入后当前检测显示需重新检测。按编号恢复原对象选择；空间导入成功时保留最后成功模型的Skin插值点选择。

## 验证

新增 `tests/test_max_skin_weights.py` 21 项，覆盖检查/选择/两种限制策略、异常一基编号、只改超限行与 DQ 保留、失败恢复、完整数值/开关/非单位和往返、新建 Skin、缺骨骼、顶点不匹配、文件损坏、原子替换保护、4105 顶点分批尾块、骨骼重排和旧影响清零、源小上限不截断、Max 字面名称不被导出解码、同名缓存冲突，以及真实 Qt 五按钮后台回调与日志线程。

真实独立 3ds Max 2020.3.7 / Python 2.7.15 批处理进程，9 顶点/4 影响节点，实际执行中台 Python 3 数值及文件代码，验证检测/选择/限制、子对象级别1可查看选点、未修改行与全部 DQ 保留、完整文件恢复、骨骼编号空洞及补影响、删除后重建 Skin、DQ 关闭时潜在 DQ 恢复。逐项读回以 1e-6 容差验证，并确认 Max 未加载 NumPy。证据 `build/max-local-skin-native-result.json`；卡片预览 `build/max-skin-weights-preview.png`。这些是临时小模型功能验证，不是大模型性能基准；未通过当前交互桥验证，因为本轮开始时端口17024没有响应。批处理环境既有外部 SEASUN 启动脚本报错导致进程退出码1，功能结论来自结果文件的全部断言，不能称整个宿主启动无错误。

本轮只开发验证，不执行打包；仅登记新中台模块的 hidden import，保证未来正式构建依赖齐全。既有 Maya 卡片与 Max 接收 Maya 权重动作保持原行为，CSR 转换新增 `decode_names=False` 可选参数供本地 Max 文件使用，原发送默认仍解码空格。
最终完整开发回归：36/36 个测试文件全部通过，含新增21项Max蒙皮专项、既有Maya蒙皮/文件/传输、插件树与统一样式检查。


## 六顶点检测的重复界面激活优化（2026-11-09）

用户报告相同6点模型Maya检测0.009秒、Max检测1.283秒。本轮在当前交互Max连接上仅执行只读检测，复现连续三次1.379441/1.378913/1.312022秒。临时分段探针显示每次snapshot里两次强制选择模型约0.47～0.57秒，两次强制setCurrentObject约0.42～0.46秒，结束后再重新选择原对象约0.22～0.23秒；骨骼/权重采集本身很短。瓶颈是首版重复重建修改面板，不能将这1秒多归因于6顶点权重计算或NumPy。

max_actions/skin_weights.py增加本地条件激活：仅目标选择、面板模式或当前Skin不同时变更；切换节点时即使共用同一个实例Skin仍设置节点上下文。采集第二次调用只确认现有上下文，不重复重建面板；恢复对象选择时，完全相同则跳过rt.select。现有Maya→Max接收动作的强制激活逻辑未变。

snapshot新增retain_task=True兼容默认值；中台纯check传False，根本不创建后续写入任务，避免再排队调用release。成功select/limit会消耗任务，中台不再额外release；计算失败或未正常返回仍保留finally释放。旧中台不传新参数仍沿用原任务机制，重启中台才会获得单请求检测的完整收益。Max正式动作已重载。

同一Pyramid001、6顶点/3个Skin骨骼、有效最大影响1、Skin已激活时，优化后三次0.106986/0.114628/0.112550秒；中位数1.378913→0.112550秒，降低91.84%。各次检测报告完全相同，期间未写权重/DQ。优化后Max内部snapshot约1～2毫秒，其余主要为既有100ms主线程桥接调度及端到端开销；未改桥接timer，不能承诺与Maya0.009秒相同。首次切换到别的模型/Skin仍需必要的界面激活，此基准不代表任意场景/大模型性能。

证据build/max-skin-detection-before.json、max-skin-detection-after.json及max-skin-detection-comparison.json；临时性能动作已从注册表和源码删除。新增测试覆盖已激活不重建、首次激活仅一次、实例Skin换节点必切上下文、纯检测单桥接请求、成功应用不重复释放、计算失败释放。仅开发目录验证，不打包。
本轮最终验证：Max专项26项及完整36/36测试文件通过；独立Max2020/Python2.7原生功能回归全部断言通过，证据build/max-local-skin-activation-native-result.json。批处理仍有既存外部启动脚本错误（退出码1），与首版验证相同，不能把退出码当作功能通过证据。当前交互Max正式动作已重载，用户重启中台后可加载完整单请求优化；未打包。

## 大模型 Skin 读取接口缓存（2026-11-09）

用户报告27308顶点模型Maya检测0.234秒、Max1.230秒。只读实测F1_5587v_body含163个Skin骨骼，密集快照35609632字节。优化前连续检测0.899528/0.796579/0.824541秒；分段交替试验里Max读取/展开中位约0.678秒、写文件约9毫秒、中台读约11毫秒、NumPy约9毫秒、桥接约79毫秒。性能探针额外计算SHA256，max_other含该校验开销，不应计作正式检测的业务耗时。

只在max_actions/skin_weights.py的_capture中把GetVertexWeightCount、GetVertexWeightBoneID、GetVertexWeight保存为局部可调用对象；文件导出时同样缓存DQ及未归一化查询。避免每个顶点/影响都重新解析pymxs接口属性，不改算法、文件格式、精度或异常阈值。交替只读试验读取/展开约0.678→0.272秒，所有快照SHA256一致。

用户恢复原模型后，正式检测五次1.037291/0.512426/0.439290/0.432888/0.427950秒；首次含重新激活开销，中位0.439290秒，对比本轮优化前中位0.824541降低46.72%。这不是将用户单次1.230秒直接当作稳定基线。模型刚打开或换Skin时仍有必要界面激活，不能保证首次也为0.44秒。

移除临时探针后直接调用正式snapshot：Max采集总计0.298000秒（含准备、展开及写文件），桥接余量0.073387秒，中台读0.013088秒、NumPy0.010863秒。完整快照SHA256为ef26d3b7df15c91fd5485dbd2a25fe66177cadee6ad5b54210becf1427217021，与优化前完全相同；hash检查在采集/桥接计时之外。此前探针重建源码仅用于候选对比/参考校验，最终正式耗时以这次正式动作和五次check为准。

异常顶点11045实际有一项权重-1.2068729127179267e-08，另两项为0.5/0.5。它确实是Skin接口返回的微小负数；仍按用户既定“任意负数报告异常”规则，不自动改成0或引入容差。整个交互测试未写用户权重或DQ，期间原模型暂时不可用时未选择其他模型代替基准，待用户恢复后继续。

证据build/max-skin-large-read-profile.json、max-skin-cached-read-verification.json、max-skin-cached-production-snapshot.json。Max专项26项、完整37/37测试文件通过；独立Max2020/Python2.7原生功能回归全部断言通过（build/max-local-skin-cached-native-result.json），Max未加载NumPy。批处理仍有既存外部启动脚本错误，不能以退出码当功能结论。临时性能动作已注销且源文件移除，当前交互Max正式动作已重载，可以直接重新检测；仅开发测试，不打包。


## 连续检测偶发尖峰与分阶段计时（2026-11-09）

同一27308顶点模型连续40次正式只读check：最小0.335729秒、中位0.364976秒、最大1.034764秒。三次约1.03秒尖峰多出的约0.65秒落在Max准备或读取/展开，桥接余量保持正常，不能由100ms主线程调度解释。证据build/max-skin-timing-spike-probe.json。

随后仅临时包装真实_stage，观察每阶段前后Max heapFree/heapSize及Python gc计数，不改变堆大小、不禁用或强制垃圾回收。另40轮中位0.398904秒，三个尖峰1.035085/1.088742/1.070957秒；对应读取小段0.658/0.714/0.668秒，heapFree从1.36/1.15/1.34MB跃升至约364MB，heapSize始终400MB，Python gc计数在对应小段前后不变。三次桥接余量0.067/0.066/0.063秒。结合Autodesk文档所述“堆空间不足时自动扫描回收并暂停”，证据定位为MAXScript自动内存回收；临时pymxs调用结果积累使连续检测约每12～13轮出现一次，但其他脚本/场景操作也共享堆，不能保证固定频率。不能把观测探针额外开销当作进一步性能退化。

正式动作增加max_prepare/max_read_expand/max_file_write/max_apply/max_restore/max_other分段；公开action_seconds覆盖finally恢复选择。中台汇总host_file_read/host_file_write/numpy_compute、桥接余量和本地清理。桥接余量包含传输/序列化/主线程排队，并非纯timer等待。旧动作没有分段时标为Max未分段。正常小幅波动仍可能来自既有100ms调度与系统调度。中台重启后显示新日志，Max正式动作已重载。

新增四项计时回归（Max专项共30项），覆盖恢复选择耗时、桥接扣除远端、旧动作兼容及端到端日志，不改变权重算法、精度、异常规则。真实用户场景仅检测，未写普通权重/DQ。临时heap探针源码/字节码及注册均已删除，正式动作重载；原始观测证据build/max-skin-heap-spike-probe.json保留。未调整用户Max内存设置。

独立Max2020/Python2.7.15功能验证结果build/max-local-skin-timing-native-result.json的全部断言通过，含检测/选点/限制/完整文件往返与DQ保留，确认宿主未加载NumPy。批处理存在既有外部SEASUN启动错误且退出码非零（本轮-130），不将其描述为宿主进程干净退出。仅开发目录验证，不打包。

最终完整开发回归37/37测试文件通过（build/max-skin-timing-full-regression-repeat.log），Max专项30/30。首次完整回归桥接测试曾偶发失败，单独复测15/15及完整重跑均通过，未为此修改桥接代码。


2026-11-09 用户要求关闭分阶段耗时日志：Max卡片恢复仅输出总耗时，内部timings_seconds及计时保留用于开发诊断，Maya日志不变。中台重启后生效；未修改场景权重。


## Max 最近表面点导入（2026-11-09）

用户要求接着实现Max空间导入。修改前代码保存在backups/max-skin-before-space-20261109/，仅开发目录验证，不打包。新增max_actions/skin_weight_geometry.py，修改Max动作、中台协调层、Max独立卡片及专项测试；Maya动作、共享插值算法、跨DCC传输规则不变。检测仍只显示总耗时。

新导出统一生成v3文件，路径仍为temp/skin_weights/模型名.qzskin。除原有完整float64普通权重/DQ、方式、bone_limit及未归一化标记外，增加float64坐标与uint32三角形。几何取Skin输入：临时禁用Skin与其上方修改器，snapshotAsMesh后立即finally恢复原enabled状态；world坐标乘逆objectTransform还原object空间。真实Max已验证物体位移/旋转/非均匀缩放、骨骼移动、Skin上方Push的组合。两边仍需同名模型、同一局部坐标基准与所需骨骼；不做自动对齐。没有可用三角形的源网格不能导出空间数据。

下拉默认按顶点编号，此路径不读取/查询几何；旧v1/v2继续支持。主动选择最近表面点后，先用POSITION_MATCH_DECIMALS=3（仅开发常量）生成坐标键，唯一命中直接复制原普通权重/DQ。Python2使用float格式化生成与Python3一致的取整键，不使用Python2 round的不同中点规则。多重坐标命中走表面查询，不额外处理歧义。没有命中时用Max MeshProjIntersect原生加速查询，doubleSided=True；按文档先做一次IntersectRay初始化但丢弃该射线结果，GetHitFace为零基且读取命中数据不传hitIndex。临时源三角网格为单位变换，查询完成/失败均Free并删除节点。三角形比例先检查有限值，再钳制并归一化；中台复用现有NumPy interpolate_weights生成CSR普通权重与DQ，不量化、不裁剪最大影响数、不主动改变源非单位权重和。Max未归一化标记按源贡献点映射，原写入流程也保留非单位行。

只在成功空间导入后自动选中实际走过表面查询的顶点，包括投影刚好落在源三角形角点的顶点；成功时保留最后成功模型的Skin顶点层。按用户后续要求，Max不创建/更新顶点命名选择集，也不写AppData记录；全坐标命中时清空Skin顶点选择。按编号导入和写前失败不改这项选择。Maya的插值点set功能不变。已有Skin写失败仍可能部分更新，遵循原错误报告边界。

39项Max专项通过，完整37/37测试文件通过。新增覆盖重排/增点/直接复制精度、DQ和未归一化标记映射、三角形插值数值参考、角点投影选点、临时查询失败清理、旧文件仅按编号兼容、无效几何写前拒绝、修改器状态恢复、Python2/3坐标键、集合冲突/复用/清空和Qt下拉默认值。

真实独立Max2020/Python2.7.15结果build/max-skin-space-native-result.json为ok:true：3点源→5点目标（3点直接、2点插值）；普通权重与DQ全点读回对比，投影角点被选中，命名集合重复复用，全直接匹配清空旧集合，Editable Mesh与Editable Poly都验证通过；无临时节点泄漏，Max未加载NumPy。仅临时测试模型，尚未测用户大模型的空间导入耗时；未写当前交互场景权重。原生批处理环境仍有既有外部启动脚本错误，不能以宿主非零退出码直接判断功能成败，依据结果文件逐项断言。

最终原有功能原生回归也通过：build/max-local-skin-space-legacy-result.json为ok:true，涵盖编号往返、DQ开关/潜在DQ、未归一化标记、骨骼编号空洞补影响、新建Skin、检测/选点/限制。当前交互Max动作库已成功重载，中台重启即可显示新导入方式。



## Maya / Max 共用缓存（2026-11-09 用户纠正）

两边统一使用paths.project_dir()/temp/skin_weights/模型名.qzskin，不再使用Max子目录；同名文件共用，任一端导出都更新该文件。Maya/Max均支持v3普通权重、DQ、方式及几何；Max额外字段在Maya侧忽略，Maya文件缺Max字段时按原兼容默认处理；骨骼FBXASC032空格解码规则保持。新增专项使用真正Maya文件写入函数覆盖同一文件，再由Max导入，反向Max导出也由Maya公共读取器验证，确认无需复制/另存权重文件。没有移动或覆盖用户现有缓存。中台重启后生效，宿主动作无需为目录修改重载。


## 仅选择插值点与新建Skin显示默认值（2026-11-09）

用户明确要求Max不再增加选择集，仅保留导入后自动选中插值顶点。删除Max几何模块的命名集合、AppData持久记录、基底面板切换等逻辑；保留空间插值ID文件和成功写入后的Skin.selectVertices。旧场景已存在的集合/记录不主动删除，Maya端set不变。

Max卡片文件导入（编号及空间）新建Skin时，Display设置与用户截图一致：draw_vertices=False、shadeweights=False、colorAllWeights=False、draw_all_envelopes=False、draw_all_vertices=False、draw_all_gizmos=False，showNoEnvelopes=True、showHiddenVertices=True。导入完整权重时临时容纳实际影响数，完成后新建Skin.bone_limit=4；已有Skin显示选项不改，bone_limit仍沿用原文件恢复策略。检测/限制及发送Max的既有策略不变。

40项Max专项通过，完整39/39测试文件通过（当前开发目录新增的其他插件测试也被回归覆盖）。独立Max2020/Python2.7.15真实验证8项显示属性及新建上限4，确认未创建命名集合/旧AppData，普通权重/DQ、Skin插值点选择/重复导入/全匹配清空、EditableMesh和EditablePoly都通过；Max无NumPy。证据build/max-skin-selection-display-native-result.json与build/max-skin-selection-display-full-regression.log。宿主仍有既有外部启动错误，不将退出码作为功能通过依据。仅开发测试，不打包，未写当前用户场景权重。


## 文件导入批量与接口缓存优化（2026-11-09）

用户授权同时尝试缓存写入接口及复用发送Max的批量写入，明确不改桥接调度。仅改max_actions/skin_weights.py、共享skin_transfer.py的可选DQ参数及专项测试；中台文件格式、共享目录、UI和100ms桥接定时器不变。

逐点回退将isUnNormalizeVertex、unNormalizeVertex、ReplaceVertexWeights、setVertexDQWeight及Array绑定为局部引用，仍逐顶点完整覆盖，不量化、不裁剪。源非单位和、任意源/目标未归一化标记、骨骼系统ID空洞、容量不符或小模型继续走此路径。保持原源标记恢复、新建Skin显示默认与完成后上限4、已有Skin按文件恢复上限、线性模式也恢复潜在DQ等语义。

正常批量路径限定Max2020、FILE_BULK_MIN_VERTICES=256、连续系统骨骼ID、源/目标全归一化、权重<=1及单位和、现有100万条容量保护。复用skin_transfer的初始化/分组/写入；每次文件导入均先两次SetWeight建立稳定原生基底，不能只凭IsVertexModified=1跳过初始化。原生失败实验中，直接用顶点接口写过的数据即使有完整影响和修改标记，直接批量写仍会留错值；失败证据build/max-file-bulk-initial-native-failure.json。源与目标的未归一化情况保持逐点兼容，不为加速改变状态语义。

共享_write_bone_groups仅新增write_dq=None可选参数：默认发送逻辑仍依Maya源方式决定是否写DQ，文件导入显式True恢复潜在DQ，且只在验证后的全归一化路径使用。普通权重按骨骼批量写入，DQ/归一化状态仍有逐点调用；写入失败不自动重试顶点路径，已有Skin报告可能部分更新，新建Skin失败移除。新增/缺失骨骼复用发送流程的“中间addBone update=0，最后一次update=1”，减少重复求值，未改变名称映射或骨骼完整性检查。

真实独立Max2020/Python2.7.15：642点/8骨骼与27360点/32骨骼，优化前后交替各3轮，分别测试已有Skin、新建Skin、非单位和逐点回退，共36次全顶点读回检查普通权重、所有DQ（源线性模式）及未归一化标记，误差容限1e-6，全部通过，Max未加载NumPy。计时为Max内部完整_import_one（新建场景包含Skin创建、添加骨骼），排除删除测试Skin、中台读写、空间匹配、桥接及读回验证；不作为用户端到端时间或任意模型速度承诺。

三轮中位数（秒）：

- 642点 existing：0.028852 → 0.024844，降低13.89%。
- 642点 new：0.287805 → 0.251900，降低12.48%。
- 642点 nonunit：0.051745 → 0.042518，降低17.83%。
- 27360点 existing：1.732976 → 0.954370，降低44.93%。
- 27360点 new：8.179433 → 3.428874，降低58.08%。
- 27360点 nonunit：3.003277 → 2.576542，降低14.21%。

642点小模型已有Skin内部仅省约4ms，用户总耗时仍受未修改的桥接等待影响，不承诺接近Maya。大模型新建收益同时包含统一更新骨骼，不能全部归因于SetBoneWeights。

46项Max专项及完整39/39测试文件通过；既有发送Max专项34项继续通过。独立真实文件往返/骨骼ID空洞/缺骨骼添加/新建Skin/关闭DQ时潜在DQ恢复（build/max-file-bulk-legacy-native-result.json）与空间插值/DQ/直接Skin选点/无选择集/显示默认值/Mesh及Poly（build/max-file-bulk-space-native-result.json）全部断言通过。基准明细build/max-file-bulk-native-result.json，汇总build/max-file-bulk-performance-summary.json，完整回归build/max-file-bulk-full-regression.log。原批处理环境仍有既有外部启动错误，不以宿主退出码宣称整体启动干净。测试只在开发目录进行，不打包，未写用户当前场景权重。


## 导入前关闭权重工具（2026-11-09）

文件导入共用入口在激活已有 Skin 后，通过 skinOps.isWeightToolOpen 检查并调用 skinOps.closeWeightTool 关闭浮动权重工具，随后才添加骨骼、初始化和写入权重。按编号与最近表面点导入均适用，导入后保持关闭。新建 Skin 无需关闭窗口；未修改桥接调度或超时参数。
