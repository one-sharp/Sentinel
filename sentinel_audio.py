"""Microphone validation and capture; no UI, models, API calls or recordings.

Call from the session worker, never a Qt/Tk callback. Probe only checks device
capabilities; opening the stream is a separate, explicit listening action.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import math


class MicrophoneUnavailable(RuntimeError):
    """An actionable input-device failure that permits a later retry."""


@dataclass(frozen=True)
class Microphone:
    device: int | None
    name: str
    sample_rate: int


def prepare_microphone(device=None, sample_rate=48000):
    import sounddevice as sd

    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("麦克风采样率必须为正数")
    try:
        info = sd.query_devices(device, "input")
        if info["max_input_channels"] < 1:
            raise ValueError("设备没有输入通道")
    except (sd.PortAudioError, ValueError, OSError) as exc:
        message = ("未检测到可用麦克风。请连接麦克风并在 Windows 中设为默认输入，然后再次开始监听。"
                   if device is None else
                   f"输入设备 {device} 不可用。请重新选择设备，或去掉 --device 使用系统默认麦克风。")
        raise MicrophoneUnavailable(message) from exc

    # Freeze the resolved index so a default-device change during model loading
    # cannot silently capture a different microphone with different settings.
    resolved = int(info["index"])
    rates = dict.fromkeys((sample_rate, info["default_samplerate"], 48000, 44100, 16000))
    last_error = None
    for rate in rates:
        if not math.isfinite(rate) or rate < 1:
            continue
        rate = int(rate)
        try:
            sd.check_input_settings(device=resolved, channels=1, dtype="float32", samplerate=rate)
        except (sd.PortAudioError, ValueError, OSError) as exc:
            last_error = exc
            continue
        return Microphone(resolved, str(info["name"]), rate)
    raise MicrophoneUnavailable("麦克风不支持所需的单声道输入。请更换输入设备后重试。") from last_error


@contextmanager
def microphone_stream(microphone, *, callback):
    import sounddevice as sd

    with ExitStack() as cleanup:
        try:
            capture = sd.InputStream(
                device=microphone.device, samplerate=microphone.sample_rate,
                channels=1, dtype="float32",
                blocksize=int(microphone.sample_rate * .1), callback=callback)
            try:
                stream = cleanup.enter_context(capture)
            except BaseException:
                # PortAudio opens in the constructor, starts in __enter__.
                # Failed __enter__ is not registered by ExitStack for cleanup.
                capture.close()
                raise
        except (sd.PortAudioError, OSError) as exc:
            raise MicrophoneUnavailable(
                "无法使用麦克风。请检查设备连接、是否被其他程序独占，以及 Windows 麦克风权限，然后重试。"
            ) from exc
        # Exceptions from decoding/logging must retain their actual cause.
        yield stream
