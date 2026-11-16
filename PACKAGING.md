# Sentinel v0.3.1 Windows 安装与构建

2026-11-13 安装版已加入 [对话逻辑图](LOGIC_GRAPH.md)。图文档位于 `%LOCALAPPDATA%\Sentinel\logic_graphs`，不随包分发。后文的安装、用户配置和模型资源目录保持不变。

本轮沿用 Tkinter 和现有 ASR / CAM++ / VAD / DeepSeek 后端，使用 PyInstaller `onedir + windowed`，由 Inno Setup 生成安装程序。实际结果见 [构建与测试报告](release/BUILD_REPORT.md)。

## 安装、启动和首次配置

1. 双击 `release\Sentinel-Setup.exe`，选择安装目录；按需要保留桌面图标选项。
2. 默认安装到 `%LOCALAPPDATA%\Programs\Sentinel`，也可选择当前用户可写的其他目录。安装程序创建开始菜单入口，支持卸载和安装结束后启动；不添加开机启动。
3. 第一次打开会显示 API 配置窗口。填写自己的 Key、服务商实际提供的模型 ID 和 HTTPS API 地址，点击「保存并测试连接」。测试只发送固定短文本，不启用麦克风。
4. 配置完成后关闭配置窗口，在主窗口点击「开始监听」。启动默认待机，不录音、不请求 LLM。停止后可再次开始新会话。
5. 窗口的关闭按钮收起到托盘；从设置或托盘选择「退出 Sentinel」才会停止监听并退出。请先退出再安装升级。

API 配置也可从「设置 → API 配置」重新打开。Key 使用 Windows DPAPI 按当前用户加密，保存在用户数据目录，不写入程序安装目录。换 Windows 账户或迁移电脑后需要重新填写 Key。

按最新需求，应用不再注册全局快捷键，设置页不再提供桌面配置。悬浮卡片的「﹣」折叠为迷你状态条，「主界面 ↗」展开详细页面；主界面的「悬浮窗 ↗」返回卡片。状态条保留麦克风开关、笔记、主界面与展开按钮。拖动标题区移动窗口，右下角拖动调整大小。

主界面可编辑本次目标和场景，声纹与角色绑定入口保留。点击悬浮卡片的目标文字也能进入主界面。关闭按钮仍收起到托盘，可从任务栏或托盘恢复；私人笔记仍不自动发送给 LLM。更多截图和测试见 [悬浮窗升级](UI_UPDATE.md)。

也可以直接双击 `dist\Sentinel\Sentinel.exe`。分发免安装版时必须复制**整个 `dist\Sentinel` 目录**，包括 `_internal` 和 `resources`；单独复制 EXE 无法运行。目标机无需安装 Python。

## 资源与用户数据

```text
安装目录/
  Sentinel.exe
  _internal/                  Python、Tk、DLL、图标及运行依赖
  resources/
    sherpa-onnx-streaming-paraformer-bilingual-zh-en/
      encoder.int8.onnx
      decoder.int8.onnx
      tokens.txt
    speaker_models/
      3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
      silero_vad.onnx

%LOCALAPPDATA%/Sentinel/
  api_config.json             当前用户加密 API 配置
  desktop_settings.json       桌面偏好（旧快捷键字段保留但不启用）
  private_notes/              私人笔记和草稿
  voice_profiles/self.json    本人声纹特征，不是完整录音
  logs/                       应用日志、JSONL 会话、最近转写
```

开发版从项目原有模型目录读取资源；EXE 从自身旁边的 `resources` 读取。两种桌面启动方式的新用户数据都默认写到 `%LOCALAPPDATA%\Sentinel`。安装、升级、卸载不删除该用户目录。

原项目的 `.env`、旧 `logs`、笔记和声纹文件均保留原处，没有自动移动或打包。EXE 完全不读取开发目录 `.env`。开发版继续读取它；如通过 GUI 保存了用户 API 配置，则显式保存的配置优先。需要沿用旧声纹或笔记时，可在程序退出后自行备份并复制相应文件至上述目录，或重新录入声纹。

