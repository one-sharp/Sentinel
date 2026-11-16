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
