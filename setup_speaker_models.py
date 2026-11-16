"""Download only the two official CPU models; never replaces the ASR model."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent / "speaker_models"
MODELS = {
    "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx": (
        "speaker-recongition-models", "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"),
    "silero_vad.onnx": ("asr-models", "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
}


def main():
    ROOT.mkdir(exist_ok=True)
    manifest = {}
    for name, (tag, expected_hash) in MODELS.items():
        url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{tag}/{name}"
        target = ROOT / name
        if not target.exists():
            temporary = target.with_suffix(".download")
            try:
                with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
                    while chunk := response.read(1024 * 1024):
                        out.write(chunk)
                if temporary.stat().st_size < 100_000:
                    raise ValueError(f"模型下载不完整：{name}")
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_hash:
                    raise ValueError(f"模型 SHA256 校验失败：{name}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        manifest[name] = {"url": url, "bytes": target.stat().st_size,
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        if manifest[name]["sha256"] != expected_hash:
            raise ValueError(f"已存在模型 SHA256 不匹配，请移走损坏文件后重试：{target}")
        print(name, manifest[name]["bytes"], manifest[name]["sha256"], flush=True)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
