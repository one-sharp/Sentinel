import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from restore_models import FILES,restore,verify

ROOT=Path(__file__).resolve().parent


class RestoreModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-')
        self.root=Path(self.temp.name);self.dest=self.root/'fresh'
        self.data={name:('fixture-'+name).encode() for name in FILES}
        self.values={name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in self.data.items()}
        self.archive=self.root/'models.zip'
    def tearDown(self):self.temp.cleanup()
    def write_zip(self,data):
        with zipfile.ZipFile(self.archive,'w') as out:
            for name,content in data.items():out.writestr(name,content)
    def test_restore_verify_and_repeat_without_replacing_existing(self):
        self.write_zip(self.data)
        self.assertEqual(restore(self.archive,self.dest,self.values),5)
        before={p:p.stat().st_mtime_ns for p in self.dest.rglob('*') if p.is_file()}
        self.assertEqual(restore(self.archive,self.dest,self.values),5)
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in before})
        self.assertEqual(verify(self.dest,self.values),5)
    def test_forbidden_path_is_rejected_before_any_write(self):
        self.write_zip(self.data|{'../outside.txt':b'bad'})
        with self.assertRaises(ValueError):restore(self.archive,self.dest,self.values)
        self.assertFalse((self.root/'outside.txt').exists())
        self.assertEqual(list(self.dest.rglob('*.onnx')),[])
    def test_corrupt_archive_does_not_install_partial_models(self):
        corrupt=dict(self.data);name=next(iter(corrupt));corrupt[name]=b'x'*len(corrupt[name])
        self.write_zip(corrupt)
        with self.assertRaises(ValueError):restore(self.archive,self.dest,self.values)
        self.assertEqual(list(self.dest.rglob('*.onnx')),[])
    def test_existing_different_model_is_preserved(self):
        self.write_zip(self.data)
        name=next(iter(self.data));target=self.dest/name;target.parent.mkdir(parents=True)
        target.write_bytes(b'existing-work')
        with self.assertRaises(ValueError):restore(self.archive,self.dest,self.values)
        self.assertEqual(target.read_bytes(),b'existing-work')


if __name__=='__main__':unittest.main()
