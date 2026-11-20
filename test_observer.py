import json
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sentinel_observer import LLMObserver, check_llm_connection, common_prefix_length, create_client, parse_json_response


class MemoryLog:
    def __init__(self):
        self.rows = []
    def write(self, kind, **fields):
        self.rows.append((kind, fields))


class FakeChat:
    def __init__(self, delay=0.04):
        self.delay = delay
        self.calls = []
    def create(self, model, messages, temperature, max_tokens, **kwargs):
        self.calls.append((model, messages[-1]['content']))
        time.sleep(self.delay)
        response = {
            'alert': True, 'headline': '付款期限出现变化',
            'summary': '原三十天，现提出六十天',
            'action': '确认是否已同意',
            'state': '付款期限原30天，现提议60天，尚未同意'
        }
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response, ensure_ascii=False)))])


class ScriptedChat:
    """Deterministic provider double; optionally hold the first request open."""

    def __init__(self, responses, block_first=False):
        self.responses = list(responses)
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not block_first:
            self.release.set()

    def create(self, **kwargs):
        index = len(self.calls)
        self.calls.append(kwargs)
        if index == 0:
            self.entered.set()
            if not self.release.wait(3):
                raise TimeoutError("test did not release provider")
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(response, ensure_ascii=False)))])


class StructuredBriefingTests(unittest.TestCase):
    def response(self, **overrides):
        response = {
            'alert': False, 'state': '期限尚未确认',
            'briefing': {'situation': '双方正在确认期限', 'agreements': [], 'open_questions': []},
        }
        response.update(overrides)
        return response

    def observer(self, chat):
        events, done, log = [], threading.Event(), MemoryLog()

        def receive(**event):
            events.append(event)
            if event['kind'] in ('llm_silent', 'alert', 'llm_error'):
                done.set()

        observer = LLMObserver(log, 'negotiation', client=SimpleNamespace(
            chat=SimpleNamespace(completions=chat)), on_event=receive)
        self.addCleanup(observer.stop)
        self.addCleanup(chat.release.set)
        return observer, events, done, log

    def test_silent_update_still_emits_structured_briefing(self):
        response = self.response()
        chat = ScriptedChat([response])
        observer, events, done, _ = self.observer(chat)
        self.assertTrue(observer.submit('对方提出三十天付款，我方需要核实后回复。', ''))
        self.assertTrue(done.wait(2))
        brief = next(event for event in events if event['kind'] == 'briefing')
        self.assertEqual(brief['briefing'], response['briefing'] | {
            'background': '', 'background_quotes': [], 'next_goal': '', 'next_goal_quotes': []})
        self.assertEqual(brief['source_epoch'], 0)
        self.assertFalse(brief['provisional'])
        self.assertFalse(any(event['kind'] == 'alert' for event in events))

    def test_sidebar_context_uses_confirmed_evidence_in_the_existing_request(self):
        response = self.response(briefing={
            'situation': '仍在讨论付款期限', 'agreements': [], 'open_questions': [],
            'background': '双方已经同意六十天付款', 'background_quotes': ['同意六十天付款'],
            'next_goal': '确认账期是否可延长', 'next_goal_quotes': ['希望延长到六十天'],
        })
        chat = ScriptedChat([response])
        observer, events, done, _ = self.observer(chat)
        self.assertTrue(observer.submit('对方希望延长到六十天，我方还未答复。', '同意六十天付款'))
        self.assertTrue(done.wait(2))
        briefing = next(event['briefing'] for event in events if event['kind'] == 'briefing')
        self.assertEqual(briefing['background'], '')
        self.assertEqual(briefing['next_goal'], '确认账期是否可延长')
        self.assertEqual(briefing['next_goal_quotes'], ['希望延长到六十天'])
        self.assertEqual(briefing['agreements'], [])
        self.assertEqual(len(chat.calls), 1)
        system = chat.calls[0]['messages'][0]['content']
        self.assertIn('background_quotes', system)
        self.assertIn('next_goal_quotes', system)
        self.assertIn('区别于 action', system)
        self.assertIn('不能把', system)

    def test_context_correction_discards_stale_sidebar_values(self):
        old = self.response(briefing={
            'situation': '旧归纳', 'agreements': [], 'open_questions': [],
            'background': '旧角色背景', 'background_quotes': ['先看样品'],
            'next_goal': '旧目标', 'next_goal_quotes': ['先看样品'],
        })
        new = self.response(briefing={
            'situation': '新归纳', 'agreements': [], 'open_questions': [],
            'background': '重新核对样品', 'background_quotes': ['先看样品'],
            'next_goal': '明确检查安排', 'next_goal_quotes': ['先看样品'],
        })
        chat = ScriptedChat([old, new], block_first=True)
        observer, events, done, _ = self.observer(chat)
        self.assertTrue(observer.submit('先看样品，再谈付款条件。', ''))
        self.assertTrue(chat.entered.wait(1))
        observer.invalidate_context()
        self.assertTrue(observer.submit('先看样品，再谈付款条件。', ''))
        chat.release.set()
        self.assertTrue(done.wait(2))
        visible = [event for event in events if event['kind'] == 'briefing']
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]['source_epoch'], 1)
        self.assertEqual(visible[0]['briefing']['background'], '重新核对样品')
        self.assertEqual(visible[0]['briefing']['next_goal'], '明确检查安排')

    def test_claims_require_quotes_from_this_requests_confirmed_text(self):
        confirmed = '甲：我们同意三十天付款。乙：我们也同意三十天付款。'
        response = self.response(briefing={
            'situation': '确认期限',
            'agreements': [
                {'text': '三十天付款', 'quotes': ['我们同意三十天付款', '我们也同意三十天付款']},
                {'text': '临时的六十天提议', 'quotes': ['我们也同意六十天付款']},
                {'text': '编造的定金', 'quotes': ['定金已支付']},
            ],
            'open_questions': [],
        })
        chat = ScriptedChat([response], block_first=True)
        observer, events, done, _ = self.observer(chat)
        self.assertTrue(observer.submit(confirmed, '我们也同意六十天付款'))
        self.assertTrue(chat.entered.wait(1))
        # A later transcript must not change evidence for the held request.
        observer.submit(confirmed + '我们也同意六十天付款', '', force=True)
        chat.responses.append(self.response())
        chat.release.set()
        self.assertTrue(done.wait(2))
        brief = next(event['briefing'] for event in events if event['kind'] == 'briefing')
        self.assertEqual([item['text'] for item in brief['agreements']], ['三十天付款'])

    def test_missing_or_malformed_briefing_preserves_legacy_alerts(self):
        for briefing in (None, {'situation': '缺少证据数组'}):
            with self.subTest(briefing=briefing):
                response = self.response(alert=True, headline='期限变化', action='核对期限', briefing=briefing)
                chat = ScriptedChat([response])
                observer, events, done, _ = self.observer(chat)
                self.assertTrue(observer.submit('原定三十天付款，对方提出了六十天付款。', ''))
                self.assertTrue(done.wait(2))
                self.assertFalse(any(event['kind'] == 'briefing' for event in events))
                alert = next(event for event in events if event['kind'] == 'alert')
                self.assertEqual(alert['source_epoch'], 0)

    def test_context_invalidation_discards_old_response_and_old_failure(self):
        for stale_response in (self.response(alert=True, headline='旧共识'), RuntimeError('旧请求失败')):
            with self.subTest(stale_response=stale_response):
                chat = ScriptedChat([stale_response, self.response(state='新角色下尚未达成共识')], block_first=True)
                observer, events, done, log = self.observer(chat)
                transcript = '对方提出三十天付款，我方尚未明确同意。'
                self.assertTrue(observer.submit(transcript, ''))
                self.assertTrue(chat.entered.wait(1))
                self.assertTrue(observer.submit(transcript + '对方再次询问。', '', force=True))
                observer.event_state = '纠正前的状态'
                self.assertEqual(observer.invalidate_context(), 1)
                self.assertEqual(observer.event_state, '')
                self.assertTrue(observer.submit(transcript, ''))
                chat.release.set()
                self.assertTrue(done.wait(2))
                self.assertEqual(len(chat.calls), 2)
                visible = [event for event in events if event['kind'] in ('briefing', 'alert', 'llm_error')]
                self.assertEqual([event['kind'] for event in visible], ['briefing'])
                self.assertEqual(visible[0]['source_epoch'], 1)
                self.assertEqual(observer.completed, 1)
                self.assertEqual(observer.failures, 0)
                self.assertEqual(observer.event_state, '新角色下尚未达成共识')
                self.assertNotIn('纠正前的状态', chat.calls[1]['messages'][-1]['content'])
                self.assertTrue(any(kind == 'llm_cancelled' and row['reason'] == 'context_invalidated'
                                    for kind, row in log.rows))

    def test_stop_suppresses_briefing_as_well_as_alert(self):
        chat = ScriptedChat([self.response(alert=True, headline='不应展示')], block_first=True)
        observer, events, _, _ = self.observer(chat)
        observer.submit('原定三十天付款，对方提出了六十天付款。', '')
        self.assertTrue(chat.entered.wait(1))
        stopper = threading.Thread(target=observer.stop)
        stopper.start()
        self.assertTrue(observer.stop_flag.wait(1))
        chat.release.set()
        stopper.join(2)
        self.assertFalse(stopper.is_alive())
        self.assertFalse(any(event['kind'] in ('briefing', 'alert') for event in events))


