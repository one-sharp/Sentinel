"""Interactive multi-lane graph, with original audio-time evidence on selection."""
import textwrap
import unicodedata
import tkinter as tk
from tkinter import ttk,filedialog
from sentinel_ui import BG,TEXT,MUTED,GREEN,AMBER,BUTTON,FONT
from sentinel_logic import GraphDocument,KINDS,graph_dir

LANES={'我方':0,'多方':1,'对方':2,'第三方':3,'未绑定':3}
STAGES={'issue':0,'intent':1,'proposal':2,'position':2,'condition':3,'question':3,'blocker':4,'progress':5,'resolution':5,'turn':6}


def preview(text, width=28, lines=3):
    rows=[''];n=0
    for ch in text.replace('\n',' '):
        w=2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if n+w>width:
            if len(rows)==lines:return '\n'.join(rows)[:-1]+'…'
            rows.append('');n=0
        rows[-1]+=ch;n+=w
    return '\n'.join(rows)


def display_nodes(snapshot):
    nodes=list(snapshot['nodes'])
    used={ref['turn_id'] for node in nodes for ref in node['evidence']}
    for turn in snapshot['turns']:
        if turn['turn_id'] not in used:
            nodes.append({'id':f"raw-{turn['turn_id']}",'kind':'turn','topic':'尚未归纳的原话',
                          'role':turn['role'],'basis':'stated','title':turn['text'],'text':turn['text'],
                          'evidence':[{'turn_id':turn['turn_id'],'quote':turn['text']}]})
    return nodes


def layout_nodes(nodes):
    """Columns are roles, grouped in topic bands and semantic stages, not time."""
    positions={};y=130
    topics=list(dict.fromkeys(n['topic'] for n in nodes))
    bands=[]
    for topic in topics:
        start=y
        selected=[n for n in nodes if n['topic']==topic]
        for stage in sorted({STAGES[n['kind']] for n in selected}):
            buckets={lane:[n for n in selected if STAGES[n['kind']]==stage and LANES.get(n['role'],3)==lane] for lane in range(4)}
            rows=max(map(len,buckets.values()))
            for lane,bucket in buckets.items():
                for row,node in enumerate(bucket):positions[node['id']]=(30+lane*300,y+row*156)
            y+=rows*156
        bands.append((topic,start-28,y-8));y+=55
    return positions,bands


