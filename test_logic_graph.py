import gc
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest

from sentinel_logic import GraphDocument,GraphWorker
from sentinel_logic_ui import display_nodes,layout_nodes,LogicGraphWindow

ROOT=Path(__file__).resolve().parent


def turn(ident,text,role='对方',actor='B'):
    return dict(kind='final',turn_id=ident,text=text,role=role,speaker_id=actor,start_s=ident*4.,end_s=ident*4.+3.)


def node(ident='n1',source=1,quote='先付三成',kind='proposal'):
    return dict(id=ident,kind=kind,topic='付款',title='对方提出预付三成',text='是否接受仍未明确',basis='stated',
                evidence=[dict(turn_id=source,quote=quote)])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-')
        self.doc=GraphDocument('test-session','验收后付款',directory=self.temp.name)
        self.doc.observe(turn(1,'先付三成，才能排期'))
        self.doc.observe(turn(2,'我还没有同意，先验收再谈付款','我方','A'))
    def tearDown(self):self.temp.cleanup()
    def test_no_128_turn_history_loss_and_no_private_notes_or_partials(self):
        for i in range(3,204):self.doc.observe(turn(i,'后续确认片段'))
        self.doc.observe(dict(kind='partial',turn_id=204,text='不确定临时文本'))
        self.doc.observe(dict(kind='private_note',turn_id=205,text='私人想法不能当作语音'))
        self.assertEqual(len(self.doc.turns),203)
        self.assertEqual(self.doc.turns[1]['text'],'先付三成，才能排期')
    def test_intent_always_inferred_and_roles_come_from_binding(self):
        n=node(kind='intent');n['role']='我方'
        self.doc.apply(dict(nodes=[n],edges=[]),self.doc.turns)
        self.assertEqual(self.doc.nodes['n1']['basis'],'inferred')
        self.assertEqual(self.doc.nodes['n1']['role'],'对方')
    def test_unknown_cannot_be_assigned_a_side_by_the_model(self):
        self.doc.observe(turn(1,'先付三成','我方','unknown'))
        self.doc.apply(dict(nodes=[node()],edges=[]),self.doc.turns)
        self.assertEqual(self.doc.nodes['n1']['role'],'未绑定')
    def test_fabricated_quote_and_dangling_edge_rejected_atomically(self):
        self.doc.apply(dict(nodes=[node()],edges=[]),self.doc.turns)
        for patch in (dict(nodes=[node(quote='已经无条件同意')],edges=[]),
                      dict(nodes=[node('n2')],edges=[dict(source='n1',target='missing',relation='推进',evidence=[])])):
            with self.assertRaises(ValueError):self.doc.apply(patch,self.doc.turns)
        self.assertEqual(list(self.doc.nodes),['n1'])
    def test_corrections_invalidate_related_nodes_edges_and_late_result(self):
        supplied=json.loads(json.dumps(self.doc.turns));supplied={int(k):v for k,v in supplied.items()}
        self.doc.apply(dict(nodes=[node(),node('n2',2,'我还没有同意',kind='blocker')],edges=[
            dict(source='n1',target='n2',relation='反驳',evidence=[dict(turn_id=2,quote='我还没有同意')])]),supplied)
        self.doc.observe(dict(kind='attribution_update',turn_id=1,speaker_id='unknown',role='未绑定'))
        self.assertNotIn('n1',self.doc.nodes);self.assertEqual(self.doc.edges,{})
        with self.assertRaises(ValueError):self.doc.apply(dict(nodes=[node()],edges=[]),supplied)
        self.assertEqual(self.doc.batch()[0]['speaker_id'],'unknown')
    def test_new_roles_do_not_rewrite_private_goal_or_speech(self):
        self.doc.apply(dict(nodes=[node()],edges=[]),self.doc.turns)
        self.doc.observe(dict(kind='speaker_roles',speakers={'B':'第三方'}))
        self.assertFalse(self.doc.nodes)
        self.assertEqual(self.doc.turns[1]['role'],'第三方')
        self.assertEqual(self.doc.goal,'验收后付款')
    def test_patch_merge_retains_early_topics_and_evidence_roundtrip(self):
        self.doc.apply(dict(nodes=[node()],edges=[]),self.doc.turns)
        self.doc.apply(dict(nodes=[node('n2',2,'我还没有同意',kind='blocker')],edges=[
            dict(source='n1',target='n2',relation='卡住',evidence=[dict(turn_id=2,quote='我还没有同意')])]),self.doc.turns)
        self.doc.save();restored=GraphDocument.load(self.doc.path)
        self.assertEqual(len(restored.nodes),2);self.assertEqual(len(restored.edges),1)
        self.assertEqual(restored.turns,self.doc.turns)
    def test_smalltalk_reviewed_without_inventing_semantic_nodes(self):
        self.doc.apply(dict(nodes=[],edges=[],reviewed_turn_ids=[1,2]),self.doc.turns)
        self.assertEqual(self.doc.batch(),[])
        self.assertEqual(len(display_nodes(self.doc.snapshot())),2)
    def test_lanes_and_topics_are_branches_not_one_column(self):
        a=node();a.update(role='对方')
        b=node('n2',2,'我还没有同意',kind='blocker');b.update(role='我方')
        positions,bands=layout_nodes([a,b])
        self.assertNotEqual(positions['n1'][0],positions['n2'][0])
        self.assertEqual(len(bands),1)


