"""Reference confidence visualization must keep constraints off the score axis."""
import tkinter as tk
import unittest

from reference_confidence import ConfidenceGraph


class ConfidenceGraphTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(self.root.destroy)
        self.root.geometry('450x250')
        self.root.attributes('-alpha', 0)
        self.sought = []
        self.graph = ConfidenceGraph(self.root, on_seek=self.sought.append)
        self.graph.pack(fill='both', expand=True)

    def test_manual_and_missing_break_trace_and_cursor_updates_independently(self):
        graph = self.graph
        graph.set_rows([{'confidence': .5}, {'confidence': .9, 'review': True},
                        {'confidence': .99, 'manual': True}, {'confidence': .75},
                        {'confidence': None, 'status': 'missing'}, {'confidence': None, 'status': 'missing'},
                        {'confidence': None}, {'confidence': .6}])
        self.root.update()
        canvas = graph.canvas
        traces = canvas.find_withtag('confidence_trace')
        self.assertEqual(len(traces), 3)
        self.assertTrue(all(canvas.type(item) == 'line' for item in traces))
        first, isolated, last = [canvas.coords(item) for item in traces]
        self.assertGreater(first[-2], first[0])
        self.assertLess(first[-1], first[1])  # Higher confidence is drawn higher.
        self.assertEqual(isolated[1], isolated[-1])
        self.assertEqual(last[1], last[-1])
        self.assertLess(first[-2], isolated[0])
        self.assertLess(isolated[-2], last[0])
        bottom = graph._bounds()[3]
        for tag in ('manual', 'no_score'):
            self.assertEqual(len(canvas.find_withtag(tag)), 1)
            self.assertGreater(min(canvas.coords(tag)[1::2]), bottom)
        missing = canvas.find_withtag('missing_frame')
        self.assertEqual(len(missing), 2)
        self.assertEqual(len(canvas.find_withtag('missing_band')), 2)
        left, top, right, _ = graph._bounds()
        for frame, item in zip((5, 6), missing):
            x, y = canvas.coords(item)[:2]
            self.assertGreater(y, bottom)
            self.assertAlmostEqual(x, left + graph._view.position(frame) * (right-left), delta=.5)
            canvas.event_generate('<Button-1>', x=round(x), y=round(y))
            self.assertIn(str(frame), str(graph._value['text']))
            self.assertTrue(any(label in graph._value['text'] for label in ('누락 후보', 'Missing')))
        issues = canvas.find_withtag('issue')  # Image mismatch can require review even at a high score.
        self.assertEqual(len(issues), 1)
        self.assertEqual(canvas.type(issues[0]), 'oval')
        self.assertEqual([item for item in canvas.find_all() if canvas.type(item) == 'oval'], list(issues))
        self.assertFalse(canvas.find_withtag('stem'))
        graph.set_position(3)
        self.root.update_idletasks()
        self.assertEqual(traces, canvas.find_withtag('confidence_trace'))
        self.assertIn('3', str(graph._value['text']))
        canvas.event_generate('<Button-1>', x=left, y=top+2)
        canvas.event_generate('<B1-Motion>', x=right, y=top+2)
        self.assertEqual(self.sought, [5, 6, 1, 8])

    def test_dense_timeline_bounds_marks_and_pink_bands_and_respects_zoom(self):
        graph = self.graph
        rows = [{'confidence': .9 if n % 2 else .1} for n in range(4096)]
        rows[9] = {'confidence': .9, 'status': 'held'}
        rows[20] = {'confidence': None, 'manual': True}
        rows[-1] = {'confidence': None, 'status': 'missing'}
        graph.set_rows(rows)
        self.root.update()
        self.assertEqual(len(graph.canvas.find_withtag('hold_frame')), 1)
        self.assertEqual(len(graph.canvas.find_withtag('manual')), 1)
        traces = graph.canvas.find_withtag('confidence_trace')
        self.assertEqual(len(traces), 2)
        coords = [graph.canvas.coords(item) for item in traces]
        self.assertLess(sum(len(run) // 2 for run in coords), graph.canvas.winfo_width() * 4)
        self.assertGreater(max(coords[1][1::2]) - min(coords[1][1::2]), 1)
        self.assertEqual(coords[1][::2], sorted(coords[1][::2]))
        nav = graph.navigation
        nav.zoom(30, anchor=1)
        graph.set_position(4096)
        self.root.update_idletasks()
        self.assertTrue(nav.view.zoomed)
        self.assertIn(4096, nav.view.visible_frames)
        self.assertFalse(graph.canvas.find_withtag('hold_frame'))
        self.assertFalse(graph.canvas.find_withtag('manual'))
        self.assertTrue(graph.canvas.find_withtag('missing_frame'))
        self.assertTrue(graph.canvas.find_withtag('playhead'))
        self.assertIn('4096', str(graph._value['text']))
        nav._scroll('moveto', 0)
        self.root.update_idletasks()
        self.assertFalse(graph.canvas.find_withtag('playhead'))
        self.assertFalse(graph.canvas.find_withtag('missing_frame'))
        nav._y_value.set('.2')
        nav._apply_y()
        self.root.update_idletasks()
        self.assertFalse(graph.canvas.find_withtag('guide'))
        self.assertTrue(graph.canvas.find_withtag('clipped'))
        nav.fit()
        self.root.update_idletasks()
        self.assertFalse(nav.view.zoomed)
        self.assertFalse(graph.canvas.find_withtag('guide'))
        self.assertNotIn('0.25', str(graph._legend['text']))
        self.assertTrue(graph.canvas.find_withtag('playhead'))
        graph.set_rows([])
        graph.set_position(0)
        self.root.update_idletasks()
        self.assertFalse(graph.canvas.find_withtag('playhead'))
        self.assertEqual(graph._value['text'], '')

    def test_clicking_a_dense_missing_marker_seeks_a_missing_source(self):
        graph = self.graph
        rows = [{'confidence': .7}] * 4096
        graph.set_rows(rows)
        self.root.update()
        left, top, right, bottom = graph._bounds()
        x = (left + right) // 2
        nominal = graph._view.frame_at((x - left) / (right - left))
        earlier, later = nominal - 3, nominal + 2
        rows[earlier-1] = rows[later-1] = {'status': 'missing', 'confidence': None}
        graph.set_rows(rows)
        self.root.update()
        markers = graph.canvas.find_withtag('missing_frame')
        self.assertEqual(len(markers), 1)
        self.assertEqual(graph.canvas.coords(markers[0])[0], x)
        graph.canvas.event_generate('<Button-1>', x=x, y=bottom+8)
        graph.canvas.event_generate('<Button-1>', x=x-1, y=bottom+10)
        graph.canvas.event_generate('<Button-1>', x=x, y=top+8)
        self.assertEqual(self.sought, [later, earlier, nominal])
        graph._can_seek = lambda: False
        graph.canvas.event_generate('<Button-1>', x=x, y=bottom+8)
        self.assertEqual(self.sought, [later, earlier, nominal])

    def test_fixed_source_costs_preserve_window_while_seeking_and_restore_overview(self):
        graph = self.graph
        canvas = graph.canvas
        rows = [{'confidence': .8} for _ in range(64)]
        graph.set_rows(rows)
        self.root.update()
        self.assertTrue(canvas.find_withtag('confidence_trace'))
        graph.navigation.zoom(4, 18)
        self.root.update_idletasks()
        self.assertTrue(canvas.find_withtag('confidence_trace'))
        self.assertFalse(canvas.find_withtag('stem'))
        previous_view = graph._view.fractions()

        costs = [1.25] * 40
        costs[17:20] = [float('nan'), None, 10.]
        costs[23] = 1.125
        graph.set_candidates(7, costs, 20, automatic_frame=24)
        self.root.update_idletasks()
        self.assertFalse(canvas.find_withtag('guide'))
        self.assertTrue(canvas.find_withtag('auto_frame:24'))
        self.assertEqual(len(canvas.find_withtag('no_score')), 3)
        traces = canvas.find_withtag('cost_trace')
        self.assertEqual(len(traces), 2)
        self.assertFalse(canvas.find_withtag('cost_stem'))
        self.assertEqual(canvas.winfo_rgb(canvas.itemcget('auto_match', 'fill')),
                         canvas.winfo_rgb(canvas.cget('background')))
        left, top, right, _ = graph._bounds()
        def candidate_x(frame):
            return left + graph._candidate_view.position(frame - graph._first_candidate + 1) * (right - left)

        early, late = [canvas.coords(item) for item in traces]
        self.assertAlmostEqual(early[-2], candidate_x(17), delta=1)
        self.assertAlmostEqual(late[0], candidate_x(21), delta=1)
        points = list(zip(late[::2], late[1::2]))
        # Cost 1.125 at edit 24 is the closest match and plots below its neighbours.
        lowest_x, _ = max(points, key=lambda xy: xy[1])
        self.assertAlmostEqual(lowest_x, candidate_x(24), delta=1)
        for frame, event in [(24, '<Button-1>'), (30, '<B1-Motion>')]:
            canvas.event_generate(event, x=round(candidate_x(frame)), y=top+2)
        self.root.update_idletasks()
        self.assertEqual(self.sought[-2:], [24, 30])
        self.assertEqual(canvas.find_withtag('cost_trace'), traces)
        self.assertEqual(graph._first_candidate, 8)
        self.assertIn('1.25', str(graph._value['text']))
        canvas.event_generate('<MouseWheel>', x=100, y=45, delta=120)
        self.assertEqual(previous_view, graph._view.fractions())

        graph.set_position(40)
        self.root.update_idletasks()
        self.assertFalse(canvas.find_withtag('auto_match'))
        self.assertTrue(graph._candidate_visible(40))
        self.assertFalse(graph._candidate_visible(27))
        self.assertIn('40', str(graph._value['text']))
        graph.set_rows(rows)
        self.root.update_idletasks()
        self.assertFalse(canvas.find_withtag('guide'))
        self.assertTrue(canvas.find_withtag('confidence_trace'))
        self.assertFalse(canvas.find_withtag('cost_trace'))
        self.assertEqual(previous_view, graph._view.fractions())

    def test_no_costs_and_one_frame_candidate_window(self):
        graph = self.graph
        graph.set_candidates(2, [], 3)
        self.root.update()
        self.assertFalse(graph.canvas.find_withtag('cost_trace'))
        graph.set_candidates(2, [float('inf'), float('nan'), 10.], 2)
        self.root.update_idletasks()
        self.assertEqual(len(graph.canvas.find_withtag('no_score')), 3)
        self.assertFalse(graph.canvas.find_withtag('cost_grid'))
        self.assertFalse(graph.canvas.find_withtag('cost_trace'))
        graph.set_candidates(2, [0.], 1, automatic_frame=1)
        self.root.update_idletasks()
        self.assertTrue(graph.canvas.find_withtag('auto_frame:1'))
        x1, y1, x2, y2 = graph.canvas.coords('cost_trace')
        self.assertGreater(x2, x1)
        self.assertEqual(y2, y1)
        left, top, right, _ = graph._bounds()
        graph.canvas.event_generate('<Button-1>', x=(left+right)//2, y=top)
        self.assertEqual(self.sought, [1])
