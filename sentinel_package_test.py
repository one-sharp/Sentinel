"""Explicit frozen-app diagnostics: --self-test [--test-microphone] [--test-llm].

Requires an isolated SENTINEL_DATA_DIR. Never silently runs on normal startup.
"""
import ctypes
import gc
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

from sentinel_paths import user_data_dir, ASSETS_DIR, ASR_MODEL_DIR, SPEAKER_MODEL_DIR, FROZEN


def run_self_test(llm=False, microphone=False):
    if not os.getenv('SENTINEL_DATA_DIR'):
        raise ValueError('Self-test requires an isolated SENTINEL_DATA_DIR')
    # Tk test roots must be reclaimed on their creating thread, including when
    # native/ASR workers allocate Python objects between separate test cases.
    gc_enabled = gc.isenabled()
    gc.disable()
    results = {'frozen':FROZEN,'executable':sys.executable,'data_dir':str(user_data_dir()),
               'time':datetime.now().astimezone().isoformat(),'cases':[]}
    def case(name, fn):
        started = time.perf_counter()
        try:
            details = fn()
            result = {'name':name,'passed':True,'details':details}
        except Exception as exc:
            result = {'name':name,'passed':False,'error':str(exc),'traceback':traceback.format_exc()}
        result['seconds'] = round(time.perf_counter()-started,3)
        results['cases'].append(result)
        print(name,result['passed'],flush=True)

    def imports():
        import numpy, scipy.signal, sounddevice, sherpa_onnx, openai, psutil, _cffi_backend
        modules = (numpy,scipy.signal,sounddevice,sherpa_onnx,openai,psutil,_cffi_backend)
        paths = {m.__name__:str(m.__file__) for m in modules}
        if FROZEN and not all(str(Path(p).resolve()).startswith(str(Path(sys._MEIPASS).resolve())) for p in paths.values()):
            raise RuntimeError('A runtime dependency was loaded from outside the bundle')
        return paths
    case('standalone_imports', imports)

    def models():
        import numpy as np
        import sherpa_onnx as sherpa
        from sentinel_live import build_recognizer
        from sentinel_speakers import MODEL,VAD_MODEL,unit
        recognizer = build_recognizer(ASR_MODEL_DIR,2,1.2)
        stream = recognizer.create_stream()
        stream.accept_waveform(16000,np.zeros(32000,np.float32))
        count = 0
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream); count += 1
        extractor = sherpa.SpeakerEmbeddingExtractor(sherpa.SpeakerEmbeddingExtractorConfig(model=str(MODEL),num_threads=1,provider='cpu'))
        voice = extractor.create_stream()
        voice.accept_waveform(16000,np.random.default_rng(2).normal(0,.01,48000).astype(np.float32))
        voice.input_finished()
        embedding = unit(extractor.compute(voice))
        if len(embedding) != 192: raise RuntimeError('CAM++ dimension mismatch')
        cfg = sherpa.VadModelConfig();cfg.silero_vad.model=str(VAD_MODEL);cfg.sample_rate=16000
        vad = sherpa.VoiceActivityDetector(cfg,buffer_size_in_seconds=15)
        for _ in range(50):vad.accept_waveform(np.zeros(512,np.float32))
        vad.flush()
        if count < 1 or not vad.empty():raise RuntimeError('ASR decoding / VAD silence check failed')
        return {'asr_decode_calls':count,'cam_dimension':len(embedding),'vad_silence_empty':vad.empty(),
                'model_path':str(ASR_MODEL_DIR)}
    case('real_asr_cam_vad_models', models)

    def data():
        import numpy as np
        from sentinel_desktop import NoteStore,DesktopPreferences
        from sentinel_speakers import MODEL,save_profile,load_profile
        from sentinel_paths import profile_path
        store = NoteStore()
        record = store.save('打包验证：私人输入未说出口','我在想','package-test')
        if store.read()[-1]['note_id'] != record['note_id']:raise RuntimeError('Note readback failed')
        settings = DesktopPreferences();settings.save(topmost=False)
        if DesktopPreferences().value['topmost']:raise RuntimeError('Settings readback failed')
        path = profile_path()
        old = path.read_bytes() if path.exists() else None
        digest = hashlib.sha256(Path(MODEL).read_bytes()).hexdigest()
        try:
            save_profile(path,np.ones(192,np.float32),digest,15)
            vector = load_profile(path,digest,192)
            if len(vector)!=192:raise RuntimeError('Profile readback failed')
        finally:
            if old is None:path.unlink(missing_ok=True)
            else:path.write_bytes(old)
        return {'notes':str(store.directory),'preferences':str(settings.path),'profile':str(path)}
    case('user_data_roundtrip', data)

    def graph_view():
        import tkinter as tk
        from types import SimpleNamespace
        from sentinel_logic import GraphDocument
        from sentinel_logic_ui import LogicGraphWindow
        doc=GraphDocument('package-logic-view',goal='仅测试图与原话，不录音',directory=user_data_dir()/'logic_qa')
        doc.observe(dict(kind='final',turn_id=1,text='我还没有同意预付款',speaker_id='A',role='我方',start_s=1.,end_s=3.))
        doc.apply({'nodes':[dict(id='block1',topic='付款',kind='blocker',title='预付款未同意',text='仍需讨论付款条件',basis='stated',
                               evidence=[dict(turn_id=1,quote='我还没有同意预付款')])],'edges':[]},doc.turns)
        doc.save();loaded=GraphDocument.load(doc.path)
        root=tk.Tk();root.withdraw()
        view=LogicGraphWindow(SimpleNamespace(root=root,topmost=tk.BooleanVar(root,value=False),logic_document=loaded))
        try:
            view.open(loaded);root.update();view.inspect_node(loaded.nodes['block1'])
            if '1.0–3.0s' not in view.inspector.get('1.0','end'):raise RuntimeError('Graph evidence UI failed')
            view.scale(.8);view.overview();view.window.withdraw();view.open(loaded)
            return {'graph_nodes':len(loaded.nodes),'evidence_readback':True,'canvas_items':len(view.canvas.find_all())}
        finally:root.destroy()
    case('logic_graph_view_and_persistence',graph_view)

    if microphone:
        def mic():
            import sounddevice as sd
            from sentinel_live import run,create_parser
            import tkinter as tk
            from sentinel_panel import SentinelPanel
            streams=[]
            original=sd.InputStream
            def track(*a,**kw):
                instance=original(*a,**kw);streams.append(instance);return instance
            args=create_parser().parse_args(['--speakers'])
            args.llm=False
            events=[]
            root=tk.Tk();root.withdraw()
            panel=SentinelPanel(root,args,run)
            original_handle=panel.handle
            def on_event(event):
                events.append(event)
                return original_handle(event)
            panel.handle=on_event
            try:
                sd.InputStream=track
                for _ in range(2):
                    panel.start_button.invoke()
                    deadline=time.monotonic()+45
                    listening_at=None
                    while panel.controller.busy and time.monotonic()<deadline:
                        root.update()
                        if panel.mic_on and listening_at is None:listening_at=time.monotonic()
                        if listening_at is not None and time.monotonic()-listening_at>=3:
                            panel.stop_button.invoke()
                        time.sleep(.01)
                    if panel.controller.busy:raise RuntimeError('Backend did not stop through the UI lifecycle')
                summaries=[e['summary'] for e in events if e['kind']=='stopped']
                if len(streams)!=2 or not all(s.closed for s in streams):raise RuntimeError('Microphone did not close across two sessions')
                if len(summaries)!=2 or any(s['audio_processed_s']<1 for s in summaries):raise RuntimeError('No real microphone audio was processed')
                return {'streams':len(streams),'all_closed':all(s.closed for s in streams),'summaries':summaries,
                        'start_stop_from_real_tk_buttons':True}
            finally:
                sd.InputStream=original
                panel.controller.stop()
                if panel.controller.thread:panel.controller.thread.join()
                try:root.destroy()
                except tk.TclError:pass
        case('real_microphone_start_stop_release', mic)

    if llm:
        def check_llm():
            from sentinel_observer import check_llm_connection
            check_llm_connection()
            return 'fixed synthetic connection text only; no microphone transcript sent'
        case('real_llm_connection',check_llm)
        def check_logic_llm():
            from sentinel_logic import GraphDocument,GraphWorker
            doc=GraphDocument('package-logic-llm',goal='验收后付款，明确各方意见',directory=user_data_dir()/'logic_qa')
            lines=[('B','对方','先付三成预付款，我们才安排交货。'),('A','我方','我还没有同意预付款，我希望验收后再付。'),
                   ('B','对方','那先确认样品，付款条件下午再谈。'),('A','我方','同意先确认样品，但这不代表我接受三成预付款。')]
            for i,(actor,role,text) in enumerate(lines,1):
                doc.observe(dict(kind='final',turn_id=i,text=text,speaker_id=actor,role=role,start_s=i*4.,end_s=i*4.+3.))
            worker=GraphWorker(doc,interval=1);worker.notify()
            try:
                end=time.monotonic()+40
                while len(doc.covered)<4 and time.monotonic()<end:
                    if '失败' in doc.status or '异常' in doc.status:raise RuntimeError(doc.status)
                    time.sleep(.05)
                if len(doc.covered)!=4 or not doc.nodes:raise RuntimeError('Real logic graph analysis did not complete')
                return {'synthetic_text_only':True,'covered_turns':len(doc.covered),'nodes':len(doc.nodes),'edges':len(doc.edges)}
            finally:worker.stop();worker.thread.join(30)
        case('real_logic_graph_llm',check_logic_llm)

    def gui():
        import tkinter as tk
        from sentinel_panel import SentinelPanel
        from sentinel_live import create_parser,run
        from sentinel_windows import WindowsDesktop, WM_TRAY, MESSAGE_WINDOW
        root=tk.Tk();root.withdraw()
        panel=SentinelPanel(root,create_parser().parse_args([]),run)
        native=WindowsDesktop(lambda **e:panel.desktop_events.put(e), hotkeys={})
        panel.desktop=native;native.start()
        saw_window=False
        try:
            end=time.monotonic()+10
            while time.monotonic()<end and not native.ready.is_set():root.update();time.sleep(.01)
            root.update()
            end=time.monotonic()+5
            while not hasattr(panel,'hotkey_result') and time.monotonic()<end:root.update();time.sleep(.01)
            panel.open_api_settings();root.update()
            saw_window=bool(panel.api_window.winfo_exists())
            panel.api_window.destroy()
            panel.set_layout('bar');panel.set_layout('detail');panel.set_layout('compact')
            panel.quick_note();root.update();panel.notes.editor.insert('1.0','EXE 笔记生命周期测试')
            panel.notes.save();panel.notes.close()
            user=ctypes.WinDLL('kernel32');user.GetConsoleWindow.restype=ctypes.c_void_p
            console=bool(user.GetConsoleWindow())
            native_ready=native.tray_available
            if not saw_window or not native_ready or (FROZEN and console):raise RuntimeError('GUI/API/tray/no-console check failed')
            if getattr(panel,'hotkey_result',{}).get('active') != {}:raise RuntimeError('Unexpected global hotkey registration')
            panel.hide_window(); root.update()
            native.user.PostMessageW(native.hwnd,native.user.RegisterWindowMessageW(MESSAGE_WINDOW+'.show'),0,0)
            end=time.monotonic()+3
            while panel.hidden and time.monotonic()<end:root.update();time.sleep(.01)
            if panel.hidden:raise RuntimeError('Single-instance restore message did not show the window')
            # Exercise native popup creation/dispatch; emulate selecting Exit so
            # the test never blocks on desktop input or touches another app.
            original_track=native.user.TrackPopupMenu
            native.user.TrackPopupMenu=lambda *args:104
            native.user.PostMessageW(native.hwnd,WM_TRAY,0,0x205)
            end=time.monotonic()+10
            try:
                while time.monotonic()<end and not native.closed.is_set():root.update();time.sleep(.01)
            finally:
                native.user.TrackPopupMenu=original_track
            if not native.closed.is_set():raise RuntimeError('Native service did not close')
            return {'api_setup_window':saw_window,'native_tray':native_ready,'native_closed':native.closed.is_set(),
                    'has_console_window':console,'real_backend_binding':panel.controller.runner is run,
                    'single_instance_restore':True,'tray_exit_command':True,'tray_menu_selection':'simulated',
                    'global_hotkeys_registered':len(panel.hotkey_result.get('active',{}))}
        finally:
            native.close();native.thread.join(5)
            try:root.destroy()
            except tk.TclError:pass
    case('actual_tk_ui_tray_hotkeys_notes_exit',gui)
    results['passed']=all(r['passed'] for r in results['cases'])
    path=user_data_dir()/'self-test.json'
    path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Self-test:',results['passed'],str(path),flush=True)
    gc.collect()
    if gc_enabled:gc.enable()
    return results['passed']
