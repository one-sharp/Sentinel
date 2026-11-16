# 随迁移快照保留的模型

模型为既有工程文件，本轮上传没有替换模型。精确文件大小和 SHA256 见 `models-manifest.json`，原始归档和全部发行附件的 SHA256 见 `release/SHA256SUMS.txt`。

- Paraformer Streaming：[官方 sherpa-onnx 原始归档](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2)。原归档为1,047,319,737字节，保留其 README 和官方测试样例；不是用户录音。原 README 标注 `apache-2.0`，并注明权重转换自 [ModelScope Paraformer](https://www.modelscope.cn/models/damo/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online/summary)，相关元数据参考 [Hugging Face](https://huggingface.co/csukuangfj/streaming-paraformer-zh)。
- CAM++：[sherpa-onnx 官方 release 模型](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx)。
- VAD：[sherpa-onnx 官方 release 模型](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx)。

运行模型包仅含五个既有运行资源；原 ASR 归档另存为 Release 附件 `model.tar.bz2`。这些模型的来源和许可声明归各上游项目；本项目的私有上传不改变上游模型条款。
