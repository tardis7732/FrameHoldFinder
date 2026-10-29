import math
import random
import unittest

from graph_viewport import FrameViewport


class FrameViewportTests(unittest.TestCase):
    def test_empty_single_and_short_domains(self):
        view=FrameViewport()
        for frames,first,last in ((0,1,1),(1,1,1),(2,2,2),(3,2,3),(5,2,5),(102,2,102)):
            with self.subTest(frames=frames):
                view.reset(frames)
                self.assertEqual((view.first,view.last),(first,last))
                self.assertEqual((view.left,view.right),(first,last))
                self.assertEqual(view.span,last-first)
                self.assertEqual(view.full_span,last-first)
                self.assertFalse(view.zoomed)
                self.assertEqual(view.fractions(),(0.,1.))
                self.assertEqual(view.frame_at(0),first)
                self.assertEqual(view.frame_at(1),last)
                self.assertEqual(list(view.visible_frames),list(range(first,last+1)))
        view.reset(2)
        self.assertFalse(view.zoom(2));self.assertFalse(view.pan(20));self.assertFalse(view.reveal(100))
        self.assertEqual(view.position(2),.5)
        self.assertEqual(view.ticks(8),[2])

    def test_same_domain_preserves_view_and_new_domain_resets(self):
        view=FrameViewport();view.reset(102)
        view.zoom(2);view.pan(7.25)
        before=(view.left,view.right)
        self.assertFalse(view.set_frames(102));self.assertEqual((view.left,view.right),before)
        self.assertTrue(view.set_frames(202));self.assertEqual((view.left,view.right),(2,202))
        self.assertFalse(view.reset(202))
        view.zoom(2)
        self.assertTrue(view.reset(202));self.assertFalse(view.zoomed)
        view.reset(0)
        self.assertFalse(view.set_frames(1));self.assertEqual(view.frames,1)

    def test_zoom_preserves_cursor_frame_fraction_in_both_directions(self):
        view=FrameViewport();view.reset(202)
        anchor=56.;fraction=view.position(anchor)
        self.assertAlmostEqual(fraction,.27)
        self.assertTrue(view.zoom(2,anchor))
        self.assertEqual(view.span,100.)
        self.assertAlmostEqual(view.position(anchor),fraction)
        self.assertTrue(view.zoom(.5,anchor))
        self.assertEqual((view.left,view.right),(2,202))
        self.assertAlmostEqual(view.position(anchor),fraction)
        self.assertFalse(view.zoom(1,anchor))

    def test_zoom_at_edges_clamps_and_full_view_is_recoverable(self):
        view=FrameViewport();view.reset(102)
        view.zoom(2,anchor=2)
        self.assertEqual((view.left,view.right),(2,52))
        view.zoom(2,anchor=52)
        self.assertEqual((view.left,view.right),(27,52))
        view.zoom(.001,anchor=30)
        self.assertEqual((view.left,view.right),(2,102))
        view.zoom(2,anchor=1000)
        self.assertEqual((view.left,view.right),(52,102))

    def test_minimum_span_and_five_integer_points_when_aligned(self):
        view=FrameViewport();view.reset(102)
        self.assertTrue(view.zoom(1e10))
        self.assertEqual((view.left,view.right,view.span),(50,54,4))
        self.assertEqual(list(view.visible_frames),[50,51,52,53,54])
        self.assertFalse(view.zoom(2))
        view.pan(.25)
        self.assertEqual(view.span,4)
        self.assertEqual(list(view.visible_frames),[51,52,53,54])
        view.reset(4)
        self.assertFalse(view.zoom(100));self.assertEqual(view.span,2)
        custom=FrameViewport(min_span=8);custom.reset(102);custom.zoom(1e10)
        self.assertEqual(custom.span,8)

    def test_pan_clamps_without_changing_width(self):
        view=FrameViewport();view.reset(102);view.zoom(5)
        self.assertEqual(view.span,20)
        self.assertTrue(view.pan(500));self.assertEqual((view.left,view.right),(82,102))
        self.assertFalse(view.pan(1))
        self.assertTrue(view.pan(-500));self.assertEqual((view.left,view.right),(2,22))
        self.assertFalse(view.pan(-1));self.assertFalse(view.pan(0))

    def test_scrollbar_moveto_uses_full_domain_fraction(self):
        view=FrameViewport();view.reset(102);view.zoom(5)
        self.assertTrue(view.moveto(.25));self.assertEqual((view.left,view.right),(27,47))
        self.assertEqual(view.fractions(),(.25,.45))
        self.assertTrue(view.moveto(1));self.assertEqual(view.fractions(),(.8,1.))
        self.assertFalse(view.moveto(2))
        self.assertTrue(view.moveto(-3));self.assertEqual(view.fractions(),(0.,.2))
        view.reset(102);self.assertFalse(view.moveto(.5))

    def test_reveal_only_moves_outside_frames_and_uses_directional_margins(self):
        view=FrameViewport();view.reset(202);view.zoom(10)
        self.assertEqual((view.left,view.right),(92,112))
        self.assertFalse(view.reveal(102));self.assertFalse(view.reveal(92));self.assertFalse(view.reveal(112))
        self.assertTrue(view.reveal(150));self.assertEqual((view.left,view.right),(146,166))
        self.assertAlmostEqual(view.position(150),.2)
        self.assertTrue(view.reveal(100));self.assertEqual((view.left,view.right),(84,104))
        self.assertAlmostEqual(view.position(100),.8)
        self.assertTrue(view.reveal(-100));self.assertEqual(view.left,2)
        self.assertTrue(view.reveal(1000));self.assertEqual(view.right,202)

    def test_frame_mapping_stays_inside_fractional_boundaries_and_inverts(self):
        view=FrameViewport();view.reset(42);view.zoom(2,anchor=8);view.pan(.25)
        self.assertEqual((view.left,view.right),(5.25,25.25))
        self.assertEqual((view.frame_at(-1),view.frame_at(0),view.frame_at(1),view.frame_at(2)),(6,6,25,25))
        for frame in view.visible_frames:
            self.assertEqual(view.frame_at(view.position(frame)),frame)
        for fraction in (0,.001,.1,.33,.5,.75,.999,1):
            self.assertIn(view.frame_at(fraction),view.visible_frames)
        self.assertLess(view.position(5),0)
        self.assertGreater(view.position(26),1)

    def test_ticks_are_integer_nice_steps_with_spaced_edges(self):
        view=FrameViewport();view.reset(202)
        self.assertEqual(view.ticks(5),[50,100,150,200])
        view.reset(182)
        self.assertEqual(view.ticks(6),[2,50,100,150])
        self.assertEqual(view.ticks(2),[2,182])
        self.assertEqual(len(view.ticks(1)),1)
        view.zoom(1000);view.pan(.4)
        ticks=view.ticks(20)
        self.assertEqual(ticks,list(view.visible_frames))
        for maximum in range(1,15):
            ticks=view.ticks(maximum)
            self.assertLessEqual(len(ticks),maximum)
            self.assertEqual(ticks,sorted(set(ticks)))
            self.assertTrue(all(isinstance(frame,int) and frame in view.visible_frames for frame in ticks))

    def test_invalid_values_raise_without_changing_the_view(self):
        for value in (0,-1,.5,math.nan,math.inf,True,None,'bad'):
            with self.subTest(min_span=value),self.assertRaises(ValueError):FrameViewport(value)
        view=FrameViewport();view.reset(102);view.zoom(2)
        original=(view.first,view.last,view.left,view.right)
        for value in (math.nan,math.inf,-math.inf,True,None,'bad'):
            for operation in (view.pan,view.moveto,view.reveal,view.position,view.frame_at,view.ticks,view.set_frames):
                with self.subTest(operation=operation.__name__,value=value),self.assertRaises(ValueError):operation(value)
            with self.assertRaises(ValueError):view.zoom(value)
        for value in (0,-1):
            with self.assertRaises(ValueError):view.zoom(value)
        for value in (-1,2.5):
            with self.assertRaises(ValueError):view.reset(value)
        for value in (0,-1,2.5):
            with self.assertRaises(ValueError):view.ticks(value)
        for value in (math.nan,math.inf):
            with self.assertRaises(ValueError):view.zoom(2,anchor=value)
        self.assertEqual((view.first,view.last,view.left,view.right),original)

    def test_navigation_sequences_preserve_domain_and_integer_selection(self):
        randomizer=random.Random(134)
        view=FrameViewport();view.reset(1034)
        for _ in range(400):
            choice=randomizer.randrange(4)
            if choice==0:view.zoom(randomizer.choice((.5,.8,1.2,2,10)),anchor=randomizer.uniform(view.left,view.right))
            elif choice==1:view.pan(randomizer.uniform(-500,500))
            elif choice==2:view.moveto(randomizer.uniform(-.2,1.2))
            else:view.reveal(randomizer.randint(1,1100))
            self.assertGreaterEqual(view.left,view.first)
            self.assertLessEqual(view.right,view.last)
            self.assertGreaterEqual(view.span,4-1e-9)
            start,end=view.fractions()
            self.assertTrue(0<=start<=end<=1)
            self.assertGreater(len(view.visible_frames),0)
            self.assertIn(view.frame_at(randomizer.random()),view.visible_frames)
            self.assertLessEqual(len(view.ticks(8)),8)


if __name__=='__main__':unittest.main()
