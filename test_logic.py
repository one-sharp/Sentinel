import unittest
from sentinel_live import cer, norm_zh, percentile, parse_json_response

class LogicTests(unittest.TestCase):
    def test_zh_norm(self):
        self.assertEqual(norm_zh("合同三十天。"), "合同三十天")
    def test_cer_exact(self):
        self.assertEqual(cer("十天。", "十天"), 0.0)
    def test_cer_one_substitution(self):
        self.assertAlmostEqual(cer("三十天", "十三天"), 2 / 3)
    def test_percentile(self):
        self.assertEqual(percentile([3,1,2,4,5], 95), 5)
    def test_json(self):
        self.assertTrue(parse_json_response('```json\n{"alert":true}\n```')["alert"])

if __name__ == "__main__":
    unittest.main()
