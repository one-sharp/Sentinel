"""Audit the exact Git index before the first upload. Never print credentials."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from dotenv import dotenv_values
    keys=[]
    key=dotenv_values(ROOT/'.env').get('LLM_API_KEY','') or ''
    if len(key)>12 and not key.startswith('YOUR_'):keys.append(key.encode())
    try:
        from sentinel_config import load_saved_config
        key=load_saved_config().get('LLM_API_KEY','')
        if len(key)>12 and not key.startswith('YOUR_'):keys.append(key.encode())
    except (OSError,ValueError):pass
    files=subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=ROOT).decode('utf-8').split('\0')
    files=[p for p in files if p]
    if not files:raise ValueError('Nothing staged')
    private={'logs','private_notes','voice_profiles','logic_graphs','packaging-qa','.downloads','.venv','.build_tools','dist','build','__pycache__'}
    total=0
    for name in files:
        path=Path(name)
        if set(path.parts)&private or (path.name.startswith('.env') and path.name!='.env.example') or path.name in {'api_config.json','desktop_settings.json','self.json'}:
            raise ValueError('Private path in Git index: '+name)
        if path.suffix.lower() in {'.zip','.bz2','.onnx','.wav','.log','.jsonl','.pem','.key','.exe','.dll'}:
            raise ValueError('Unapproved binary/data in Git index: '+name)
        content=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
        if len(content)>10*1024**2:raise ValueError('Unexpectedly large source file: '+name)
        if any(secret in content for secret in keys):raise ValueError('Application API credential found in Git index: '+name)
        total+=len(content)
    print(f'Git export audit passed: {len(files)} files, {total:,} bytes; no application credentials or private data paths.')


if __name__=='__main__':main()
