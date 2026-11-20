"""Knowledge selection metadata tests: no Tk, document retrieval or network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel_knowledge import KnowledgeCatalog

ROOT = Path(__file__).resolve().parent


class KnowledgeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='.sentinel-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assertEqual(self.root.resolve().parent, ROOT)
        self.path = self.root / 'catalog.json'
        self.first = self.root / '合同资料'
        self.second = self.root / '会议资料'
        self.first.mkdir(); self.second.mkdir()

    def test_multiple_selection_roundtrip_and_clear(self):
        catalog = KnowledgeCatalog(self.path)
        self.assertEqual(catalog.summary(), '未选择知识库')
        first = catalog.add_directory(self.first)
        second = catalog.add_directory(self.second)
        reread = KnowledgeCatalog(self.path)
        self.assertEqual(reread.summary(), '已选 2 个 · 检索待接入')
        self.assertEqual(len(reread.packs), 2)
        reread.set_enabled(first['pack_id'], False)
        self.assertEqual(reread.summary(), '已选 1 个 · 检索待接入')
        reread.set_enabled(second['pack_id'], False)
        self.assertEqual(KnowledgeCatalog(self.path).summary(), '未选择知识库')

    def test_remove_only_unregisters_and_never_reads_document_contents(self):
        document = self.first / '资料.txt'
        document.write_text('local-only sample', encoding='utf-8')
        catalog = KnowledgeCatalog(self.path)
        with patch.object(Path, 'iterdir', side_effect=AssertionError('Must not scan documents')):
            pack = catalog.add_directory(self.first)
            catalog.remove(pack['pack_id'])
        self.assertEqual(catalog.packs, [])
        self.assertTrue(self.first.is_dir())
        self.assertEqual(document.read_text(encoding='utf-8'), 'local-only sample')

    def test_duplicate_resolved_path_rejected(self):
        catalog = KnowledgeCatalog(self.path)
        catalog.add_directory(self.first)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '已经添加'):
            catalog.add_directory(self.first / '..' / self.first.name)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_directory_and_unknown_ids_leave_catalog_unchanged(self):
        catalog = KnowledgeCatalog(self.path)
        pack = catalog.add_directory(self.first)
        before = self.path.read_bytes()
        for operation in (lambda: catalog.add_directory(self.root/'missing'),
                          lambda: catalog.set_enabled('missing', True),
                          lambda: catalog.set_enabled(pack['pack_id'], 'false'),
                          lambda: catalog.remove('missing')):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_config_is_preserved_and_cannot_be_overwritten(self):
        for bad in ('{broken', '{"version":1,"packs":[{}]}', '{"version":2,"packs":[]}'):
            with self.subTest(bad=bad):
                self.path.write_text(bad, encoding='utf-8')
                with self.assertLogs(level='ERROR'):
                    catalog = KnowledgeCatalog(self.path)
                self.assertTrue(catalog.error)
                self.assertEqual(catalog.summary(), '知识库选择读取失败')
                with self.assertRaises(ValueError):
                    catalog.save()
                with self.assertRaises(ValueError):
                    catalog.add_directory(self.first)
                self.assertEqual(self.path.read_text(encoding='utf-8'), bad)

    def test_write_failure_keeps_previous_selection_and_file(self):
        catalog = KnowledgeCatalog(self.path)
        pack = catalog.add_directory(self.first)
        before = self.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk failed')):
            with self.assertRaisesRegex(OSError, 'disk failed'):
                catalog.set_enabled(pack['pack_id'], False)
        self.assertTrue(catalog.packs[0]['enabled'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_returned_packs_are_copies_and_missing_drive_does_not_erase_selection(self):
        catalog = KnowledgeCatalog(self.path)
        catalog.add_directory(self.first)
        catalog.packs[0]['enabled'] = False
        self.assertTrue(catalog.packs[0]['enabled'])
        self.first.rmdir()
        restored = KnowledgeCatalog(self.path)
        self.assertFalse(restored.error)
        self.assertEqual(len(restored.packs), 1)
        self.assertTrue(restored.packs[0]['enabled'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['version'], 1)


if __name__ == '__main__':
    unittest.main()
