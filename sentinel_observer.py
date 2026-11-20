"""Non-blocking, stateful LLM observer for Sentinel live ASR.

The audio decoder never calls the model. The latest pending transcript replaces older
pending snapshots; completed API responses are not silently discarded during speech.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from sentinel_briefing import validate_briefing

SCENES = {
    "meeting": "工作会议：决策、职责、截止时间、分歧、未确认事项。",
    "negotiation": "商务与合同谈判：金额、期限、条件、责任、让步和前后矛盾。",
    "dispute": "纠纷沟通：事实变化、证据线索、明确承诺和需要核对的问题。",
    "general": "普通事件：关键变化、重要承诺、计划调整和需要关注的事项。",
}
STOP = object()


def parse_json_response(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass
    # A non-JSON answer must never become a fabricated warning.
    return {"alert": False, "headline": "", "summary": "", "action": "", "state": "", "invalid_json": True}


def create_client():
    from sentinel_config import load_api_environment
    from openai import DefaultHttpxClient, OpenAI
    load_api_environment()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    url = os.getenv("LLM_BASE_URL", "").strip()
    if not api_key or not model or "YOUR_" in api_key or "YOUR_" in model:
        raise ValueError("请打开设置 → API 配置填写 Key、模型和地址；开发版也可使用项目 .env")
    if url and "YOUR_PROVIDER" in url:
        raise ValueError("LLM_BASE_URL 仍然是模板地址，请改成服务商实际的 OpenAI 兼容接口")
    network_mode = os.getenv("LLM_NETWORK_MODE", "system").strip().lower()
    if network_mode not in ("direct", "system"):
        raise ValueError("LLM_NETWORK_MODE 只能填写 direct（直连）或 system（使用系统代理）")
    kwargs = {"api_key": api_key, "timeout": 25.0, "max_retries": 0}
    if url:
        kwargs["base_url"] = url
    # Windows proxy discovery includes registry settings, even without *_PROXY
    # environment variables. Limit this choice to Sentinel's own HTTP client.
    http_client = DefaultHttpxClient(trust_env=network_mode == "system")
    try:
        return OpenAI(http_client=http_client, **kwargs), model
    except Exception:
        http_client.close()
        raise


def provider_options(client, model: str) -> dict:
    """Disable DeepSeek's default high-effort thinking for low-latency alerts."""
    base_url = str(getattr(client, "base_url", ""))
    if "api.deepseek.com" in base_url or model.startswith("deepseek-"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


def check_llm_connection():
    """A tiny, deliberate API call independent of microphone/model loading."""
    client, model = create_client()
    t = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "只返回 JSON 对象，不要代码块。"},
                {"role": "user", "content": '{"alert":false,"headline":"","summary":"连接成功","action":"","state":""} 请原样返回这个 JSON。'},
            ],
            max_tokens=130,
            **provider_options(client, model),
        )
        content = response.choices[0].message.content or ""
        if parse_json_response(content).get("invalid_json"):
            raise ValueError("LLM 未返回有效 JSON，请检查模型和接口兼容性")
        mode = os.getenv("LLM_NETWORK_MODE", "system")
        print(f"[LLM连通] 模型={model} | 网络={mode} | 耗时={time.perf_counter()-t:.2f}s")
        print("[LLM返回] " + content[:350])
    finally:
        client.close()


def common_prefix_length(a: str, b: str) -> int:
    i = 0
    for x, y in zip(a, b):
        if x != y:
            break
        i += 1
    return i


