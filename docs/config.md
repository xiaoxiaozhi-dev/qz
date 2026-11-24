# 当前配置位置（2026-11-13）

官方默认值改为根目录 defaults.json，个人覆盖配置为 data/settings.json，
启动时递归合并。布局和日志上限/置顶在 data/layout.json，配色在
data/theme.json。首次启动复制旧根目录 config.json 及 ~/.qz-tool 的设置，
保留原文件，已有新设置不覆盖。发布更新不覆盖 data/。

下文端口等字段规则仍适用；历史描述中的可编辑 config.json 现在对应
data/settings.json，官方 defaults.json 不作为个人设置文件编辑。
详见 [packaging.md](packaging.md) 与 [development.md](development.md)。

# 中台配置说明

配置文件是 **`config.json`**（项目根目录），格式 JSON。

> **为什么从 YAML 改成 JSON（2026-10-19）**
>
> 原先 `config.yaml` 是项目里唯一的 YAML 文件，为了它引入了 PyYAML 依赖。
> 但全项目一共 11 个配置文件：`config.yaml` 1 个，
> `plugins/<dcc>/<id>/manifest.json` 10 个，`~/.qz-tool/layout.json` 1 个 ——
> **只有 1/11 用 YAML**，却多了一个第三方依赖（还有 PyYAML 的 CVE 历史）
> 和两种配置格式。收益（能写注释）远小于代价，所以统一成 JSON。
>
> 注释全部搬到本文件，配置文件保持干净（配置和文档分离是好实践，
> 干净的配置也更容易进版本库、拷贝、发给同事）。

---

## 完整示例

```json
{
  "bridge": {
    "host": "127.0.0.1",
      "ports": { "maya": 17023, "max": 17024, "blender": 17025 },
    "timeout": 10,
    "interval": 10
  },
  "ui": { "theme": "dark", "remember_layout": true },
  "plugins": { "dir": "" }
}
```

---

## bridge

### host
桥接服务监听的地址。默认 `127.0.0.1`，一般不用改。

### ports
各 DCC 的端口。

⚠️ **不需要指定当前连哪个**。中台独立探测 Maya、Max 和 Blender 的连接。

这一条最容易误解。历史上确实有过 `dcc` / `port` 字段（"切换 DCC"），
2026-10-18 双 DCC 改造时已经删掉了，现在按 `ports` 下的软件名配置。

| 键 | 默认 | 说明 |
|---|---|---|
| `ports.maya` | 17023 | Maya 端 `maya_bridge/server.py` |
| `ports.max` | 17024 | Max 端 `max_bridge/server.py` |
| `ports.blender` | 17025 | Blender 端 `blender_bridge/server.py`，连接代码使用此端口 |

各宿主的 server 都用 `SO_EXCLUSIVEADDRUSE`（Windows 上不能换成
`SO_REUSEADDR`，它会静默抢占已 listen 的端口，bind/listen 都成功、
日志照打，但新 socket 根本不服务，**零报错**）。所以端口被占时
会明确抛 10048。

⚠️ **同时只能开一个 3ds Max** —— 两个 Max 都要绑 17024，
第二个会起不来（这是设计上的已知限制）。

### timeout
单次调用的超时秒数。默认 10。

DCC 端主线程卡住时（比如 Max 正在弹一个模态对话框），
调用要等这么久才返回。调小会让慢操作误报失败。

### interval
探测间隔（秒），无论连上还是断开都按这个间隔 ping。

- **连上** → 心跳，用来发现 DCC 被关闭
- **断开** → 自动重连

实测 ping ≈ 2.7ms，10 秒一次约 0.03% CPU，可忽略。
自动重连**常驻开启，没有开关** —— Maya/Max 重启后中台要能自己连回去。

设置页桥接行显示连接/断开按钮，替代地址和独立的临时启动按钮。未连接时点击“连接”直接复制对应软件的单行 Python 启动代码，不尝试连接或弹窗；代码在软件端执行后由后台自动检测连接。“断开”会让相应软件端关闭监听端口；后台检测继续，但只有重新启动软件端桥接后才会连接回来。断开一端不影响另一端。连接反馈、旧桥接升级和操作说明见 [bridge-connections.md](bridge-connections.md)。

---

## ui

| 键 | 默认 | 说明 |
|---|---|---|
| `theme` | `"dark"` | 保留字段，目前界面固定深色 |
| `remember_layout` | `true` | 保留字段，目前窗口位置总是持久化 |

---

## plugins

| 键 | 默认 | 说明 |
|---|---|---|
| `dir` | `""` | 插件目录。留空则用 `<项目根>/plugins` |

结构：

```
plugins/<dcc>/<plugin_id>/manifest.json
plugins/<dcc>/<plugin_id>/widget.py
```

`<dcc>` 只能是 `maya` 或 `max`。**manifest 的 `dcc` 字段必须与目录名一致**，
不一致会被跳过（防止插件放错目录却以为是另一个 DCC 的）。

> ⚠️ 这个目录**不能放进 PyInstaller 的 `--onefile` 里**。
> 插件化的意义就是用户能自己加插件 —— 内嵌的话每加一个都要重新打包，
> 跟设计初衷反着来。详见 `docs/max_bridge_design.md` 或
> 待补的打包章节。

---

## 用户目录 `~/.qz-tool/`

`config.json` 是**只读**的（全项目没有写它的代码路径），凡是程序运行时
需要自己保存的东西，一律落到用户目录，按"一类数据一个文件"拆开：

| 文件 | 内容 | 写入方 |
|---|---|---|
| `layout.json` | 窗口位置/尺寸、置顶状态 always_on_top、分栏比例、画布节点、折叠状态及日志行数上限 log_max_lines | `ui/layout_state.py` |
| `theme.json` | 各 DCC 的标题栏颜色 | `ui/theme.py` |

日志行数上限默认1000，可在设置中修改为100～100000，立即生效并保存，重启后恢复。保存窗口布局、关闭卡片等操作会保留该值。用户脚本副本例外，按用户要求保存在项目内 user_plugins/，详见 [user-scripts.md](user-scripts.md)。

### `theme.json`

```json
{
  "version": 1,
  "dcc_title": {
    "maya": "#0f6e56",
    "max": "#185fa5"
  }
}
```

| 键 | 说明 |
|---|---|
| `version` | 预留。将来改结构时按这个字段做迁移 |
| `dcc_title` | `dcc -> "#rrggbb"`。**只存标题栏底色**，其余颜色（选中描边、标题文字色）由它派生 |

派生规则：选中描边 = 该色 `lighten(0.45)`；标题文字在浅色底上自动转 `#1e1e1e`。
所以**只要改一个值，整张卡片的配色都跟着变**，不需要逐项配置。

色块里的「恢复默认配色」= 删掉 `dcc_title` 里的自定义值（文件本身保留，
为空对象时下次启动走内置默认表 `ui/theme.py::DEFAULT_TITLE`）。

> ⚠️ **为什么颜色不塞进 `layout.json`**
>
> `layout_state._normalize()` 是**白名单**过滤：不在白名单里的字段，
> 保存后再读回来会被**静默抹掉**（不报错、不崩溃，就是设置"莫名其妙没生效"）。
> 塞进去要同时改 `_normalize` 和 `save_all` 两处，且两类数据混在一个文件里
> 以后更容易互相踩。新类数据一律开新文件。

> ⚠️ 读这两个文件都用 `encoding="utf-8-sig"`。
> 用户可能拿记事本打开改颜色，记事本存 UTF-8 会带 BOM，
> 用 `utf-8` 硬读会抛 `JSONDecodeError` ⇒ 表现为"设置全部静默回落默认值"。
