# PyInstaller onedir + windowed. Model files are staged by build_exe.py afterwards.
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, copy_metadata

root = Path(SPECPATH)
binaries = collect_dynamic_libs('sherpa_onnx')
datas = [(str(root / 'assets'), 'assets')]
for package in ('httpx2', 'httpcore2', 'openai', '_sounddevice_data'):
    datas += collect_data_files(package)
for package in ('openai','sherpa-onnx','numpy','scipy','sounddevice','psutil','python-dotenv','httpx2','httpcore2','pydantic','pydantic_core'):
    datas += copy_metadata(package)
a = Analysis([str(root/'sentinel_app.pyw')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=['_cffi_backend','_sounddevice','_sounddevice_data','sherpa_onnx.lib._sherpa_onnx',
                            'scipy.signal','openai','httpx2','httpcore2','pydantic_core'],
             excludes=['matplotlib','pandas','torch','pytest','PIL'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Sentinel', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, icon=str(root/'assets/sentinel-idle.ico'),
          version=str(root/'packaging/version_info.txt'), contents_directory='_internal')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Sentinel')
