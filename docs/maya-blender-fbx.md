# Maya / Blender 选中物体互传

在中台连接 Maya 和 Blender，在源端选中物体，再打开对应的「选中物体发送到 Maya / Blender」卡片。物体互传使用 FBX，网格可携带关联骨架与已有蒙皮。Blender 在物体模式下导出基础网格，不应用可能改变拓扑的修改器。更新后重启中台；已加载旧桥接的 Blender 也需要重启并重新连接。

## Maya → Blender

- **打开到 Blender**：确认后，用本次发送生成的独立 `.blend` 替换当前场景。确认默认取消；暂存导入或写出失败时保留当前场景。成功后，生成的 `.blend` 留在本机交换目录，路径见日志。
- **导入到 Blender**：把物体加入当前场景。齿轮里的「导出动画」默认关闭；开启后导入动画关键帧，保留 Blender 当前场景帧率和 FBX 中的关键帧帧号。
- **发送顶点位置到 Blender**：只在 Maya 选一张源网格。插件在 Blender 当前场景查找与 Maya 网格所属 transform 短名精确相同的唯一网格。仅当原名没有网格候选时，才去掉 Maya 命名空间再精确查找。Blender 须处于物体模式，目标数据可写、未被其他物体共享且没有形态键。插件按米制世界坐标、同索引面和边拓扑及未移动的唯一坐标锚点确认对应关系。找不到、重名或无法确认时停止写入。成功时只改基础顶点位置，保留顶点组和 Blender 选择状态。

## Blender → Maya

- **打开到 Maya**：明确确认后，在新场景载入本次发送内容。确认默认取消，原 Maya 场景文件不会被覆盖。
- **导入到 Maya**：把物体加入当前场景，每次分配独立的 `blender_` 命名空间。
- 齿轮中的「导出动画」和「导入后优化显示」可分别设置，默认都开启。开启动画时 Maya 会采用 FBX 的帧率和时间范围；开启显示优化时会调整骨骼与视口显示。

## 实测记录

使用 Blender 3.3.5 和 Maya 2024，选中一张已蒙皮的 5 顶点网格，骨架保持未选中。经过 Blender → Maya → Blender，验证了这张网格的顶点顺序和逐顶点蒙皮权重。

| Blender 原名 | Maya 导入名（去掉命名空间） | 返回 Blender 后的组名 |
| --- | --- | --- |
| `Space Bone` | `SpaceFBXASC032Bone` | `Space Bone` |
| `Dot.Bone` | `DotFBXASC046Bone` | `Dot.Bone` |
| `中文骨` | `FBXASC228FBXASC184FBXASC173FBXASC230FBXASC150FBXASC135FBXASC233FBXASC170FBXASC168` | `中文骨` |
| `左手.指节 01` | 中文转成 UTF-8 字节的 `FBXASCnnn` 序列，点和空格分别转成 `FBXASC046` 和 `FBXASC032` | `左手.指节 01` |
| `SpaceFBXASC032Bone` | `SpaceFBXASC032Bone_ncl1_1` | `Space Bone.001` |

这份结果说明批量反解字符串不足以处理名称冲突。Maya 蒙皮权重卡片的「发送权重到 Blender」是独立流程：先验证活动目标网格的顶点对应，再在绑定骨架中匹配原名和 FBX 转义候选。缺失、歧义或重复匹配都会在写入前拒绝。

升级后的 Maya → Blender 已用 Maya 2024 与 Blender 3.3.5 验证；Blender → Maya 已用 Blender 5.2.4 与 Maya 2024 经真实桥接验证。两方向均覆盖打开、导入和动画；另分别验证了发送顶点位置和 Maya 显示优化。

复测：将环境变量 `QZ_TEST_BLENDER` 设为 Blender 可执行文件的绝对路径，再执行 `.venv/Scripts/python.exe tests/run_maya_native.py tests/maya_blender_fbx_integration.py`。
