"""One downward, branching graph; selection reveals original audio-time evidence."""
import unicodedata
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog
from sentinel_ui import button
from sentinel_theme import BG,TEXT,MUTED,SUCCESS,WARNING,FONT,CARD,BORDER,ACCENT_SOFT,ACCENT
from sentinel_scroll import attach_scrollbars
from sentinel_logic import GraphDocument,KINDS,graph_dir

from sentinel_graph_layout import layout_graph, NODE_WIDTH, NODE_HEIGHT, ROW_STEP

MIN_ZOOM = .8
MAX_ZOOM = 1.6


def preview(text, width=28, lines=3):
    rows=[''];n=0
    for ch in text.replace('\n',' '):
        w=2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if n+w>width:
            if len(rows)==lines:return '\n'.join(rows)[:-1]+'…'
            rows.append('');n=0
        rows[-1]+=ch;n+=w
    return '\n'.join(rows)


def fit_preview(text, measure, width, lines=1):
    """Fit glyphs at their actual rendered size, including the ellipsis."""
    remaining=' '.join(text.split());rows=[]
    for index in range(lines):
        if measure(remaining)<=width:
            rows.append(remaining);break
        suffix='…' if index==lines-1 else ''
        low,high=0,len(remaining)
        while low<high:
            middle=(low+high+1)//2
            if measure(remaining[:middle]+suffix)<=width:low=middle
            else:high=middle-1
        rows.append(remaining[:low]+suffix)
        remaining=remaining[low:].lstrip()
    return '\n'.join(rows)


from sentinel_graph_layout import display_nodes


def layout_nodes(nodes, edges=()):
    """Compatibility entry point: geometry follows dependencies, never roles."""
    result=layout_graph(nodes, edges)
    return result.positions,[('全局推进',70,result.height-36)]


