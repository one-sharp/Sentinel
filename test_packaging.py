import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sentinel_config as config
import sentinel_paths as paths

ROOT = Path(__file__).resolve().parent


class PackagingConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-')
        self.path = Path(self.temp.name)
        self.assertEqual(self.path.resolve().parent,ROOT)
        self.env = patch.dict(os.environ, {'SENTINEL_DATA_DIR':str(self.path)}, clear=True)
        self.env.start()
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def test_local_appdata_default_and_explicit_qa_override(self):
        self.assertEqual(paths.user_data_dir(),self.path)
        with patch.dict(os.environ, {'LOCALAPPDATA':str(self.path/'local')},clear=True):
            self.assertEqual(paths.user_data_dir(),self.path/'local/Sentinel')
    def test_frozen_does_not_read_project_env(self):
        with patch.object(config,'FROZEN',True),patch('dotenv.load_dotenv') as loader:
            self.assertEqual(config.api_settings()['LLM_API_KEY'],'')
        loader.assert_not_called()
    def test_developer_env_is_read_but_never_written(self):
        env_path = self.path / '.env'
        before = b'LLM_API_KEY=unit-test-only-key\nLLM_MODEL=test-model\n'
        env_path.write_bytes(before)
        with patch.object(config, 'FROZEN', False), patch.object(config, 'SOURCE_ROOT', self.path):
            config.load_api_environment()
        self.assertEqual(os.environ['LLM_API_KEY'], 'unit-test-only-key')
        self.assertEqual(os.environ['LLM_MODEL'], 'test-model')
        self.assertEqual(env_path.read_bytes(), before)
    def test_api_key_protected_roundtrip_and_saved_config_priority(self):
        secret='unit-test-only-key'
        def crypto(data,decrypt=False):return data[4:] if decrypt else b'ENC:'+data
        with patch.object(config,'_dpapi',side_effect=crypto):
            config.save_api_settings(dict(LLM_API_KEY=secret,LLM_BASE_URL='https://llm.example.invalid/v1',
                                          LLM_MODEL='test',LLM_NETWORK_MODE='direct'))
            text=config.config_path().read_text(encoding='utf-8')
            self.assertNotIn(secret,text)
            self.assertNotIn('"LLM_API_KEY"',text)
            with patch.object(config,'FROZEN',True):
                self.assertEqual(config.api_settings()['LLM_API_KEY'],secret)
    def test_invalid_api_input_does_not_create_config(self):
        for values in ({'LLM_API_KEY':'','LLM_MODEL':'m','LLM_BASE_URL':'https://example.invalid'},
                       {'LLM_API_KEY':'k','LLM_MODEL':'m','LLM_BASE_URL':'http://example.invalid'}):
            with self.assertRaises(ValueError):config.save_api_settings(values)
        self.assertFalse(config.config_path().exists())
    def test_installer_does_not_own_or_delete_user_data(self):
        script=(ROOT/'packaging/Sentinel.iss').read_text()
        sections=[s.strip().lower() for s in script.splitlines() if s.strip().startswith('[')]
        self.assertNotIn('[uninstalldelete]',sections)
        self.assertIn('PrivilegesRequired=lowest',script)
        self.assertIn('dist\\Sentinel\\*',script)
        self.assertNotIn('Source: "{#ProjectRoot}\\.env',script)


if __name__=='__main__':unittest.main()
