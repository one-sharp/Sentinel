# Sentinel Windows 构建与逻辑图验证报告

日期：2026-11-13。当前 v0.3.1 工程增量加入实时对话逻辑图，仍使用 Tkinter、Paraformer Streaming、CAM++、VAD、DeepSeek；全局快捷键仍停用。

## 实际产物

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| [Sentinel.exe](../dist/Sentinel/Sentinel.exe) | 15,614,485 字节 / 14.89 MiB | 377be33c43c7f81578ee2bbe94feb8e870676c85c9d1947725ed67ee50993c3a |
| [Sentinel-Setup.exe](Sentinel-Setup.exe) | 300,684,392 字节 / 286.75 MiB | 393cbbc5076ef100b8d5e2da41c5a0b70e2a6e36a42f02ad0aa62970d95381e4 |

`dist\Sentinel` 整目录共 1,238 个文件，447,342,091 字节 / 426.62 MiB。免安装分发必须复制整个目录。Windows x64 GUI 子系统，PyInstaller onedir/windowed，继续使用现有狐獴图标和外置模型资源目录。

## 本轮结果

- 源代码 unittest：85项中83项通过、2项原生 opt-in 测试跳过；末次逻辑图专项12项通过。
- 图专项覆盖全场超过128条保留、32条分批、角色与原文更正、意图强制标为推测、未知身份、无效引文与箭头拒绝、合并保留早期节点、保存重读、单线程退出丢弃迟到结果、整场数据未变化时避免重复复制、旧证据清除、400节点分页和退出前最后一句保存。
- 原桌面开始/停止/再次开始、麦克风资源与笔记生命周期回归继续通过。
- 最终便携 EXE：8/8实测通过；安装后的 EXE：8/8实测通过。包含真实 ASR/CAM++/VAD、两轮真实麦克风、既有 DeepSeek 连通、逻辑图窗口与保存、真实 DeepSeek 逻辑图分析、托盘退出和零全局热键注册。
- 图 API 测试只发送虚构谈判文本；麦克风测试不发送采集转写给 LLM，不保存完整原始录音。
- Windows 安装、覆盖升级、卸载均实际执行；校验的 5 个既有用户文件与测试标记文件哈希保持不变。测试安装已卸载，用户目录保留。
- 分发审计：x64、Windows GUI；未发现开发 `.env`、本机 Key、历史日志、私人笔记、声纹档案或逻辑图数据。未改原 ASR、VAD、声纹模型和实时提醒策略。

8项 EXE 诊断：

- `standalone_imports`：通过
- `real_asr_cam_vad_models`：通过
- `user_data_roundtrip`：通过
- `logic_graph_view_and_persistence`：通过
- `real_microphone_start_stop_release`：通过
- `real_llm_connection`：通过
- `real_logic_graph_llm`：通过
- `actual_tk_ui_tray_hotkeys_notes_exit`：通过

## 真实图生成样例

八段虚构 A/B 谈判经真实模型生成11节点、10条关系，全部8段已审阅。结果区分付款条件、交付时间和样品确认；保留“预付款仍未同意”的卡点，仅把确认样品作为推进。双方意图均标为推测。该次图请求约9.14秒，工作线程 CPU 约1.53秒，仅为短样例观测。

实际 Windows 窗口已检查全图缩放及点选证据。截图明确标记为虚构样例：

- [分支与原话依据](../docs/screenshots/logic-graph-evidence.png)
- [全图总览](../docs/screenshots/logic-graph-overview.png)

## 数据和限制

图使用独立后台文本分析线程，点击入口后启用。全部确认文本和当前图保存在 `%LOCALAPPDATA%\Sentinel\logic_graphs`，事件日志记录耗时、工作线程CPU和错误。私人笔记、临时转写不参与。安装、升级、卸载不删除用户数据；原 Python 启动和开发 `.env` 保留。

模型每次读取新片段和近期图上下文，跨长距离的关系可能遗漏或重复。实线只代表有对应引文，并不证明 AI 归纳正确；意图永远是推测。用户需要点击原话核对关键判断。

本机为 Windows 11 x64。未在独立无 Python 的干净 VM、Windows 10、长时间真人对话或混合DPI多显示器上重测；未统计语义准确率或真人长时并行性能。短音频测试不代表长期性能。安装程序未添加代码签名。

## 文件和证据

新增：`sentinel_logic.py`、`sentinel_logic_ui.py`、`test_logic_graph.py`、`LOGIC_GRAPH.md` 和两张截图。
修改：`sentinel_panel.py`、`sentinel_ui.py`、`sentinel_package_test.py`、`test_desktop_features.py`、`build_exe.py`、`.gitignore`、`README.md`、`PACKAGING.md`。

原始本地证据（不随包分发）：`build/unit-tests.txt`、`build/logic-tests.txt`、`build/logic-regression.txt`、`build/final-build.log`、`build/installer-build.log`、`build/distribution-audit.json`、`packaging-qa/portable-self-test.json`、`packaging-qa/installed-self-test.json`、`packaging-qa/install-results.json`。

[逻辑图使用与设计边界](../LOGIC_GRAPH.md) · [安装与构建](../PACKAGING.md)
