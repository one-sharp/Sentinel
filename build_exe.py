"""Actual EXE builder with an explicit public-resource allowlist and audit."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ASR = 'sherpa-onnx-streaming-paraformer-bilingual-zh-en'
RESOURCE_FILES = [ASR+'/'+name for name in ('encoder.int8.onnx','decoder.int8.onnx','tokens.txt')] + [
    'speaker_models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx', 'speaker_models/silero_vad.onnx']


def audit_distribution(directory):
    directory = Path(directory).resolve()
    banned_names = {'.env','desktop_settings.json','api_config.json','self.json'}
    banned_parts = {'logs','private_notes','voice_profiles','logic_graphs','test_audio','.venv','__pycache__'}
    forbidden = [str(p.relative_to(directory)) for p in directory.rglob('*') if
                 (p.name in banned_names or set(p.relative_to(directory).parts) & banned_parts or p.suffix == '.jsonl')]
    if forbidden:
        raise RuntimeError('Private/unapproved files in bundle: '+str(forbidden))
    actual = {p.relative_to(directory/'resources').as_posix() for p in (directory/'resources').rglob('*') if p.is_file()}
    if actual != set(RESOURCE_FILES):
        raise RuntimeError('Model resource allowlist mismatch: '+str(actual))
    # Inspect the PE, do not infer GUI mode from the file extension.
    import pefile
    pe = pefile.PE(str(directory/'Sentinel.exe'))
    if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Subsystem != 2:
        raise RuntimeError('Expected x64 Windows GUI executable')
    if not list((directory/'_internal').glob('python3*.dll')):
        raise RuntimeError('Python runtime missing from standalone bundle')
    from dotenv import dotenv_values
    secret = (dotenv_values(ROOT/'.env').get('LLM_API_KEY') or '').encode('utf-8')
    secret_hits = []
    if len(secret) > 12 and b'YOUR_' not in secret:
        for path in directory.rglob('*'):
            if not path.is_file():continue
            tail = b''
            with path.open('rb') as file:
                while chunk := file.read(4*1024*1024):
                    if secret in tail+chunk:
                        secret_hits.append(str(path.relative_to(directory)));break
                    tail = chunk[-len(secret):]
    if secret_hits:
        raise RuntimeError('Local API credential found in bundle files: '+str(secret_hits))
    return {'private_files_found':forbidden, 'pe_machine':'x64', 'pe_subsystem':'Windows GUI',
            'local_api_key_found':bool(secret_hits),
            'files':sum(p.is_file() for p in directory.rglob('*')),
            'bytes':sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())}


def main():
    target = (ROOT/'dist/Sentinel').resolve()
    work = (ROOT/'build/pyinstaller').resolve()
    # Validate PyInstaller's replacement/recursive-cleanup destinations first.
    for path in (target, work):
        if not path.is_relative_to(ROOT) or path == ROOT:
            raise ValueError('Build target escaped project')
    for relative in RESOURCE_FILES:
        if not (ROOT/relative).is_file():
            raise FileNotFoundError(relative)
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT/'.build_tools/pyinstaller-cache'))
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(ROOT/'dist'),
                    '--workpath',str(work),str(ROOT/'Sentinel.spec')], cwd=ROOT, env=env, check=True)
    manifest = {}
    for relative in RESOURCE_FILES:
        source, dest = ROOT/relative, target/'resources'/relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source,dest)
        manifest[relative] = {'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
    (target/'build-manifest.json').write_text(json.dumps({'version':'0.3.1','resources':manifest},indent=2),encoding='utf-8')
    audit = audit_distribution(target)
    (ROOT/'build').mkdir(exist_ok=True)
    (ROOT/'build/distribution-audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