class ObserverTests(unittest.TestCase):
    def test_parse_json_fence(self):
        self.assertEqual(parse_json_response('```json\n{"alert":true}\n```')['alert'], True)
    def test_no_false_warning_when_response_invalid(self):
        self.assertFalse(parse_json_response('sorry')['alert'])
    def test_common_prefix(self):
        self.assertEqual(common_prefix_length('我是abc', '我是xyz'), 2)
    def test_concurrent_transcripts_still_emit_alert(self):
        log, fake, events = MemoryLog(), FakeChat(delay=0.09), []
        ob = LLMObserver(log, 'negotiation', seconds=1, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)),
                         model='mock', on_event=lambda **kwargs: events.append(kwargs))
        try:
            self.assertTrue(ob.submit('', '我们原定三十天付款，对方要求六十天'))
            time.sleep(0.02)
            self.assertTrue(ob.submit('', '我们原定三十天付款，对方要求六十天，但没有同意', force=True))
            deadline = time.time()+2
            while time.time() < deadline and ob.completed < 2:
                time.sleep(0.015)
            self.assertEqual(ob.completed, 2)
            self.assertTrue(any(e.get('kind') == 'alert' for e in events))
            self.assertIn('付款期限', ob.event_state)
        finally:
            ob.stop()


class ClientConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.saved = patch("sentinel_config.load_saved_config", return_value={})
        self.saved.start()
        self.addCleanup(self.saved.stop)
        self.env = patch.dict(os.environ, {
            "LLM_API_KEY": "test-key", "LLM_MODEL": "test-model",
            "LLM_BASE_URL": "https://llm.example.invalid/v1",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.dotenv = patch("dotenv.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    def test_direct_mode_ignores_windows_proxy_and_keeps_request_timeout(self):
        os.environ["LLM_NETWORK_MODE"] = "direct"
        with patch("openai.DefaultHttpxClient") as http, patch("openai.OpenAI") as sdk:
            client, model = create_client()
        http.assert_called_once_with(trust_env=False)
        self.assertIs(client, sdk.return_value)
        self.assertEqual(model, "test-model")
        self.assertEqual(sdk.call_args.kwargs["timeout"], 25.0)
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
        self.assertIs(sdk.call_args.kwargs["http_client"], http.return_value)

    def test_default_mode_preserves_system_proxy_behavior(self):
        with patch("openai.DefaultHttpxClient") as http, patch("openai.OpenAI"):
            create_client()
        http.assert_called_once_with(trust_env=True)

    def test_invalid_mode_does_not_create_network_client(self):
        os.environ["LLM_NETWORK_MODE"] = "typo"
        with patch("openai.DefaultHttpxClient") as http:
            with self.assertRaisesRegex(ValueError, "LLM_NETWORK_MODE"):
                create_client()
        http.assert_not_called()

    def test_constructor_failure_releases_http_client(self):
        with patch("openai.DefaultHttpxClient") as http, \
                patch("openai.OpenAI", side_effect=RuntimeError("bad config")):
            with self.assertRaisesRegex(RuntimeError, "bad config"):
                create_client()
        http.return_value.close.assert_called_once()

    def test_connection_check_closes_client_on_failure(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("connection failed")
        with patch("sentinel_observer.create_client", return_value=(client, "test")):
            with self.assertRaisesRegex(RuntimeError, "connection failed"):
                check_llm_connection()
        client.close.assert_called_once()

    def test_connection_check_rejects_invalid_json_and_closes_client(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])
        with patch("sentinel_observer.create_client", return_value=(client, "test")):
            with self.assertRaisesRegex(ValueError, "JSON"):
                check_llm_connection()
        client.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