class WorkerTests(unittest.TestCase):
    def test_shutdown_discards_inflight_result_and_closes_owned_client(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-') as directory:
            doc=GraphDocument('worker','goal',directory=directory);doc.observe(turn(1,'先付三成'))
            entered,release=threading.Event(),threading.Event()
            closed=[]
            def create(**kwargs):
                entered.set();release.wait(3)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(dict(nodes=[node()],edges=[]))))])
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),close=lambda:closed.append(True))
            with patch('sentinel_observer.create_client',return_value=(client,'test-model')):
                service=GraphWorker(doc,interval=.01);service.notify()
                try:
                    self.assertTrue(entered.wait(2))
                    service.stop();release.set();service.thread.join(3)
                    self.assertFalse(service.thread.is_alive());self.assertFalse(doc.nodes)
                    self.assertEqual(closed,[True]);self.assertTrue(doc.path.exists())
                finally:release.set();service.stop();service.thread.join(3)
    def test_full_batch_catchup_is_background_and_bounded(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-') as directory:
            doc=GraphDocument('catchup',directory=directory)
            for i in range(1,71):doc.observe(turn(i,'先付三成'))
            batches=[]
            def create(**kwargs):
                payload=json.loads(kwargs['messages'][1]['content']);batch=payload['new_turns'];batches.append(len(batch))
                result=dict(nodes=[node('n'+str(t['turn_id']),t['turn_id']) for t in batch[:24]],edges=[],reviewed_turn_ids=[t['turn_id'] for t in batch])
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])
            service=GraphWorker(doc,client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),model='test-model',interval=.01)
            try:
                service.notify();end=time.monotonic()+5
                while len(doc.covered)<70 and time.monotonic()<end:time.sleep(.01)
                self.assertEqual(len(doc.covered),70);self.assertEqual(batches,[32,32,6])
            finally:service.stop();service.thread.join(3)


class ViewTests(unittest.TestCase):
    def test_click_inspects_audio_evidence_and_history_is_read_only(self):
        gc.collect();gc.disable()
        root=tk.Tk();root.withdraw()
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='.sentinel-test-') as directory:
            doc=GraphDocument('view',directory=directory);doc.observe(turn(1,'先付三成'))
            doc.apply(dict(nodes=[node(kind='intent')],edges=[]),doc.turns)
            p=SimpleNamespace(root=root,topmost=tk.BooleanVar(root,value=False),logic_document=doc)
            view=LogicGraphWindow(p)
            try:
                view.open(doc);root.update();view.inspect_node(doc.nodes['n1'])
                text=view.inspector.get('1.0','end')
                self.assertIn('推测',text);self.assertIn('先付三成',text);self.assertIn('4.0–7.0s',text)
                self.assertGreater(len(view.canvas.find_all()),5)
                view.scale(.8);view.fit();view.window.withdraw();view.open(doc)
                self.assertEqual(view.selected,'n1')
                from unittest.mock import patch
                with patch.object(doc,'snapshot',side_effect=AssertionError('No full copy on unchanged UI tick')):
                    view.refresh()
                doc.observe(dict(kind='attribution_update',turn_id=1,speaker_id='unknown',role='未绑定'))
                view.refresh()
                self.assertIn('旧判断已失效',view.inspector.get('1.0','end'))
                view.inspect_edge(dict(relation='回应',evidence=[dict(turn_id=1,quote='先付三成')]))
                doc.observe(dict(kind='attribution_update',turn_id=1,speaker_id='B',role='第三方'))
                view.refresh();self.assertIn('图已更新',view.inspector.get('1.0','end'))
                for i in range(2,402):doc.observe(turn(i,'长会话保留原话'))
                view.refresh();self.assertEqual(view.page_text.get(),'1/2')
                self.assertIn('raw-1',view.node_map)
                view.change_page(1);self.assertIn('raw-401',view.node_map)
            finally:root.destroy();gc.collect();gc.enable()


if __name__=='__main__':unittest.main()
