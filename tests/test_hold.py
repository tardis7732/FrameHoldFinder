import threading
import unittest
import hold_core as core


class RunTests(unittest.TestCase):
    def test_single_pair_and_multiple_runs(self):
        result=core.runs([.1,0,.1,.1,.1,0,.1])
        self.assertEqual([(r['start'],r['end'],r['copies']) for r in result],[(2,3,1),(6,7,1)])
    def test_long_and_trailing(self):
        result=core.runs([0,0,.2,0,0])
        self.assertEqual([(r['start'],r['end'],r['copies']) for r in result],[(1,3,2),(4,6,2)])
    def test_no_repeats(self):
        self.assertEqual(core.runs([.1,.2,.3]),[])
        self.assertEqual(core.runs([]),[])
    def test_tolerance_and_finite(self):
        self.assertEqual(len(core.runs([.001,.1],.002)),1)
        with self.assertRaises(ValueError):core.runs([],float('nan'))
        with self.assertRaises(ValueError):core.runs([float('inf')])
    def test_one_frame(self):
        self.assertEqual(core.runs([]),[])


if __name__=='__main__':unittest.main()
