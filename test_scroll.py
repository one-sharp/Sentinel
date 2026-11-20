"""Black-box navigation checks for the shared overlay scroll thumbs."""
import gc
import tkinter as tk
import unittest

from sentinel_scroll import attach_scrollbars


class ScrollTests(unittest.TestCase):
    def setUp(self):
        gc.collect(); gc.disable()
        self.root = tk.Tk()
        self.root.geometry('400x300')

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect(); gc.enable()

    def test_text_thumb_scrolls_then_disappears_when_content_fits(self):
        text = tk.Text(self.root, wrap='word')
        text.pack(fill='both', expand=True)
        bar, = attach_scrollbars(text)
        text.insert('end', '\n'.join(f'Line {i}' for i in range(200)))
        self.root.update()
        self.assertTrue(bar.winfo_ismapped())
        self.assertLess(bar.winfo_width(), 20)
        # Click the lower track, then drag its thumb back toward the start.
        bar.event_generate('<ButtonPress-1>', x=3, y=bar.winfo_height()-10)
        # Continue the same gesture before Tk's idle scroll callback fires.
        bar.event_generate('<B1-Motion>', x=3, y=bar.winfo_height()-8)
        bar.event_generate('<ButtonRelease-1>', x=3, y=bar.winfo_height()-10)
        self.root.update()
        self.assertGreater(text.yview()[0], .7)
        _, _, start, _ = bar.metrics()
        bar.event_generate('<ButtonPress-1>', x=3, y=round(start+5))
        bar.event_generate('<B1-Motion>', x=3, y=3)
        bar.event_generate('<ButtonRelease-1>', x=3, y=3)
        self.root.update()
        self.assertLess(text.yview()[0], .1)
        text.delete('1.0', 'end'); text.insert('end', 'One line')
        self.root.update()
        self.assertFalse(bar.winfo_ismapped())

    def test_wheel_over_canvas_content_accumulates_small_trackpad_deltas(self):
        canvas = tk.Canvas(self.root)
        canvas.pack(fill='both', expand=True)
        attach_scrollbars(canvas, wheel=True)
        content = tk.Frame(canvas)
        canvas.create_window(0, 0, window=content, anchor='nw')
        for i in range(50):
            tk.Label(content, text=f'Knowledge pack {i}').pack()
        self.root.update()
        canvas.configure(scrollregion=canvas.bbox('all'))
        self.root.update()
        label = content.winfo_children()[0]
        for _ in range(3):
            label.event_generate('<MouseWheel>', delta=-40)
        self.root.update()
        self.assertGreater(canvas.yview()[0], 0)
        canvas.yview_moveto(0)
        from tkinter import ttk
        choice = ttk.Combobox(content, values=('one', 'two'))
        choice.pack()
        self.root.update()
        choice.event_generate('<MouseWheel>', delta=-120)
        self.root.update()
        self.assertEqual(canvas.yview()[0], 0)

    def test_canvas_both_axes_remain_reachable(self):
        canvas = tk.Canvas(self.root, scrollregion=(0,0,4000,3000))
        canvas.pack(fill='both', expand=True)
        vertical, horizontal = attach_scrollbars(canvas, horizontal=True)
        self.root.update()
        self.assertTrue(vertical.winfo_ismapped())
        self.assertTrue(horizontal.winfo_ismapped())
        horizontal.event_generate('<ButtonPress-1>', x=horizontal.winfo_width()-10, y=3)
        vertical.event_generate('<ButtonPress-1>', x=3, y=vertical.winfo_height()-10)
        self.root.update()
        self.assertGreater(canvas.xview()[0], .7)
        self.assertGreater(canvas.yview()[0], .7)
