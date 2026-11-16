"""Repack installed distributions for this private project's exact offline restore.

Preserves package metadata/licenses; omits bytecode, installation paths and
generated launchers. Pip regenerates Python launchers in the new environment.
"""
import base64
import csv
from importlib import metadata
import io
import json
from pathlib import Path
import re
import sys
import zipfile
import hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from packaging.tags import sys_tags
from restore_models import digest


def main():
    site=(Path(sys.prefix)/'Lib/site-packages').resolve()
    scripts=(Path(sys.prefix)/'Scripts').resolve()
    out=ROOT/'.downloads/wheelhouse';out.mkdir(parents=True,exist_ok=True)
    from dotenv import dotenv_values
    secret=(dotenv_values(ROOT/'.env').get('LLM_API_KEY') or '').encode()
    if len(secret)<12 or secret.startswith(b'YOUR_'):secret=b''
    lock={re.sub(r'[-_.]+','-',line.split('==')[0]).lower():line.split('==')[1]
          for line in (ROOT/'requirements-lock.txt').read_text().splitlines() if '==' in line}
    exported=[]
    for dist in metadata.distributions():
        name=dist.metadata['Name'];normalized=re.sub(r'[-_.]+','-',name).lower()
        if normalized not in lock:continue
        if dist.version!=lock[normalized]:raise ValueError('Installed version changed: '+name)
        info=next((str(f).replace('\\','/') for f in dist.files or [] if str(f).replace('\\','/').endswith('.dist-info/WHEEL')),None)
        if info is None:raise ValueError('No wheel metadata for '+name)
        prefix=info.rsplit('/',1)[0];stem=prefix.removesuffix('.dist-info')
        wheel=dist.read_text('WHEEL')
        tags={line[5:].strip() for line in wheel.splitlines() if line.startswith('Tag:')}
        tag=next((str(t) for t in sys_tags() if str(t) in tags),None)
        if tag is None:raise ValueError('No compatible Windows Python tag: '+name)
        wheel='\n'.join(line for line in wheel.splitlines() if not line.startswith(('Tag:','Generator:')))
        wheel+='\nGenerator: Sentinel private environment export\nTag: '+tag+'\n'
        launchers={p.name.lower()+'.exe' for p in dist.entry_points if p.group in ('console_scripts','gui_scripts')}
        target=out/(stem+'-'+tag+'.whl');records=[]
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            for file in dist.files or []:
                original=str(file).replace('\\','/')
                if '__pycache__' in Path(original).parts or original.endswith(('.pyc','.pyo')):continue
                path=Path(dist.locate_file(file)).resolve()
                if path.is_relative_to(site):entry=path.relative_to(site).as_posix()
                elif path.is_relative_to(scripts):
                    if path.name.lower() in launchers:continue
                    entry=stem+'.data/scripts/'+path.relative_to(scripts).as_posix()
                else:raise ValueError('Unexpected package file location for '+name)
                if entry in {prefix+'/'+s for s in ('RECORD','RECORD.jws','RECORD.p7s','INSTALLER','REQUESTED','direct_url.json')}:continue
                if not path.is_file():raise ValueError('Missing installed package file: '+entry)
                content=wheel.encode() if entry==prefix+'/WHEEL' else path.read_bytes()
                if secret and secret in content:raise ValueError('Application credential detected in package file: '+entry)
                archive.writestr(entry,content)
                sha=base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
                records.append((entry,'sha256='+sha,str(len(content))))
            records.append((prefix+'/RECORD','',''))
            output=io.StringIO(newline='');csv.writer(output,lineterminator='\n').writerows(records)
            archive.writestr(prefix+'/RECORD',output.getvalue().encode())
        exported.append(target)
        print('Exported',target.name,target.stat().st_size,flush=True)
    if len(exported)!=len(lock):raise ValueError('Not all locked packages were exported')
    bundle=ROOT/'release/Sentinel-dev-wheels-py312-win64.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
        for wheel in sorted(exported):archive.write(wheel,'wheelhouse/'+wheel.name)
        summary={'python':'3.12','platform':'Windows x64','source':'repacked installed distributions, licenses retained',
                 'files':{w.name:{'bytes':w.stat().st_size,'sha256':digest(w)} for w in exported}}
        archive.writestr('wheelhouse/manifest.json',json.dumps(summary,indent=2))
    sums=ROOT/'release/SHA256SUMS.txt'
    lines=[line for line in sums.read_text().splitlines() if not line.endswith(bundle.name)] if sums.exists() else []
    sums.write_text('\n'.join(lines+[digest(bundle)+'  '+bundle.name])+'\n',encoding='ascii')
    print('Created',bundle.name,bundle.stat().st_size,flush=True)


if __name__=='__main__':main()