class LLMObserver:
    """Single API worker; coalesces pending updates without blocking ASR.

    `submit` is called from the ASR thread; callbacks may be called from the LLM
    worker thread. The UI must marshal callbacks back to its own event loop.
    """

    def __init__(self, log, scene: str, seconds: float = 3.0,
                 on_event: Callable | None = None, goal: str = "",
                 client=None, model: str | None = None):
        if scene not in SCENES:
            raise ValueError(f"未知场景：{scene}")
        self._owns_client = client is None
        if client is None:
            client, model = create_client()
        self.client = client
        self.model = model or "test-model"
        self.scene = scene
        self.goal = (goal or os.getenv("EVENT_GOAL", "")).strip()[:250]
        self.min_seconds = max(1.0, seconds)
        self.log = log
        self.on_event = on_event or (lambda **kwargs: None)
        self.pending = queue.Queue(maxsize=1)
        self.stop_flag = threading.Event()
        self._submission_lock = threading.RLock()
        self._client_closed = False
        self.last_submitted = ""
        self.last_submit_at = float("-inf")
        self.revision = 0
        self.source_epoch = 0
        self.last_completed_revision = 0
        self.event_state = ""
        self.last_alert_signature = ""
        self.last_alert_time = float("-inf")
        self.last_alert_was_provisional = False
        self.requests = 0
        self.completed = 0
        self.failures = 0
        self.alerts = 0
        self.coalesced = 0
        self.last_latency_s = None
        if self._owns_client:
            self.log.write("llm_config", model=self.model,
                           network_mode=os.getenv("LLM_NETWORK_MODE", "system"))
        self.thread = threading.Thread(target=self._worker, daemon=False, name="sentinel-llm")
        self.thread.start()

    def submit(self, finals: str, partial: str, force: bool = False):
        with self._submission_lock:
            if self.stop_flag.is_set():
                return False
            return self._submit(finals, partial, force)

    def invalidate_context(self) -> int:
        """Invalidate analysis before publishing transcript or role corrections.

        Request revisions order submissions; source epochs identify corrections
        that make even a newer in-flight interpretation obsolete. The caller
        attaches the returned epoch to its correction event.
        """
        with self._submission_lock:
            if self.stop_flag.is_set():
                return self.source_epoch
            self.source_epoch += 1
            self.event_state = ""
            self.last_submitted = ""
            self.last_submit_at = float("-inf")
            self.last_alert_signature = ""
            self.last_alert_time = float("-inf")
            self.last_alert_was_provisional = False
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            return self.source_epoch

    def _submit(self, finals: str, partial: str, force: bool):
        parts = []
        if finals.strip():
            parts.append("[已确认] " + finals.strip())
        if partial.strip():
            parts.append("[临时识别，可能改写] " + partial.strip())
        snapshot = "\n".join(parts)[-2300:]
        now = time.perf_counter()
        if len(snapshot) < 12 or snapshot == self.last_submitted:
            return False
        if not force:
            if now - self.last_submit_at < self.min_seconds:
                return False
            # Do not spend a paid API request on a one- or two-character change.
            if (len(snapshot) - common_prefix_length(self.last_submitted, snapshot)) < 10:
                return False
        self.last_submitted = snapshot
        self.last_submit_at = now
        self.revision += 1
        # Evidence travels with the request, independently of provisional text.
        job = (self.revision, snapshot, finals.strip()[-2300:], now, self.source_epoch)
        try:
            self.pending.put_nowait(job)
        except queue.Full:
            try:
                self.pending.get_nowait()
                self.coalesced += 1
            except queue.Empty:
                pass
            self.pending.put_nowait(job)
        self.log.write("llm_enqueue", revision=self.revision,
                       transcript_chars=len(snapshot), force=force)
        self.on_event(kind="llm_pending", revision=self.revision, source_epoch=self.source_epoch)
        return True

    def _worker(self):
        try:
            self._loop()
        except Exception as exc:
            self.failures += 1
            self.log.write("llm_error", error=str(exc), traceback=traceback.format_exc())
            self.on_event(kind="llm_error", text="分析线程异常：" + str(exc))

    def _loop(self):
        system = (
            "你是 Sentinel，一个持续观察实时对话的事件副驾驶。场景：" + SCENES[self.scene] + "。"
            "用户关注的目标：" + (self.goal or "及时发现重要事件变化") + "。"
            "转录可能有错字、缺字、人物和数字错误，临时识别尤其不可靠。"
            "必须保留否定和条件关系，不能把可能、提议、已经同意混淆。"
            "转写带有片段ID、音频时间、说话人ID、角色与归属原因；同一片段的新版本取代旧版本。"
            "我方、对方、第三方角色来自用户绑定，不得根据说话顺序、语气或文本自行猜角色。"
            "逐字转写中的我、我们、我方，是当前说话人自己的阵营；角色为对方时，其自称我方应解释为对方。"
            "分析输出中的我方统一指用户阵营。明确转述或引述别人时，区分引述内容与本人立场。"
            "unknown、未绑定和不确定归属必须保留为未知，不能猜是我方。"
            "绝不能把对方的提议、承诺、同意，当作我方的同意；拒绝、未同意也绝非同意。"
            "提醒中指出谁提出、谁拒绝、谁同意；身份依据不足时明确说无法确定发言者。"
            "没有新事件则 alert=false，不要为了提醒而提醒。"
            "只输出一个 JSON 对象，包含："
            "alert(boolean)、headline(<=18字)、summary(<=60字)、"
            "action(<=28字)、state(<=220字，更新后的事件事实和待核实事项摘要)、"
            'briefing({"situation":"当前局势，<=220字",'
            '"background":"当前谈论事项的背景，<=180字",'
            '"background_quotes":["支持背景的已确认逐字原话"],'
            '"agreements":[{"text":"双方明确达成的共识","quotes":["已确认转写中的逐字原话"]}],'
            '"open_questions":[{"text":"仍需确认的问题","quotes":["已确认转写中的逐字原话"]}],'
            '"next_goal":"建议下一步希望确认或达成的目标，<=180字",'
            '"next_goal_quotes":["支持该目标的已确认逐字原话"]} )。'
            "每次都更新 briefing，即使 alert=false；两种条目各最多4项，没有可靠依据时返回空数组。"
            "agreements 仅记录双方明确确认的共识；单方提议、拒绝、沉默和未同意均不能记为共识。"
            "quotes 只能逐字引用本轮已确认转写，禁止引用临时识别、此前事件状态或自行补写原话。"
            "每条最多4个引用，每个引用<=180字，必须同时覆盖对应双方的确认依据。"
            "situation 可说明当前提议、拒绝或分歧，清楚区分事实与待核实内容。"
            "background 只归纳当前事项的已知前因和条件；没有本轮已确认原话依据时留空。"
            "next_goal 是建议接下来希望确认或达成的结果，区别于 action 的具体问法或操作；"
            "不得写成已经达成、已经说出口或已经获得双方同意，也不得凭空添加预算、期限或人物。"
            "background_quotes 和 next_goal_quotes 各需1至4条本轮已确认逐字原话支撑；"
            "没有可靠依据时，文字留空且对应 quotes 返回空数组。"
            "headline 简明扼要。summary 必须描述本轮发现的依据，而非编造的背景。"
            "action 应该是具体、可执行的核实动作。"
            "涉及医疗、法律、安全等情形时只提供事实核对和提醒，不给出未经验证的确定性专业判断。"
        )
        while not self.stop_flag.is_set():
            try:
                job = self.pending.get(timeout=0.2)
            except queue.Empty:
                if self.stop_flag.is_set():
                    break
                continue
            if job is STOP or self.stop_flag.is_set():
                break
            revision, transcript, confirmed_text, submitted_at, source_epoch = job
            with self._submission_lock:
                if self._discard_obsolete(revision, source_epoch):
                    continue
                self.requests += 1
                queue_delay = time.perf_counter() - submitted_at
                self.on_event(kind="llm_running", revision=revision, source_epoch=source_epoch)
                prompt = (
                    "此前已建立的事件状态（根据新证据可更正）：\n"
                    + (self.event_state or "暂无")
                    + "\n\n目前最新的转写片段（可能包含前文供你理解上下文）：\n"
                    + transcript
                    + "\n\n请更新当前事项的背景、当前局势、已达成共识、待确认事项和建议的下一步目标，"
                    "并检查是否有值得提醒用户注意的新变化。"
                )
            started_at = time.perf_counter()
            self.log.write("llm_request", revision=revision, queue_delay_s=round(queue_delay, 3))
            try:
                result = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=1200,
                    **provider_options(self.client, self.model),
                )
                raw = result.choices[0].message.content or ""
                with self._submission_lock:
                    if not self._discard_obsolete(revision, source_epoch):
                        self._publish_result(raw, revision, confirmed_text, submitted_at,
                                             source_epoch, time.perf_counter() - started_at)
            except Exception as exc:
                with self._submission_lock:
                    if self._discard_obsolete(revision, source_epoch):
                        continue
                    self.failures += 1
                    self.log.write("llm_error", revision=revision, source_epoch=source_epoch,
                                   error=str(exc), traceback=traceback.format_exc())
                    print(f"\n[LLM错误] {exc}", flush=True)
                    self.on_event(kind="llm_error", text=str(exc)[:180],
                                  revision=revision, source_epoch=source_epoch)

    def _discard_obsolete(self, revision, source_epoch):
        """Called under the submission lock, also used while publishing results."""
        reason = ("session_stopped" if self.stop_flag.is_set() else
                  "context_invalidated" if source_epoch != self.source_epoch else "")
        if reason:
            self.log.write("llm_cancelled", revision=revision,
                           source_epoch=source_epoch, reason=reason)
        return bool(reason)

    def _publish_result(self, raw, revision, confirmed_text, submitted_at, source_epoch, latency):
        """Commit a current response while corrections and submissions are locked."""
        parsed = parse_json_response(raw)
        self.completed += 1
        self.last_completed_revision = revision
        self.last_latency_s = latency
        if isinstance(parsed.get("state"), str) and parsed["state"].strip():
            self.event_state = parsed["state"].strip()[:500]
        provisional = revision < self.revision
        metadata = dict(revision=revision, source_epoch=source_epoch, latency=latency)
        self.log.write("llm_result", revision=revision, source_epoch=source_epoch,
                       latency_s=round(latency, 3),
                       from_capture_to_result_s=round(time.perf_counter() - submitted_at, 3),
                       provisional=provisional, result=parsed)
        if parsed.get("invalid_json"):
            print(f"\n[LLM格式异常] 模型未返回可解析JSON；原文={raw[:200]!r}", flush=True)
            self.on_event(kind="llm_error", text="模型未返回有效 JSON", **metadata)
            return
        brief = validate_briefing(parsed.get("briefing"), confirmed_text)
        if brief is not None:
            self.on_event(kind="briefing", briefing=brief, provisional=provisional, **metadata)
        is_alert = parsed.get("alert") is True or str(parsed.get("alert")).lower() == "true"
        headline = str(parsed.get("headline") or "")[:70]
        evidence = str(parsed.get("summary") or "")[:250]
        action = str(parsed.get("action") or "")[:120]
        if is_alert and headline:
            signature = headline + "|" + action
            if (signature == self.last_alert_signature
                    and time.perf_counter() - self.last_alert_time < 45
                    and (provisional or not self.last_alert_was_provisional)):
                print(f"\n[LLM观察] 同一提醒已展示，避免重复 | {latency:.2f}s", flush=True)
                self.on_event(kind="llm_silent", **metadata)
                return
            self.last_alert_signature = signature
            self.last_alert_time = time.perf_counter()
            self.last_alert_was_provisional = provisional
            self.alerts += 1
            tag = "待核对" if provisional else "关注"
            print("\n" + "=" * 66)
            print(f"[SENTINEL · {tag}] {headline}")
            print("依据：" + evidence)
            print("建议：" + action)
            print(f"LLM响应：{latency:.2f}s | 请求版本：{revision}" + (" | 已有更新转写" if provisional else ""))
            print("=" * 66, flush=True)
            self.on_event(kind="alert", headline=headline, summary=evidence,
                          action=action, provisional=provisional, **metadata)
        else:
            print(f"\n[LLM观察] 暂无值得提醒的新变化 | {latency:.2f}s | rev={revision}", flush=True)
            self.on_event(kind="llm_silent", **metadata)

    def stop(self):
        with self._submission_lock:
            self.stop_flag.set()
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait(STOP)
        # A synchronous HTTP request cannot safely be killed. Its configured
        # timeout is 25s with no retries; discard its result and wait off the UI.
        self.thread.join()
        if self._owns_client and not self._client_closed:
            self.client.close()
            self._client_closed = True
