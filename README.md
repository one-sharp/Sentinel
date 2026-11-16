# Sentinel 哨兵 v0.3.1：桌面悬浮窗与私人笔记

**换电脑继续开发：先阅读 [DEVELOPMENT.md](DEVELOPMENT.md)。** 源码和构建资料位于此仓库；当前模型、完整运行包及安装包在 [Releases](https://github.com/one-sharp/Sentinel/releases/tag/v0.3.1-logic)。真实 Key 和私人数据仅留在本机。

2026-11-13：新增可实时更新的「对话逻辑图」，按议题和双方角色显示提议、意图推测、卡点与推进，点击节点可核对原话和音频时间。主界面及悬浮卡片都有入口，使用方法、截图及验证边界见 [LOGIC_GRAPH.md](LOGIC_GRAPH.md)。全局快捷键仍停用。

最新交互：深色圆角悬浮卡片、单行迷你状态条、大字主界面。按最新需求已取消全局快捷键，全部通过窗口按钮或托盘操作。截图和用法见 [UI_UPDATE.md](UI_UPDATE.md)。

2026-11-06 Windows 安装版：已实际生成 `release\Sentinel-Setup.exe` 和 `dist\Sentinel\Sentinel.exe`（onedir/windowed，无需另装 Python）。安装步骤、首次 Key 配置、用户数据位置和复现构建见 [PACKAGING.md](PACKAGING.md)，实际构建及验证结果见 [BUILD_REPORT.md](release/BUILD_REPORT.md)。新桌面会话、笔记、声纹及桌面配置默认保存到 `%LOCALAPPDATA%\Sentinel`；原开发 `.env` 和历史文件保留。

2026-11-06 桌面交互升级：默认紧凑悬浮窗、展开/折叠、私人快速笔记、系统托盘、桌面快捷方式。用法、文件清单、截图和验证边界见 [DESKTOP_V031.md](DESKTOP_V031.md)。关闭按钮现在收起到托盘，结束程序请从设置或托盘选择「退出」。

2026-11-06 增量功能：本地 CAM++ 双人说话人区分、本人声纹录入、匿名 ID 与角色绑定。使用方法、限制、性能实测和三分钟真人验收见 [SPEAKERS_GUIDE.md](SPEAKERS_GUIDE.md)。现有 Paraformer Streaming ASR 保留。

保留已经跑通的 sherpa-onnx Paraformer Streaming INT8（CPU 推理）。本版真正新增了：持续对增量转写调用 LLM、跨请求事件状态、可视化大字报、API 连通性检查、日志中的 LLM 响应时延；不要求重新下载模型。**RAG 暂未接入**，先测「听到关键变化 → LLM 识别 → 提醒显示」闭环。

## 1. 覆盖原项目（不重装模型）

把本 ZIP **内部的文件**解压覆盖到已经成功运行的文件夹：

`D:\Sentinel-ASR-Live-Test\Sentinel-ASR-Live-Test`

保留原有 `.venv/`、`sherpa-onnx-streaming-paraformer-bilingual-zh-en/`、`model.tar.bz2` 和旧 `logs/`。新的 ZIP **不包含**这些大文件。

如不确定当前目录是否正确，在该目录的 PowerShell 输入：

```powershell
Test-Path .\.venv\Scripts\python.exe
Test-Path .\sherpa-onnx-streaming-paraformer-bilingual-zh-en\encoder.int8.onnx
```

两条都应返回 `True`。已安装 Python 3.12，无需另外安装 3.11。

若依赖尚未安装，执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 配置 API Key

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

填写：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。支持 OpenAI 兼容的 `/chat/completions` API；**模型名称必须是服务商提供的真实 ID**。比如已开通 DeepSeek API 的用户可设置 `LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`；其他供应商填各自地址。程序对 DeepSeek 自动使用非思考模式，避免默认长时间推理拖慢实时反馈。

`LLM_NETWORK_MODE` 控制 Sentinel 的 LLM 连接方式：`direct` 为直连，`system` 沿用 Windows 系统代理；省略时保持原有的 `system` 行为。本机已验证系统代理引发 TLS 连接中断，当前 `.env` 已设为 `LLM_NETWORK_MODE=direct`，并通过真实 API 请求验证。此设置仅影响 Sentinel 的 LLM 客户端。修改后请关闭旧窗口并重新双击启动。

先检查 Key 和模型是否真正可用（会产生一次很小的 API 请求）：

```powershell
.\.venv\Scripts\python.exe sentinel_live.py --check-llm
```

会输出 `[LLM连通]` 和一小段模型响应；不需要打开麦克风。

## 3. 双击打开桌面窗口（推荐）

在资源管理器打开 `D:\Sentinel-ASR-Live-Test\Sentinel-ASR-Live-Test`，双击 **`start_sentinel.cmd`**。
它会使用同一目录下已有的 `.venv\Scripts\pythonw.exe` 启动 `sentinel_app.pyw`，自动定位项目、模型、`.env` 和 `logs/`，无需激活虚拟环境或输入 PowerShell 命令。启动时命令窗口可能短暂闪过，随后只保留桌面窗口。不要把启动文件单独移出项目目录；可以创建它的桌面快捷方式。

- 打开后显示「待机」，不加载 ASR 模型、不开麦克风、不请求 LLM。
- 从「合同谈判、会议、纠纷、通用」中选择场景，点击「开始监听」，后台加载模型并启用已有 ASR + LLM。默认持续运行，没有 300 秒限制。
- 加载中和监听中均可点击「停止监听」。停止后释放麦克风，丢弃尚未执行的分析，等待正在执行的分析结束，保存日志；收尾期间界面仍然响应。
- 如正在加载模型，需等待本次加载返回后停止；如正在请求 LLM，需等待该请求返回或触发其 25 秒超时配置（不重试）。停止期间不接受重复开始，旧分析不会显示到下一会话。
- 显示「已停止」后可以切换场景并再次开始，每次创建新的会话、停止事件和日志文件。若修改了 `.env`，关闭窗口后重新双击启动，使新配置生效。
- v0.3.1 关闭窗口默认收起到托盘，监听继续；主窗口与托盘会明确显示麦克风状态。设置或托盘中的「退出」会停止监听、保存日志及笔记草稿后结束程序。异常会显示在窗口下方，停止后的错误仍保留。

桌面启动默认启用 LLM 和本地说话人区分，沿用现有 `.env`。当前机器已安装声纹模型和 scipy；迁移到其他机器时先双击 `setup_speakers.cmd`，或在说话人设置中关闭声纹。窗口使用 Python 标准库 Tkinter，原有 ASR 模型无需重装。

### 桌面验收步骤

1. 双击启动文件，确认「待机」，「开始监听」可用，「停止监听」禁用；此时不会新增 `session-*.jsonl`（只创建应用运行日志）。
2. 选择「合同谈判」并点击开始，连续点击开始也只会启动一次。首次加载模型可能需要数秒，期间窗口可以拖动，停止按钮可用。
3. 显示「监听中」后朗读第 5 节的例句，检查实时转写、确认文本和 LLM 提醒。持续运行超过 5 分钟应仍在监听。
4. 点击停止，等待「已停止」，确认 `logs/session-*.jsonl` 最后一行为 `summary`；`logs/last-transcript.txt` 更新为本次确认文本。
5. 切换到「会议」，再次开始并停止；应产生第二份独立会话日志，界面不出现上次会话的迟到提醒。
6. 监听中或模型加载中直接点窗口关闭，等待收尾完成，确认窗口退出、会话日志有总结。
7. 若遇到麦克风不可用、模型文件缺失或 API 配置错误，检查窗口错误区和日志；修正原因后再次开始。

### 保留命令行测试

原有纯 ASR、设备列表、连通性检查、场景、目标、时长和 CER 参数保留。`--panel` 现在同样先待机，由窗口按钮开始；显式传入的 `--duration` 仍作为该命令行窗口每次会话的时长。

```powershell
.\.venv\Scripts\python.exe sentinel_live.py --llm --panel --scene negotiation --llm-interval 3
```

- `--scene`：`meeting` 会议、`negotiation` 谈判、`dispute` 纠纷、`general` 通用。
- `--goal "关注付款期限、违约责任及已经确认的承诺"`：指定当场关注目标。
- `--duration 300`：运行 5 分钟；省略或改成 `0` 会持续运行，点击「停止监听」结束。
- `--llm-interval 3`：每次 LLM 分析之间**至少** 3 秒；模型请求仍按顺序执行，一个待分析快照可被新的替换，不会无限积压。
- `--panel`：显示桌面大字报；去掉则在 PowerShell 中显示相同提醒。

自动回归测试（假麦克风/假 API，不产生付费请求）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

**保持纯 ASR 测试**：

```powershell
.\.venv\Scripts\python.exe sentinel_live.py --duration 180
```

## 4. 它到底怎么边听边分析？

1. 麦克风每约 100ms 输入本地 sherpa-onnx，持续在 CPU 上处理，不被网络请求阻塞。
2. 新转写积累到一定长度，或句尾被确认，就生成新的事件快照。**即使几十秒没有 `[确认]`，也能在说话期间持续分析 `[临时]`。**
3. 单个 LLM 工作线程维护最新待分析的文本；如果 LLM 比语速慢，新的待分析快照覆盖旧待分析快照，避免队列堆积。
4. LLM 返回 `alert`、`headline`、`summary`、`action` 和最新 `state`。下一轮附带事件状态，让模型知道前面发生过什么。
5. 大字报只展示值得关注的事件；未发生关键变化时保持安静。相同内容会去重。
6. 旧请求在新转写出现后才完成，也不会被**无声丢弃**；界面会将其标为「待核对」，并等待下一轮更新。

> ASR 当前仍可能把“没有同意取消违约金”漏成“没有同意违约金”，所以事件提醒是辅助核对原话，不应当据此直接签合同或做医疗判断。

## 5. 第一次测什么

建议先不接 RAG，正常语速读下面这段，中间略微停顿：

> 我们原本约定三十天付款。对方现在提出把付款期限延长到六十天，同时要求提高预付款比例。但是我们还没有同意。请先确认对方提出的条件，和已经达成的最终协议有什么区别。

预期：终端/大字报能提示「付款期限或预付款条件有变化，建议核对」，而不应自动当成「已经正式同意」。具体措辞取决于服务商模型输出。

之后再试会议场景：

```powershell
.\.venv\Scripts\python.exe sentinel_live.py --llm --panel --scene meeting --goal "注意负责人是否明确和上线日期是否已决定" --duration 300
```

## 6. 查看日志与延迟

- `logs/session-日期时间-微秒.jsonl`：全部增量转写、确认文本、LLM 入队、请求、结果、延迟、错误、最后的统计。每次开始独立创建，不覆盖历史会话。
- `llm_config`：记录本次 LLM 模型和连接方式（direct/system），便于排查系统代理导致的连接错误。
- `logs/app-日期时间-微秒.log`：桌面启动、原有控制台输出及完整异常堆栈；缺少依赖等启动失败也会弹窗提示。
- `logs/last-transcript.txt`：最后一次测试确认下来的转写文本。
- `llm_completed`、`llm_alerts`、`llm_pending_coalesced`、`llm_last_latency_s`：可以了解 LLM 实际处理了多少次、是否发生积压、响应速度。
- `from_capture_to_result_s`：从**程序提交当前快照**到模型完成返回的时长，不是精确的「从人说出关键词开始」端到端时长。之后可加人工打点和回放进一步测。

## 注意事项

LLM 模式会把截取的转写文本发给你配置的云端服务，纯 ASR 模式不发。确保参与者同意录音/转录；不要把 API Key、`.env` 和敏感对话日志上传到公开仓库或分享给他人。

**当前仅支持一个麦克风输入**。说话人区分支持保守的片段级对齐；默认不保存原始录音，可在获得同意后限量保存测试片段。尚不支持系统音频、RAG、完整录音保存或专业决策自动执行。

官方 DeepSeek API 配置参考：https://api-docs.deepseek.com/zh-cn/ （2026-10 新版本模型名为 `deepseek-flash`）。