class LogicGraphWindow:
    def __init__(self,panel):
        self.panel=panel;self.window=None;self.doc=None;self.historical=False
        self.zoom=1.;self.signature=None;self.selected=None
        self.base_status='';self._positions={};self._snapshot=None

    def open(self,doc):
        if self.doc is not doc:
            self._positions={};self.selected=None;self._snapshot=None
        self.doc=doc;self.historical=False;self.signature=None
        if self.window is None or not self.window.winfo_exists():self._build()
        self.window.deiconify();self.window.lift();self.refresh()

    def _build(self):
        w=self.window=self.panel.open_page('全局逻辑图', scroll=False, immersive=True)
        self.dpi=w.winfo_fpixels('1i')/96
        self.unit=lambda value:round(value*self.dpi)
        bar=tk.Frame(w,bg=BG)
        bar.pack(fill='x',pady=(0,self.unit(8)))
        button(bar,'‹ 提醒',self.window.withdraw).pack(side='left',padx=(0,8))
        self.status=tk.StringVar()
        status=tk.Label(bar,textvariable=self.status,bg=BG,fg=MUTED,font=(FONT,8),cursor='hand2')
        status.pack(side='left',padx=12)
        status.bind('<Button-1>',lambda event:self.show_info())
        button(bar,'更多 ···',self.more).pack(side='right')
        button(bar,'依据',self.toggle_inspector).pack(side='right',padx=4)
        button(bar,'总览',self.fit).pack(side='right')
        self.retry_button=button(bar,'刷新',self.retry)
        self.retry_button.pack(side='right',padx=4)
        self.situation=tk.StringVar()
        self.viewport=tk.Frame(w,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        self.viewport.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(self.viewport,bg=CARD,highlightthickness=0,bd=0,takefocus=True)
        self.canvas.pack(fill='both',expand=True)
        self.scrollbars=attach_scrollbars(self.canvas,horizontal=True,wheel=True)
        self.canvas.bind('<ButtonPress-2>',lambda e:self.canvas.scan_mark(e.x,e.y))
        self.canvas.bind('<B2-Motion>',lambda e:self.canvas.scan_dragto(e.x,e.y,gain=1))
        self.canvas.bind('<ButtonPress-1>',self.pan_start)
        self.canvas.bind('<B1-Motion>',self.pan_move)
        self.canvas.bind('<ButtonRelease-1>',lambda e:setattr(self,'_panning',False))
        self._zoom_delta=0
        self.canvas.bind('<Control-MouseWheel>',self.zoom_wheel)
        self.canvas.bind('<Left>',lambda e:self.canvas.xview_scroll(-1,'units'))
        self.canvas.bind('<Right>',lambda e:self.canvas.xview_scroll(1,'units'))
        self.canvas.bind('<Up>',lambda e:self.canvas.yview_scroll(-1,'units'))
        self.canvas.bind('<Down>',lambda e:self.canvas.yview_scroll(1,'units'))
        self.canvas.bind('<Configure>',self.canvas_resized)
        zoom=tk.Frame(self.viewport,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        zoom.place(x=self.unit(14),rely=1,y=-self.unit(14),anchor='sw')
        button(zoom,'−',lambda:self.scale(.8)).pack(side='left')
        self.zoom_text=tk.StringVar(value='100%')
        tk.Label(zoom,textvariable=self.zoom_text,bg=CARD,fg=MUTED,font=(FONT,8),width=5).pack(side='left')
        button(zoom,'＋',lambda:self.scale(1.25)).pack(side='left')
        self.drawer=tk.Frame(self.viewport,bg=CARD,padx=self.unit(16),pady=self.unit(12),
                             highlightbackground=BORDER,highlightthickness=1)
        heading=tk.Frame(self.drawer,bg=CARD);heading.pack(fill='x',pady=(0,12))
        tk.Label(heading,text='原话与依据',bg=CARD,fg=TEXT,font=(FONT,10,'bold')).pack(side='left')
        button(heading,'×',self.hide_inspector).pack(side='right')
        self.inspector=tk.Text(self.drawer,width=24,wrap='word',font=(FONT,10),bg=CARD,fg=TEXT,
                               bd=0,padx=0,pady=0,state='disabled')
        self.inspector.pack(fill='both',expand=True)
        attach_scrollbars(self.inspector)
        self._inspect('点击节点或连线，查看原话与音频时间。')
        self._fit_on_resize=True
        self._panning=False

    def show_inspector(self):
        width=min(self.unit(300),max(self.unit(230),round(self.viewport.winfo_width()*.32)))
        self.drawer.place(relx=1,rely=0,anchor='ne',width=width,relheight=1)
        self.drawer.lift()

    def hide_inspector(self):
        self.drawer.place_forget()
        self.canvas.focus_set()

    def toggle_inspector(self):
        if self.drawer.winfo_manager():self.hide_inspector()
        else:self.show_inspector()

    def more(self):
        menu=tk.Menu(self.window,tearoff=False,font=(FONT,9))
        for title,command in (('回到本场',self.current),('打开历史图',self.history),
                              ('总览',self.fit),('操作与图例',self.show_info)):
            menu.add_command(label=title,command=command)
        try:menu.tk_popup(self.window.winfo_pointerx(),self.window.winfo_pointery())
        finally:menu.grab_release();menu.destroy()

    def show_info(self):
        self._inspect(self.base_status+'\n\n本场目标（未必已说出口）：'+self.doc.snapshot()['goal']+'\n\n'+self.situation.get()+
                      '\n\n拖动画布空白处浏览；滚轮上下，Shift+滚轮左右，Ctrl+滚轮缩放。'+
                      '\n\n从上往下看推进，横向看并行事项。\n实线：有原话依据\n虚线：下一步建议 / 推测 / 待核实\n橙色：当前卡点'+
                      '\n回指关系沿节点侧边绘制，不作为已完成步骤。\n字号保持可读，内容较多时平移浏览。'+
                      '\n\nAI 归纳需核对原话。私人笔记不参与图分析。')
        self.show_inspector()

    def pan_start(self,event):
        current=self.canvas.gettags('current')
        self._panning=not any(tag.startswith(('node-','edge-')) for tag in current)
        self.canvas.focus_set()
        if self._panning:self.canvas.scan_mark(event.x,event.y)

    def pan_move(self,event):
        if self._panning:self.canvas.scan_dragto(event.x,event.y,gain=1)

    def zoom_wheel(self,event):
        self._zoom_delta+=event.delta
        steps=int(self._zoom_delta/120)
        self._zoom_delta-=steps*120
        if steps:self.scale(1.15**steps)
        return 'break'

    def canvas_resized(self,event):
        if self._fit_on_resize and self.doc is not None:self.fit(reset=False)
        if not getattr(self,'node_map',{}):self.render()

    def refresh(self):
        if not self.window or not self.window.winfo_exists() or self.window.state()=='withdrawn' or self.doc is None:return
        # The panel polls at 80ms. Do not copy the complete conversation on
        # every tick when only the status text (or nothing) has changed.
        with self.doc.lock:
            signature=(id(self.doc),self.doc.revision)
            status,count=self.doc.status,len(self.doc.turns)
        self.base_status=('历史图 · ' if self.historical else '本场 · ')+status+f"  |  已确认 {count} 段"
        self.status.set(('历史图' if self.historical else '本场')+f' · {count} 段')
        self.retry_button.configure(state='disabled' if self.historical else 'normal')
        if signature!=self.signature:
            snap=self._snapshot=self.doc.snapshot()
            self.signature=signature
            resolved={e['source'] for e in snap['edges'] if e['relation']=='解决'
                      and any(n['id']==e['target'] and n['kind']=='resolution' for n in snap['nodes'])}
            blockers=[n['title'] for n in snap['nodes'] if n['kind']=='blocker' and n['id'] not in resolved]
            progress=[n['title'] for n in snap['nodes'] if n['kind'] in ('progress','resolution')]
            self.situation.set('图中卡点（AI归纳）｜'+('；'.join(blockers[-3:]) or '尚无已归纳的卡点')+
                               '     进展｜'+('；'.join(progress[-2:]) or '等待明确进展'))
            self._resolved=resolved
            self.render()
            if self.selected:
                current=self.node_map.get(self.selected)
                if current:self.inspect_node(current,reveal=False)
                else:
                    self.selected=None
                    self._inspect('原文或归属已更新，所选旧判断已失效。请点击当前图中的节点重新查看依据。')
            else:
                self._inspect('图已更新。点击节点或箭头查看最新原话、身份归属与音频时间。')

    def render(self):
        if self.doc is None:return
        snap=self._snapshot or self.doc.snapshot();nodes=display_nodes(snap)
        self.zoom_text.set(f'{round(self.zoom*100)}%')
        self.node_map={n['id']:n for n in nodes}
        geometry=layout_graph(nodes,snap['edges'],self._positions)
        positions=self._positions=geometry.positions
        c=self.canvas;left=max(0,c.canvasx(0));top=max(0,c.canvasy(0));c.delete('all')
        # Tk point fonts already follow system DPI. Only geometry also needs dpi.
        z=self.zoom*self.dpi
        font=lambda size,bold=False:(FONT,max(8,round(size*self.zoom)),*(['bold'] if bold else []))
        measured_fonts={}
        def bounded(text, font_spec, lines=1):
            if font_spec not in measured_fonts:
                measured_fonts[font_spec]=tkfont.Font(root=c,font=font_spec)
            return fit_preview(text,measured_fonts[font_spec].measure,(NODE_WIDTH-24)*z,lines)
        if not nodes:
            width,height=max(1,c.winfo_width()),max(1,c.winfo_height())
            c.configure(scrollregion=(0,0,width,height))
            c.create_text(width/2,height/2-12,text='把注意力留给对话',fill=TEXT,font=(FONT,13))
            c.create_text(width/2,height/2+24,text='确认的原话会先出现，再逐步归纳为分支和推进关系',fill=MUTED,font=(FONT,9))
            return
        c.create_text(30*z,16*z,anchor='nw',text='本场目标 · 用户设定，不代表已达成',fill=MUTED,font=font(8))
        c.create_text(30*z,36*z,anchor='nw',text=preview(snap['goal'] or '尚未设置目标',76,1),fill=TEXT,font=font(10,True))
        side_routes=False
        for index,edge in enumerate(snap['edges']):
            if edge['source'] not in positions or edge['target'] not in positions:continue
            x1,y1=positions[edge['source']];x2,y2=positions[edge['target']]
            cyclic=(edge['source'],edge['target']) in geometry.cyclic_edges
            if cyclic or y2-y1>ROW_STEP:
                # Rebuttals may point back. Route outside the cards rather than
                # inventing a forward dependency or dropping the original link.
                side_routes=True;side=geometry.width-12+(index%3)*10
                exit_y=y1+NODE_HEIGHT+16;enter_y=y2-16
                points=[x1+NODE_WIDTH/2,y1+NODE_HEIGHT,x1+NODE_WIDTH/2,exit_y,
                        side,exit_y,side,enter_y,x2+NODE_WIDTH/2,enter_y,x2+NODE_WIDTH/2,y2]
                label_x,label_y=side+6,(exit_y+enter_y)/2
            else:
                a=x1+NODE_WIDTH/2;b=x2+NODE_WIDTH/2
                mid=(y1+NODE_HEIGHT+y2)/2
                points=[a,y1+NODE_HEIGHT,a,mid,b,mid,b,y2]
                label_x,label_y=(a+b)/2,mid-9
            tag='edge-'+str(index)
            color=WARNING if edge['relation'] in ('卡住','反驳') else '#AAB6BE'
            inferred=any(self.node_map[ident]['basis']!='stated' for ident in (edge['source'],edge['target']))
            c.create_line(*[v*z for v in points],fill=color,arrow='last',width=max(1,z),
                          arrowshape=(7*z,9*z,3*z),dash=(4,4) if inferred else (),tags=tag)
            c.create_text(label_x*z,label_y*z,text=edge['relation'],fill=MUTED,font=font(8),tags=tag)
            c.tag_bind(tag,'<Button-1>',lambda e,item=edge:self.inspect_edge(item))
        for node in nodes:
            x,y=positions[node['id']]
            blocked=node['kind']=='blocker' and node['id'] not in getattr(self,'_resolved',set())
            color=WARNING if blocked else (SUCCESS if node['kind'] in ('progress','resolution') else MUTED)
            dashed=node['basis']!='stated';action=node['kind']=='action'
            tag='node-'+node['id']
            r=8;right=x+NODE_WIDTH;bottom=y+NODE_HEIGHT
            points=[x+r,y,right-r,y,right,y,right,y+r,right,bottom-r,
                    right,bottom,right-r,bottom,x+r,bottom,x,bottom,x,bottom-r,x,y+r,x,y]
            c.create_polygon(*[v*z for v in points],smooth=True,splinesteps=16,
                             fill=ACCENT_SOFT if blocked else CARD,
                             outline=ACCENT if self.selected==node['id'] else (color if dashed else BORDER),width=1,
                             dash=(4,3) if dashed else (),tags=tag)
            role={'我方':'我','对方':'对方','未绑定':'身份未确认'}.get(node['role'],node['role'])
            badge='下一步建议 · 尚未推进' if action else KINDS.get(node['kind'],'原话')+' · '+role
            if node['basis']=='uncertain':badge+=' · 待核实'
            c.create_text((x+12)*z,(y+14)*z,anchor='w',text=bounded(badge,font(8)),fill=color,font=font(8),tags=tag)
            title=c.create_text((x+12)*z,(y+28)*z,anchor='nw',text=bounded(node['title'],font(10,True),2),fill=TEXT,font=font(10,True),tags=tag)
            # Core summaries stay visible; duplicate text and evidence IDs do
            # not consume a second row. Exact quotes remain in the inspector.
            if node['text'].strip()!=node['title'].strip():
                title_bottom=c.bbox(title)[3]/z
                body_y=max(y+52,title_bottom+3)
                body=c.create_text((x+12)*z,body_y*z,anchor='nw',text=bounded(node['text'],font(9)),fill=MUTED,font=font(9),tags=tag)
                # Unusual system fonts can be taller. Keep the body inside the
                # card instead of letting it collide with the next graph row.
                if c.bbox(body)[3]>(bottom-8)*z:
                    c.itemconfigure(title,text=bounded(node['title'],font(10,True)))
                    c.coords(body,(x+12)*z,(y+52)*z)
            c.tag_bind(tag,'<Button-1>',lambda e,item=node:self.inspect_node(item))
        width=(geometry.width+(48 if side_routes else 0))*z;height=geometry.height*z
        c.configure(scrollregion=(0,0,max(c.winfo_width(),width),max(c.winfo_height(),height)))
        c.xview_moveto(left/max(c.winfo_width(),width));c.yview_moveto(top/max(c.winfo_height(),height))

    def _inspect(self,text):
        self.inspector.configure(state='normal');self.inspector.delete('1.0','end');self.inspector.insert('1.0',text);self.inspector.configure(state='disabled')

    def evidence_text(self,refs):
        turns={t['turn_id']:t for t in self.doc.snapshot()['turns']};parts=[]
        for ref in refs:
            t=turns.get(ref['turn_id'],{})
            parts.append(f"#{ref['turn_id']}  {t.get('start_s',0):.1f}–{t.get('end_s',0):.1f}s\n{t.get('speaker_id','unknown')} / {t.get('role','未绑定')}\n摘录：{ref['quote']}\n原文：{t.get('text','')}\n")
        return '\n'.join(parts)

    def inspect_node(self,node,*,reveal=True):
        self.selected=node['id']
        if reveal:self.show_inspector()
        basis={'stated':'基于原话的分析，请核对原文','inferred':'意图/关系推测，不能当作事实','uncertain':'待核实，不作为已达成结果'}[node['basis']]
        if node['kind']=='action':basis='下一步建议，尚未推进；不是已说出的承诺或已达成的事实'
        self._inspect(f"{node['title']}\n\n{node['text']}\n\n{basis}\n角色：{node['role']}\n议题：{node['topic']}\n\n"+self.evidence_text(node['evidence']))

    def inspect_edge(self,edge):
        self.selected=None
        self.show_inspector()
        self._inspect('关系：'+edge['relation']+'（AI 对原话关系的归纳）\n\n'+self.evidence_text(edge['evidence']))
    def scale(self,factor):
        self._fit_on_resize=False
        self.zoom=max(MIN_ZOOM,min(MAX_ZOOM,self.zoom*factor));self.render()

    def fit(self,*,reset=True):
        self._fit_on_resize=True
        if self.doc is None:return
        snap=self._snapshot or self.doc.snapshot()
        geometry=layout_graph(display_nodes(snap),snap['edges'],self._positions)
        side_routes=bool(geometry.cyclic_edges) or any(
            e['source'] in geometry.positions and e['target'] in geometry.positions and
            geometry.positions[e['target']][1]-geometry.positions[e['source']][1]>ROW_STEP
            for e in snap['edges'])
        width=geometry.width+(48 if side_routes else 0)
        # Sparse maps must not balloon to fill the window. Fit their structure
        # when readable; long maps use scrolling instead of tiny text.
        self.zoom=max(MIN_ZOOM,min(1.,
                      (self.canvas.winfo_width()-self.unit(16))/(width*self.dpi),
                      (self.canvas.winfo_height()-self.unit(12))/(geometry.height*self.dpi)))
        self.render()
        if reset:self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)

    def overview(self):
        self.fit()

    def retry(self):
        if not self.historical:
            self.panel.enable_logic_analysis()
            if self.panel.logic_service:self.panel.logic_service.notify()
    def current(self):
        if self.panel.logic_document:self.open(self.panel.logic_document)
    def history(self):
        path=filedialog.askopenfilename(parent=self.panel.root,initialdir=graph_dir(),filetypes=[('Sentinel逻辑图','*.json')])
        if path:
            try:
                self.doc=GraphDocument.load(path);self.historical=True;self.signature=None
                self._positions={};self._snapshot=None;self.selected=None;self._fit_on_resize=True
                self.refresh()
            except Exception as exc:
                self._inspect('历史图读取失败：'+str(exc))
                self.show_inspector()