`SENTINEL_DATA_DIR` 仅作为开发/QA 的显式隔离目录覆盖；正常安装使用时不要设置它。命令行开发测试可用 `--log-dir` 指定测试输出目录。

## 从现有 Python 环境构建

当前工程已配置 `.venv` 和本地 Inno Setup 编译器，双击 `build_windows.cmd` 即可重新构建。构建只替换项目内 `build\pyinstaller` 和 `dist\Sentinel` 生成物，不改开发 `.env`、源模型和用户数据。

首次准备另一台构建机时，在项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

需要 Windows x64 Python，以及 Inno Setup 6 的 `ISCC.exe`，脚本会依次寻找 `.build_tools\inno\ISCC.exe` 和 `%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe`。编译器来自 [Inno Setup 官方下载](https://jrsoftware.org/isdl.php)。模型仍须先准备在原项目对应目录。

单独构建或编译安装程序：

```powershell
.\.venv\Scripts\python.exe -X utf8 build_exe.py
& .\.build_tools\inno\ISCC.exe packaging\Sentinel.iss
```

构建脚本按白名单复制五个运行模型文件，校验 SHA256，检查 GUI 子系统、x64 架构、Python 运行库及禁止打包的私人文件，同时扫描当前开发 Key 是否出现在分发文件中。结果写入 `build\distribution-audit.json`。资源路径遵循 [PyInstaller 运行时约定](https://pyinstaller.org/en/stable/runtime-information.html)；安装程序使用 [Inno Setup 当前用户安装模式](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)。

## 自动化与手工复验

源代码测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
$env:SENTINEL_NATIVE_TESTS='1'
.\.venv\Scripts\python.exe -m unittest test_native_windows -v
```

EXE 提供显式诊断模式，普通启动不会运行。它要求隔离数据目录，避免向实际用户数据写测试笔记或替换声纹：

```powershell
$env:SENTINEL_DATA_DIR = Join-Path $PWD 'packaging-qa\manual'
Start-Process .\dist\Sentinel\Sentinel.exe -ArgumentList '--self-test' -Wait
Get-Content (Join-Path $env:SENTINEL_DATA_DIR 'self-test.json')
```

添加 `--test-microphone` 才会进行两轮真实麦克风采集；添加 `--test-llm` 才会请求服务商。LLM 测试前可在同一隔离数据目录正常打开 EXE，通过首次配置窗口填入自己的 Key。诊断的麦克风测试不将采集转写发送给 LLM，也不保存完整原始录音。

安装/覆盖升级/卸载测试（使用项目内临时安装目录，会真实安装和卸载该应用）：

```powershell
& .\packaging\test_install.ps1
# 已授权麦克风并在 packaging-qa\full 配好测试 Key 时：
& .\packaging\test_install.ps1 -TestMicrophone -TestLlm
```

脚本检查现有用户文件和自己的标记文件在安装、升级、卸载后的哈希。测试标记最终清理，用户文件保留。脚本使用固定 AppId，因此**不要在需要保留的正式 Sentinel 安装同时运行安装自动化测试**；建议在测试账户或干净 VM 中复验。

手工验收建议：安装后待机 → 检查首次配置 → 开始监听并朗读 → 确认转写/角色/提醒 → 停止并检查 JSONL summary → 再次开始 → 私人笔记保存并重开 → 隐藏到托盘并点击托盘恢复 → 退出后检查麦克风停止 → 覆盖升级/卸载并检查用户数据。另需在未装 Python 的干净 Windows 10/11 x64 机器完成兼容性复验。

截图：[悬浮卡片](docs/screenshots/ui-floating-dark.png)、[迷你状态条](docs/screenshots/ui-status-bar.png)、[主界面](docs/screenshots/ui-main-dark.png)。
