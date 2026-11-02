"""Difference uses the two displayed previews, including tentative correspondence pairs."""
import copy
import tkinter as tk
import unittest

from PIL import Image, ImageTk

from i18n import language, set_language
from reference_panel import ReferencePanel, _PreviewStream, _rgb_difference


class DifferencePixels(unittest.TestCase):
    def test_absolute_per_channel_and_gain_saturate_without_altering_inputs(self):
        a = Image.new('RGB', (2,2), (10,2,250))
        b = Image.new('RGB', (2,2), (10,20,0))
        self.assertEqual(_rgb_difference(a, b).getpixel((0,0)), (0,18,250))
        self.assertEqual(_rgb_difference(a, b, 4).getpixel((0,0)), (0,72,255))
        self.assertEqual(a.getpixel((0,0)), (10,2,250))
        self.assertEqual(b.getpixel((0,0)), (10,20,0))

    def test_taller_edit_is_cropped_at_the_center_at_full_width(self):
        source = Image.new('RGB', (8, 6), 'white')
        edit = Image.new('RGB', (16, 16), 'red')
        edit.paste('white', (0, 2, 16, 14))
        self.assertIsNone(_rgb_difference(source, edit).getbbox())

    def test_shorter_edit_shows_differences_only_in_the_center_of_the_source(self):
        source = Image.new('RGB', (8, 10), (60, 20, 30))
        edit = Image.new('RGB', (16, 12), (50, 30, 10))
        diff = _rgb_difference(source, edit, 2)
        self.assertEqual(diff.size, source.size)
        self.assertEqual(diff.getbbox(), (0, 2, 8, 8))
        self.assertEqual(diff.getpixel((0, 2)), (20, 20, 40))
        self.assertEqual(diff.getpixel((7, 7)), (20, 20, 40))

    def test_preview_decoder_converts_sample_aspect_ratio_to_square_pixels(self):
        anamorphic = dict(width=720, height=576, sample_aspect_ratio='16:15')
        square = dict(width=768, height=576)
        self.assertEqual(_PreviewStream('source', anamorphic).size, _PreviewStream('edit', square).size)


class _Stream:
    def __init__(self, factor):
        self.factor = factor
        self.pending = set()

    def frame(self, frame):
        if frame not in self.pending:
            return Image.new('RGB', (8,6), (self.factor*frame,frame,12))

    def stop(self):
        pass


class DifferenceReview(unittest.TestCase):
    def test_manual_and_pending_pairs_in_both_languages(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        self.addCleanup(set_language, language(), None, False)
        set_language('ko', persist=False)
        panel = ReferencePanel(root)
        self.addCleanup(panel.close)
        panel.pack()
        meta = {'width':8, 'height':6, 'fps':24}
        panel.analysis = dict(source_frames=3, change_frames=3, source_meta=meta, change_meta=meta,
            rows=[dict(source_frame=i, change_frame=j, status=s, review=False, confidence=1)
                  for i,j,s in ((1,1,'matched'), (2,1,'missing'), (3,3,'matched'))],
            change_rows=[dict(source_frame=i, change_frame=i, event='normal', review=False, confidence=1)
                         for i in (1,2,3)])
        original_result = copy.deepcopy(panel.analysis)
        panel._streams = {'source':_Stream(20), 'change':_Stream(10)}
        panel._requested = (1,1)
        panel._present_pair()
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (10,1,12))
        self.assertEqual(panel.gain_label['text'], '차이 강도')
        self.assertEqual(panel.gain_box.winfo_manager(), 'grid')
        self.assertTrue(panel.gain_box.instate(['readonly', '!disabled']))
        gain_position = panel.gain_box.grid_info()
        panel.gain_box.current(1)  # Choose 2× with Difference off, then toggle it on.
        panel.gain_box.event_generate('<<ComboboxSelected>>')
        self.assertFalse(panel.difference.get())
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (10,1,12))
        panel.difference_button.invoke()
        self.assertEqual(panel.difference_button.cget('text'), '차이 보기')
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (20,0,0))
        self.assertEqual(panel.gain_box.grid_info(), gain_position)
        panel.gain_box.current(0)
        panel.gain_box.event_generate('<<ComboboxSelected>>')
        self.assertEqual(ImageTk.getimage(panel._photos['source']).getpixel((0,0))[:3], (20,1,12))

        # Scrubbing away while one decoder is pending must never mix unmatched images.
        panel._streams['change'].pending.add(2)
        panel._requested = (3,2)
        panel._present_pair()
        self.assertEqual(panel._displayed, (1,1))
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (10,0,0))
        panel._requested = (2,3)  # editor candidate is not the nearest mapped edit (1)
        panel._present_pair()
        self.assertEqual(panel.headers['change'].get(), '원본 2 ↔ 편집 3')
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (10,1,0))
        panel.difference_gain.set('4×')
        panel._difference_changed()
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (40,4,0))

        set_language('en', root, persist=False)
        panel.refresh_language()
        self.assertEqual(panel.difference_button.cget('text'), 'Difference')
        self.assertEqual(panel.gain_label['text'], 'Difference gain')
        self.assertEqual(panel.headers['change'].get(), 'Source 2 ↔ Edit 3')
        panel.difference_button.invoke()
        self.assertEqual(panel.gain_box.grid_info(), gain_position)
        self.assertEqual(panel.difference_gain.get(), '4×')
        self.assertEqual(ImageTk.getimage(panel._photos['change']).getpixel((0,0))[:3], (30,3,12))
        self.assertEqual(original_result, panel.analysis)
        panel.difference_button.invoke()
        panel.set_inputs(change='new-edit.mp4')
        self.assertIsNone(panel.analysis)
        self.assertNotIn('change', panel._photos)
