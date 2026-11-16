"""Restore the exact current public models from a separately distributed ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent
ASR='sherpa-onnx-streaming-paraformer-bilingual-zh-en'
FILES={ASR+'/'+n for n in ('encoder.int8.onnx','decoder.int8.onnx','tokens.txt')} | {
    'speaker_models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx','speaker_models/silero_vad.onnx'}


def digest(path):
    sha=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def manifest(path=ROOT/'models-manifest.json'):
    values=json.loads(Path(path).read_text(encoding='utf-8'))['resources']
    if set(values)!=FILES:raise ValueError('Unexpected model manifest entries')
    return values


def verify(root=ROOT,values=None):
    values=manifest() if values is None else values
    for relative,info in values.items():
        path=Path(root)/relative
        if not path.is_file() or path.stat().st_size!=info['bytes'] or digest(path)!=info['sha256']:
            raise ValueError('Model missing or checksum mismatch: '+relative)
    return len(values)


def restore(archive,root=ROOT,values=None):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    values=manifest() if values is None else values
    if set(values)!=FILES:raise ValueError('Unexpected model entries')
    with zipfile.ZipFile(archive) as source:
        names=source.namelist()
        if len(names)!=len(values) or set(names)!=set(values):
            raise ValueError('ZIP entries must match the five-model allowlist')
        # Validate all existing destinations before writing/replacing anything.
        for name,info in values.items():
            if source.getinfo(name).file_size!=info['bytes']:raise ValueError('ZIP size mismatch: '+name)
            target=(root/name).resolve()
            if not target.is_relative_to(root):raise ValueError('Model path escaped project')
            if target.exists() and (not target.is_file() or digest(target)!=info['sha256']):
                raise ValueError('Existing model differs; move it aside manually: '+name)
        with tempfile.TemporaryDirectory(dir=root,prefix='.sentinel-model-restore-') as staging:
            staging=Path(staging).resolve()
            if staging.parent!=root:raise ValueError('Restore staging escaped project')
            for i,(name,info) in enumerate(values.items()):
                target=staging/str(i)
                with source.open(name) as inp,target.open('wb') as out:shutil.copyfileobj(inp,out)
                if digest(target)!=info['sha256']:raise ValueError('ZIP checksum mismatch: '+name)
            for i,name in enumerate(values):
                target=root/name
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    (staging/str(i)).replace(target)
    return verify(root,values)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,help='Downloaded Sentinel-models-v0.3.1.zip')
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if args.archive:count=restore(args.archive)
    elif args.verify:count=verify()
    else:parser.error('Use --archive PATH or --verify')
    print(f'{count} model files verified. Development resources are ready.')


if __name__=='__main__':main()
