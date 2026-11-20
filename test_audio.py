"""Input capability and failure contracts; never opens a real microphone."""
import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

import sounddevice as sd
from sentinel_audio import Microphone, MicrophoneUnavailable, prepare_microphone, microphone_stream


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        query_patch = patch('sounddevice.query_devices', return_value=dict(
            index=7, name='USB mic', max_input_channels=1, default_samplerate=44100.))
        check_patch = patch('sounddevice.check_input_settings')
        self.query, self.check = query_patch.start(), check_patch.start()
        self.addCleanup(query_patch.stop)
        self.addCleanup(check_patch.stop)

    def test_default_is_resolved_without_opening_or_recording(self):
        with patch('sounddevice.InputStream') as capture:
            self.assertEqual(prepare_microphone(), Microphone(7, 'USB mic', 48000))
        self.query.assert_called_once_with(None, 'input')
        self.check.assert_called_once_with(device=7, channels=1, dtype='float32', samplerate=48000)
        capture.assert_not_called()

    def test_missing_or_output_only_input_is_actionable(self):
        for error in (sd.PortAudioError('Error querying device -1'), ValueError('No input'), OSError('driver')):
            with self.subTest(error=error):
                self.query.side_effect = error
                with self.assertRaisesRegex(MicrophoneUnavailable, '未检测到可用麦克风'):
                    prepare_microphone()
        self.query.side_effect = None
        self.query.return_value['max_input_channels'] = 0
        with self.assertRaises(MicrophoneUnavailable):
            prepare_microphone()
        self.check.assert_not_called()

    def test_explicit_missing_device_never_switches_to_another(self):
        self.query.side_effect = sd.PortAudioError('missing')
        with self.assertRaisesRegex(MicrophoneUnavailable, '输入设备 9 不可用'):
            prepare_microphone(9)
        self.query.assert_called_once_with(9, 'input')

    def test_unsupported_requested_rate_uses_device_native_rate(self):
        self.check.side_effect = [sd.PortAudioError('Invalid sample rate'), None]
        self.assertEqual(prepare_microphone().sample_rate, 44100)
        self.assertEqual([call.kwargs['samplerate'] for call in self.check.call_args_list], [48000, 44100])

    def test_all_rates_unavailable_is_an_error(self):
        self.check.side_effect = sd.PortAudioError('unsupported')
        with self.assertRaisesRegex(MicrophoneUnavailable, '更换输入设备'):
            prepare_microphone()
        self.assertEqual(self.check.call_count, 3)

    def test_bad_rate_is_rejected_before_device_access(self):
        for rate in (0, -1, float('nan'), float('inf')):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                prepare_microphone(sample_rate=rate)
        self.query.assert_not_called()

    def test_device_removed_between_probe_and_open_has_retry_message(self):
        for fail_start in (False, True):
            capture = MagicMock()
            capture.__enter__.side_effect = sd.PortAudioError('removed')
            with self.subTest(fail_start=fail_start), patch('sounddevice.InputStream',
                    **({'return_value': capture} if fail_start else {'side_effect': sd.PortAudioError('removed')})):
                with self.assertRaisesRegex(MicrophoneUnavailable, '然后重试'):
                    with microphone_stream(Microphone(7, 'USB', 48000), callback=lambda *args: None):
                        self.fail('Must not start listening')
            if fail_start:
                capture.close.assert_called_once()

    def test_command_check_has_no_model_api_or_capture_side_effects(self):
        from sentinel_live import main
        with patch('sys.argv', ['sentinel_live.py', '--check-audio']), \
                patch('sentinel_live.build_recognizer') as model, \
                patch('sentinel_live.LLMObserver') as observer, \
                patch('sounddevice.InputStream') as capture, contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertIn('USB mic', output.getvalue())
        model.assert_not_called()
        observer.assert_not_called()
        capture.assert_not_called()

    def test_processing_errors_are_not_misreported_as_microphone_errors(self):
        capture = MagicMock()
        with patch('sounddevice.InputStream', return_value=capture):
            with self.assertRaisesRegex(OSError, 'disk full'):
                with microphone_stream(Microphone(7, 'USB', 48000), callback=lambda *args: None):
                    raise OSError('disk full')
        capture.__exit__.assert_called_once()

    def test_check_without_input_exits_nonzero_with_actionable_message(self):
        from sentinel_live import main
        self.query.side_effect = sd.PortAudioError('no input')
        with patch('sys.argv', ['sentinel_live.py', '--check-audio']), \
                contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)
        self.assertIn('未检测到可用麦克风', output.getvalue())


if __name__ == '__main__':
    unittest.main()
