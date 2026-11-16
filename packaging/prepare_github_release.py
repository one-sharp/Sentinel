"""Prepare audited release assets; never copy project data or virtualenvs."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build_exe import audit_distribution,ASR
from restore_models import manifest,verify,digest


def main():
    out=ROOT/'release';out.mkdir(exist_ok=True)
    verify()
    audit_distribution(ROOT/'dist/Sentinel')
    archive=ROOT/'model.tar.bz2'
    # This archive has the exact size of the official sherpa release asset.
    if archive.stat().st_size!=1047319737:raise ValueError('Unexpected original ASR archive size')
    allowed={ASR+'/'+name for name in ('README.md','encoder.onnx','decoder.onnx','encoder.int8.onnx','decoder.int8.onnx','tokens.txt')}
    allowed|={ASR+'/test_wavs/'+name for name in ('0.wav','1.wav','2.wav','3.wav','8k.wav')}
    seen=set()
    with tarfile.open(archive,'r|bz2') as source:
        for item in source:
            name=item.name.removeprefix('./').rstrip('/')
            if item.isdir():
                if name not in (ASR,ASR+'/test_wavs'):raise ValueError('Unexpected archive directory')
            elif item.isfile() and name in allowed:seen.add(name)
            else:raise ValueError('Unapproved original model archive member: '+name)
    if seen!=allowed:raise ValueError('Original model archive inventory differs')
    print('Original public model archive inventory verified',flush=True)
    models=out/'Sentinel-models-v0.3.1.zip'
    with zipfile.ZipFile(models,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as target:
        for name in sorted(manifest()):target.write(ROOT/name,name)
    with zipfile.ZipFile(models) as target:
        if target.testzip():raise ValueError('Model ZIP integrity failure')
    print('Created '+models.name,flush=True)
    portable=out/'Sentinel-portable-v0.3.1.zip'
    with zipfile.ZipFile(portable,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as target:
        for path in sorted((ROOT/'dist/Sentinel').rglob('*')):
            if path.is_file():target.write(path,Path('Sentinel')/path.relative_to(ROOT/'dist/Sentinel'))
    with zipfile.ZipFile(portable) as target:
        if target.testzip():raise ValueError('Portable ZIP integrity failure')
    print('Created '+portable.name,flush=True)
    files=[out/'Sentinel-Setup.exe',models,portable,archive]
    wheels=out/'Sentinel-dev-wheels-py312-win64.zip'
    if wheels.exists():files.append(wheels)
    sums=[]
    for file in files:
        if file.stat().st_size>=2*1024**3:raise ValueError('Release asset exceeds 2 GiB')
        sums.append(digest(file)+'  '+file.name)
        print(file.name,file.stat().st_size,flush=True)
    (out/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    print('Release asset checksums saved',flush=True)


if __name__=='__main__':main()
