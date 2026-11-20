"""Small overlay scroll thumbs shared by Canvas and Text surfaces.

No platform scrollbar chrome or global mouse bindings. The content owns its
scroll position; each thumb reflects the same first/last fractions Tk reports.
"""
import tkinter as tk

from sentinel_theme import BORDER, MUTED


class OverlayScrollbar(tk.Canvas):
    def __init__(self, parent, *, command, orient='vertical'):
        self.vertical = orient == 'vertical'
        self.command = command
        self.scale = parent.winfo_fpixels('1i')/96
        self.thickness = max(6, round(7*self.scale))
        super().__init__(parent, bg=parent.cget('background'), bd=0,
                         highlightthickness=0, width=self.thickness, height=self.thickness,
                         cursor='arrow', takefocus=False)
        self.first, self.last = 0.0, 1.0
        self.hover = False
        self.origin = None
        self.bind('<Configure>', self.draw)
        self.bind('<Enter>', lambda event:self.set_hover(True))
        self.bind('<Leave>', lambda event:self.set_hover(False))
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<B1-Motion>', self.drag)
        self.bind('<ButtonRelease-1>', lambda event:setattr(self, 'origin', None))

    def set_hover(self, hover):
        self.hover = hover
        self.draw()

    def set(self, first, last):
        self.first, self.last = max(0.0, float(first)), min(1.0, float(last))
        if self.first <= 0 and self.last >= 1:
            self.place_forget()
            return
        if self.vertical:
            self.place(relx=1, rely=0, anchor='ne', width=self.thickness,
                       relheight=1, height=-self.thickness)
        else:
            self.place(relx=0, rely=1, anchor='sw', height=self.thickness,
                       relwidth=1, width=-self.thickness)
        tk.Misc.lift(self)
        self.draw()

    def metrics(self):
        length = max(1, (self.winfo_height() if self.vertical else self.winfo_width())-4)
        span = min(1.0, max(0.0, self.last-self.first))
        thumb = min(length, max(round(24*self.scale), length*span))
        travel = length-thumb
        start = 2+(travel*self.first/(1-span) if span < 1 else 0)
        return span, travel, start, thumb

    def draw(self, event=None):
        self.delete('all')
        _, _, start, thumb = self.metrics()
        center = self.thickness/2
        width = max(3, round((5 if self.hover else 3)*self.scale))
        points = (center, start+width/2, center, max(start+width/2, start+thumb-width/2))
        if not self.vertical:
            points = (points[1], points[0], points[3], points[2])
        self.create_line(*points, fill=MUTED if self.hover else BORDER,
                         width=width, capstyle='round')

    def press(self, event):
        point = event.y if self.vertical else event.x
        span, travel, start, thumb = self.metrics()
        origin = self.first
        if travel and not start <= point <= start+thumb:
            origin = max(0, min(1-span, (point-2-thumb/2)/travel*(1-span)))
            self.command('moveto', origin)
        self.origin = (point, origin)

    def drag(self, event):
        if self.origin is None:
            return
        point = event.y if self.vertical else event.x
        span, travel, _, _ = self.metrics()
        if travel:
            self.command('moveto', max(0, min(1-span, self.origin[1]+(point-self.origin[0])/travel*(1-span))))


class CanvasWheel:
    """Route wheel input within this canvas, including its embedded controls.

    A root-local binding sees events from labels inside create_window. Routing
    chooses the nearest canvas and leaves Text and Combobox behavior intact.
    """
    def __init__(self, canvas, horizontal):
        self.canvas, self.horizontal = canvas, horizontal
        self.remainder = {'x': 0, 'y': 0}
        canvas._scroll_wheel = self
        self.root = canvas.winfo_toplevel()
        self.binding = self.root.bind('<MouseWheel>', self.route, add='+')
        canvas.bind('<Destroy>', self.close, add='+')

    def route(self, event):
        target = event.widget
        while target is not None:
            if isinstance(target, tk.Text) or target.winfo_class() in ('TCombobox', 'Spinbox', 'TSpinbox', 'Listbox'):
                return
            owner = getattr(target, '_scroll_wheel', None)
            if owner is not None:
                if owner is not self:
                    return
                break
            target = getattr(target, 'master', None)
        if target is None or event.state & 4:  # Ctrl is reserved for graph zoom.
            return
        axis = 'x' if self.horizontal and event.state & 1 else 'y'
        self.remainder[axis] += event.delta
        steps = int(self.remainder[axis]/120)
        self.remainder[axis] -= steps*120
        if steps:
            (self.canvas.xview_scroll if axis == 'x' else self.canvas.yview_scroll)(-steps, 'units')
        return 'break'

    def close(self, event):
        if event.widget is self.canvas and self.binding:
            try:
                self.root.unbind('<MouseWheel>', self.binding)
            except tk.TclError:
                pass
            self.binding = None


def attach_scrollbars(widget, *, horizontal=False, wheel=False):
    """Overlay thumbs without reserving content width; native Text wheel stays."""
    vertical = OverlayScrollbar(widget, command=widget.yview)
    widget.configure(yscrollcommand=vertical.set)
    bars = [vertical]
    if horizontal:
        bar = OverlayScrollbar(widget, command=widget.xview, orient='horizontal')
        widget.configure(xscrollcommand=bar.set)
        bars.append(bar)
    if wheel:
        CanvasWheel(widget, horizontal)
    return bars