class LogicGraphWindow:
    def __init__(self,panel):
        self.panel=panel;self.window=None;self.doc=None;self.historical=False
        self.zoom=.8;self.signature=None;self.selected=None
        self.page=0;self.base_status=''

    def open(self,doc):
        self.doc=doc;self.historical=False;self.signature=None;self.page=0
        if self.window is None or not self.window.winfo_exists():self._build()
        self.window.deiconify();self.window.lift();self.refresh()

    def _build(self):
        w=self.window=tk.Toplevel(self.panel.root)
        w.title('Sentinel 哨兵 · 对话逻辑图')
        w.geometry(f'{min(1280,w.winfo_screenwidth()-80)}x{min(850,w.winfo_screenheight()-100)}')
        w.minsize(900,600);w.configure(bg=BG)
        w.attributes('-topmost',self.panel.topmost.get())
        w.protocol('WM_DELETE_WINDOW',w.withdraw)
        box=ttk.Frame(w,padding=14);box.pack(fill='both',expand=True)
        top=ttk.Frame(box);top.pack(fill='x')
        ttk.Label(top,text='对话逻辑图',font=(FONT,20,'bold')).pack(side='left')
        ttk.Button(top,text='打开历史图',command=self.history).pack(side='right')
        ttk.Button(top,text='回到本场',command=self.current).pack(side='right',padx=6)
        self.retry_button=ttk.Button(top,text='更新 / 重试',command=self.retry);self.retry_button.pack(side='right')
        self.status=tk.StringVar();ttk.Label(box,textvariable=self.status,wraplength=1150,foreground=MUTED).pack(fill='x',pady=(8,4))
        ttk.Label(box,text='实线：有原话依据   ·   虚线：意图推测 / 待核实   ·   橙色：卡点   ·   点击方框或箭头看证据',foreground=GREEN).pack(anchor='w')
        self.situation=tk.StringVar()
        ttk.Label(box,textvariable=self.situation,wraplength=1150,foreground=AMBER,font=(FONT,11,'bold')).pack(fill='x',pady=(6,0))
        tools=ttk.Frame(box);tools.pack(fill='x',pady=8)
        ttk.Label(tools,text='议题').pack(side='left')
        self.topic=tk.StringVar(value='全部议题')
        self.topics=ttk.Combobox(tools,textvariable=self.topic,state='readonly',width=25)
        self.topics.pack(side='left',padx=8);self.topics.bind('<<ComboboxSelected>>',self.select_topic)
        for label,command in (('－',lambda:self.scale(.8)),('＋',lambda:self.scale(1.25)),('适应宽度',self.fit),('全图',self.overview)):
            ttk.Button(tools,text=label,command=command).pack(side='left',padx=3)
        ttk.Button(tools,text='上一页',command=lambda:self.change_page(-1)).pack(side='left',padx=3)
        self.page_text=tk.StringVar()
        ttk.Label(tools,textvariable=self.page_text).pack(side='left')
        ttk.Button(tools,text='下一页',command=lambda:self.change_page(1)).pack(side='left',padx=3)
        ttk.Label(tools,text='滚轮上下浏览 · Shift+滚轮左右 · 中键拖动',foreground=MUTED).pack(side='right')
        split=ttk.Panedwindow(box,orient='horizontal');split.pack(fill='both',expand=True)
        left=ttk.Frame(split);right=ttk.Frame(split,width=300)
        split.add(left,weight=4);split.add(right,weight=1)
        self.canvas=tk.Canvas(left,bg='#0C1722',highlightthickness=0)
        vertical=ttk.Scrollbar(left,orient='vertical',command=self.canvas.yview)
        horizontal=ttk.Scrollbar(left,orient='horizontal',command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        vertical.pack(side='right',fill='y');horizontal.pack(side='bottom',fill='x');self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        self.canvas.bind('<Shift-MouseWheel>',lambda e:self.canvas.xview_scroll(-int(e.delta/120),'units'))
        self.canvas.bind('<ButtonPress-2>',lambda e:self.canvas.scan_mark(e.x,e.y))
        self.canvas.bind('<B2-Motion>',lambda e:self.canvas.scan_dragto(e.x,e.y,gain=1))
        ttk.Label(right,text='原话与依据',font=(FONT,13,'bold')).pack(anchor='w',pady=(0,8))
        self.inspector=tk.Text(right,width=30,wrap='word',font=(FONT,10),bg=BUTTON,fg=TEXT,bd=0,padx=12,pady=10,state='disabled')
        scroll=ttk.Scrollbar(right,command=self.inspector.yview);self.inspector.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.inspector.pack(fill='both',expand=True)
        self._inspect('点击节点查看角色、判断依据、音频起止时间及逐字原文。\n\n没有证据时，系统不会将推测显示为双方已达成的共识。')

    def refresh(self):
        if not self.window or not self.window.winfo_exists() or self.window.state()=='withdrawn' or self.doc is None:return
        # The panel polls at 80ms. Do not copy the complete conversation on
        # every tick when only the status text (or nothing) has changed.
        with self.doc.lock:
            signature=(id(self.doc),self.doc.revision)
            status,count=self.doc.status,len(self.doc.turns)
        self.base_status=('历史图 · ' if self.historical else '本场 · ')+status+f"  |  已确认 {count} 段"
        self.status.set(self.base_status)
        self.retry_button.configure(state='disabled' if self.historical else 'normal')
        if signature!=self.signature:
            snap=self.doc.snapshot()
            self.signature=signature
            resolved={e['source'] for e in snap['edges'] if e['relation']=='解决'
                      and any(n['id']==e['target'] and n['kind']=='resolution' for n in snap['nodes'])}
            blockers=[n['title'] for n in snap['nodes'] if n['kind']=='blocker' and n['id'] not in resolved]
            progress=[n['title'] for n in snap['nodes'] if n['kind'] in ('progress','resolution')]
            self.situation.set('图中卡点（AI归纳）｜'+('；'.join(blockers[-3:]) or '尚无已归纳的卡点')+
                               '     进展｜'+('；'.join(progress[-2:]) or '等待明确进展'))
            topics=['全部议题']+list(dict.fromkeys(n['topic'] for n in display_nodes(snap)))
            self.topics.configure(values=topics)
            if self.topic.get() not in topics:self.topic.set('全部议题')
            self.render()
            if self.selected:
                current=self.node_map.get(self.selected)
                if current:self.inspect_node(current)
                else:
                    self.selected=None
                    self._inspect('原文或归属已更新，所选旧判断已失效。请点击当前图中的节点重新查看依据。')
            else:
                self._inspect('图已更新。点击节点或箭头查看最新原话、身份归属与音频时间。')

    def render(self):
        if self.doc is None:return
        snap=self.doc.snapshot();nodes=display_nodes(snap)
        if self.topic.get()!='全部议题':nodes=[n for n in nodes if n['topic']==self.topic.get()]
        # Bound draw work, while keeping every earlier node reachable.
        pages=max(1,(len(nodes)+399)//400);self.page=max(0,min(self.page,pages-1))
        self.page_text.set(f'{self.page+1}/{pages}')
        nodes=nodes[self.page*400:(self.page+1)*400]
        self.status.set(self.base_status+(f' · 图分{pages}页，每页最多400节点' if pages>1 else ''))
        self.node_map={n['id']:n for n in nodes}
        positions,bands=layout_nodes(nodes);c=self.canvas;c.delete('all');z=self.zoom
        for i,label in enumerate(('我方的立场 / 意图','议题、矛盾与推进','对方的立场 / 意图','未知 / 第三方')):
            c.create_text((150+i*300)*z,22*z,text=label,fill=GREEN if i==0 else MUTED,font=(FONT,max(8,int(12*z)),'bold'))
        c.create_rectangle(330*z,48*z,870*z,106*z,outline=GREEN,fill='#17343A')
        c.create_text(600*z,77*z,text='本次用户目标（未必已说出口）\n'+preview(snap['goal'] or '尚未填写目标',70,1),fill=TEXT,font=(FONT,max(8,int(10*z))))
        for topic,top,bottom in bands:
            c.create_line(15*z,top*z,1210*z,top*z,fill='#314956')
            c.create_text(22*z,(top-12)*z,text=topic,anchor='w',fill=MUTED,font=(FONT,max(8,int(11*z)),'bold'))
        for index,edge in enumerate(snap['edges']):
            if edge['source'] not in positions or edge['target'] not in positions:continue
            x1,y1=positions[edge['source']];x2,y2=positions[edge['target']]
            if x1==x2:
                points=[x1+250,y1+56,x1+272,y1+56,x2+272,y2+56,x2+250,y2+56]
            else:
                a=x1+250 if x2>x1 else x1;b=x2 if x2>x1 else x2+250
                mid=(a+b)/2;points=[a,y1+56,mid,y1+56,mid,y2+56,b,y2+56]
            tag='edge-'+str(index)
            c.create_line(*[v*z for v in points],fill=AMBER if edge['relation'] in ('卡住','反驳') else '#7897AA',arrow='last',width=2,tags=tag)
            c.create_text(((x1+x2)/2+125)*z,((y1+y2)/2+45)*z,text=edge['relation'],fill=AMBER,font=(FONT,max(8,int(9*z))),tags=tag)
            c.tag_bind(tag,'<Button-1>',lambda e,item=edge:self.inspect_edge(item))
        for node in nodes:
            x,y=positions[node['id']];color=AMBER if node['kind']=='blocker' else (GREEN if node['role']=='我方' or node['kind']=='resolution' else '#6B9AC4')
            dashed=node['basis']!='stated'
            tag='node-'+node['id']
            c.create_rectangle(x*z,y*z,(x+250)*z,(y+120)*z,fill='#17303B',outline=color,width=2,dash=(5,3) if dashed else (),tags=tag)
            badge=KINDS.get(node['kind'],'原话')+' · '+node['role']+(' · 待核实' if node['basis']=='uncertain' else '')
            if z>=.6:c.create_text((x+12)*z,(y+16)*z,anchor='w',text=badge,fill=color,font=(FONT,max(8,int(9*z))),tags=tag)
            c.create_text((x+12)*z,(y+(34 if z>=.6 else 20))*z,anchor='nw',text=preview(node['title'],28 if z>=.6 else 16,3 if z>=.6 else 1),fill=TEXT,font=(FONT,max(7,int(11*z)),'bold'),tags=tag)
            ids=', '.join('#'+str(r['turn_id']) for r in node['evidence'])
            if z>=.6:c.create_text((x+12)*z,(y+106)*z,anchor='w',text='依据 '+ids[:35],fill=MUTED,font=(FONT,max(7,int(8*z))),tags=tag)
            c.tag_bind(tag,'<Button-1>',lambda e,item=node:self.inspect_node(item))
        c.configure(scrollregion=(0,0,1240*z,max(240,(bands[-1][2]+60) if bands else 300)*z))
        if not nodes:c.create_text(600*z,170*z,text='等待已确认语音。开始监听后，图会增量更新。',fill=MUTED,font=(FONT,12))

    def _inspect(self,text):
        self.inspector.configure(state='normal');self.inspector.delete('1.0','end');self.inspector.insert('1.0',text);self.inspector.configure(state='disabled')

    def evidence_text(self,refs):
        turns={t['turn_id']:t for t in self.doc.snapshot()['turns']};parts=[]
        for ref in refs:
            t=turns.get(ref['turn_id'],{})
            parts.append(f"#{ref['turn_id']}  {t.get('start_s',0):.1f}–{t.get('end_s',0):.1f}s\n{t.get('speaker_id','unknown')} / {t.get('role','未绑定')}\n摘录：{ref['quote']}\n原文：{t.get('text','')}\n")
        return '\n'.join(parts)

    def inspect_node(self,node):
        self.selected=node['id']
        basis={'stated':'基于原话的分析，请核对原文','inferred':'意图/关系推测，不能当作事实','uncertain':'待核实，不作为已达成结果'}[node['basis']]
        self._inspect(f"{node['title']}\n\n{node['text']}\n\n{basis}\n角色：{node['role']}\n议题：{node['topic']}\n\n"+self.evidence_text(node['evidence']))

    def inspect_edge(self,edge):
        self.selected=None
        self._inspect('关系：'+edge['relation']+'（AI 对原话关系的归纳）\n\n'+self.evidence_text(edge['evidence']))
    def scale(self,factor):self.zoom=max(.45,min(1.6,self.zoom*factor));self.render()
    def fit(self):self.zoom=max(.45,min(1.2,self.canvas.winfo_width()/1240));self.render();self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
    def overview(self):
        nodes=display_nodes(self.doc.snapshot())
        if self.topic.get()!='全部议题':nodes=[n for n in nodes if n['topic']==self.topic.get()]
        nodes=nodes[self.page*400:(self.page+1)*400]
        _,bands=layout_nodes(nodes);height=bands[-1][2]+60 if bands else 300
        self.zoom=max(.25,min(1.2,self.canvas.winfo_width()/1240,self.canvas.winfo_height()/height))
        self.render();self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
    def change_page(self,delta):self.page+=delta;self.render();self.canvas.yview_moveto(0)
    def select_topic(self,event):self.page=0;self.render();self.canvas.yview_moveto(0)
    def retry(self):
        if not self.historical:self.panel.enable_logic_analysis();self.panel.logic_service.notify()
    def current(self):
        if self.panel.logic_document:self.open(self.panel.logic_document)
    def history(self):
        path=filedialog.askopenfilename(parent=self.window,initialdir=graph_dir(),filetypes=[('Sentinel逻辑图','*.json')])
        if path:
            try:
                self.doc=GraphDocument.load(path);self.historical=True;self.signature=None;self.refresh()
            except Exception as exc:self._inspect('历史图读取失败：'+str(exc))
