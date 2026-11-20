# 实际语音验证记录

2026-11-20，在当前 Windows 开发环境执行。此次未重建安装包。

## 实际程序操作

启动正式 `sentinel_app.pyw`，使用隔离的 `.downloads/audio-manual-qa` 数据目录，并通过原生鼠标点击主页「开始监听」。程序返回「未检测到可用麦克风」，麦克风保持关闭、会话正常收尾、开始按钮恢复可用。不是固定数据界面预览。当前可见输入设备列表为空，无法完成硬件拾音验证。

## 真实模型转写

从 sherpa-onnx 官方公开模型库 `csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en` 获取 `encoder.int8.onnx`、`decoder.int8.onnx`、`tokens.txt`，三项文件大小与 SHA256 均符合项目 `models-manifest.json`，已放入既有 ASR 资源目录。另取同库官方样例 `test_wavs/0.wav`、`test_wavs/1.wav`，不含用户录音。

验证使用正式 `sentinel_live.run`、真实 Paraformer CPU 模型、现有音频队列、流式解码、端点检测、转写事件与日志。只有输入设备预检和 InputStream 被替换为官方 WAV 输入；按块投递音频、追加端点静音，等待识别和收尾。LLM 与说话人功能在此测试中关闭。

| 官方样例 | 原始音频长度 | 实际确认文本 |
| --- | --- | --- |
| 0.wav | 10.05 秒 | 昨天是 monday today is 零八二 the day after tomorrow 是星期三 |
| 1.wav | 5.10 秒 | 这这是第一种第二种叫呃与 always o s 什么意思啊 |

两次均生成确认转写、会话 summary 与 last-transcript.txt，音频队列丢块为 0，LLM 请求为 0。包含端点静音的音频处理量分别为 14.05 秒和 9.10 秒。样例按加速节奏投递，以上不是实时延迟或真人识别准确率基准；未计算 CER/WER。

本地原始报告：`.downloads/audio-real-asr-qa/report.json`；完整输出：`.downloads/audio-real-asr-output.txt`；下载校验记录：`.downloads/audio-model-download.txt`。这些 QA 产物由 Git 忽略。

## 尚需硬件验收

尚未验证麦克风驱动、权限、真实热拔插、环境噪声和实际谈判场景准确率。本次未恢复 CAM++ / VAD 权重，也未验证声纹或云端提醒。正式桌面入口默认启用说话人功能；在有麦克风的电脑使用前，仍应按 DEVELOPMENT.md 恢复完整五项模型并配置自己的 API Key。
