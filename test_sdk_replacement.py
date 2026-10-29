import builtins
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from sdk_worker import replacement_runs, validate_output_paths, commit_validated_output, render, validate_interpolated_pixels


class InterpolatedPixelTests(unittest.TestCase):
    @staticmethod
    def packed(rgb, alpha=3):
        rgb=np.asarray(rgb,dtype=np.uint32)
        return rgb[...,0] | (rgb[...,1]<<10) | (rgb[...,2]<<20) | np.uint32(alpha<<30)

    def setUp(self):
        self.left=self.packed([[[30,100,200],[300,400,500]],[[600,700,800],[900,1000,50]]])
        self.right=self.packed([[[90,180,260],[380,480,560]],[[660,780,880],[960,1020,110]]])

    def test_either_endpoint_copy_is_rejected_and_identifies_output_frame(self):
        for name,endpoint,frame in (('left',self.left,5),('right',self.right,13)):
            with self.subTest(endpoint=name),self.assertRaisesRegex(RuntimeError,rf'^{frame}프레임 보간 결과가 참조 프레임과 같습니다'):
                validate_interpolated_pixels(endpoint.copy(),self.left,self.right,frame,np)

    def test_alpha_only_changes_cannot_disguise_an_endpoint_copy(self):
        for name,endpoint in (('left',self.left),('right',self.right)):
            for alpha in (0,1,2):
                copied=(endpoint & np.uint32(0x3fffffff)) | np.uint32(alpha<<30)
                self.assertFalse(np.array_equal(copied,endpoint))
                with self.subTest(endpoint=name,alpha=alpha),self.assertRaisesRegex(RuntimeError,'^57프레임'):
                    validate_interpolated_pixels(copied,self.left,self.right,57,np)

    def test_distinct_interpolation_is_accepted_without_a_motion_threshold(self):
        midpoint=self.packed([[[60,140,230],[340,440,530]],[[630,740,840],[930,1010,80]]])
        almost_left=self.left.copy();almost_left[0,0]^=np.uint32(1)
        for pixels in (midpoint,almost_left):
            with self.subTest(pixels=pixels.tolist()):
                self.assertIsNone(validate_interpolated_pixels(pixels,self.left,self.right,5,np))

    def test_identical_rgb_anchors_allow_still_images_despite_alpha_differences(self):
        for alpha in (0,3):
            same_rgb=(self.left & np.uint32(0x3fffffff)) | np.uint32(alpha<<30)
            with self.subTest(alpha=alpha):
                self.assertIsNone(validate_interpolated_pixels(self.left.copy(),self.left,same_rgb,5,np))
                self.assertIsNone(validate_interpolated_pixels(same_rgb.copy(),self.left,same_rgb,5,np))


class ReplacementWriterTests(unittest.TestCase):
    def test_single_adjacent_and_shared_original_anchor(self):
        self.assertEqual(replacement_runs([dict(start=3,end=4,generated=1)],8),[(3,4,1)])
        self.assertEqual(replacement_runs([dict(start=3,end=5,generated=2)],8),[(3,5,2)])
        runs=[dict(start=3,end=4,generated=1),dict(start=5,end=6,generated=1)]
        self.assertEqual(replacement_runs(runs,8),[(3,4,1),(5,6,1)])

    def test_rejects_insert_delete_missing_anchor_overlap_or_fractional_index(self):
        for runs in (
            [dict(start=3,end=3,generated=1)],
            [dict(start=3,end=4,generated=2)],
            [dict(start=3,end=4,generated=0)],
            [dict(start=0,end=1,generated=1)],
            [dict(start=6,end=8,generated=2)],
            [dict(start=3,end=5,generated=2),dict(start=5,end=6,generated=1)],
            [dict(start=3.5,end=5,generated=2)],
            [],
        ):
            with self.subTest(runs=runs),self.assertRaises(ValueError):
                replacement_runs(runs,8)

    def test_long_hold_keeps_every_source_slot(self):
        self.assertEqual(replacement_runs([dict(start=2,end=152,generated=150)],154),[(2,152,150)])


class OutputCommitTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.source=root/'Source.mov';self.output=root/'Output.mov';self.temp=root/'.render.partial.mov'
        self.source.write_bytes(b'original source')
        self.output.write_bytes(b'old output')
        self.temp.write_bytes(b'validated new output')

    def test_existing_output_is_replaced_without_predeletion(self):
        real_replace=os.replace
        def replace_with_old_file_still_present(temp,output):
            self.assertEqual(Path(output).read_bytes(),b'old output')
            self.assertEqual(Path(temp).read_bytes(),b'validated new output')
            real_replace(temp,output)
        with mock.patch('sdk_worker.os.replace',side_effect=replace_with_old_file_still_present) as replace:
            commit_validated_output(self.temp,self.source,self.output,lambda:False)
        replace.assert_called_once_with(self.temp,self.output)
        self.assertEqual(self.output.read_bytes(),b'validated new output')
        self.assertEqual(self.source.read_bytes(),b'original source')
        self.assertFalse(self.temp.exists())

    def test_new_destination_is_published(self):
        output=self.output.with_name('New.mov')
        self.assertEqual(validate_output_paths(self.source,output),(self.source,output))
        commit_validated_output(self.temp,self.source,output,lambda:False)
        self.assertEqual(output.read_bytes(),b'validated new output')
        self.assertEqual(self.output.read_bytes(),b'old output')

    def test_replace_failure_preserves_old_output_and_source(self):
        with mock.patch('sdk_worker.os.replace',side_effect=PermissionError('destination is busy')):
            with self.assertRaises(PermissionError):
                commit_validated_output(self.temp,self.source,self.output,lambda:False)
        self.assertEqual(self.output.read_bytes(),b'old output')
        self.assertEqual(self.source.read_bytes(),b'original source')
        self.assertEqual(self.temp.read_bytes(),b'validated new output')

    def test_cancellation_before_or_after_final_validation_preserves_output(self):
        for decisions in ([True],[False,True]):
            with self.subTest(decisions=decisions):
                cancelled=mock.Mock(side_effect=decisions)
                with mock.patch('sdk_worker.os.replace') as replace:
                    with self.assertRaises(InterruptedError):
                        commit_validated_output(self.temp,self.source,self.output,cancelled)
                replace.assert_not_called()
                self.assertEqual(self.output.read_bytes(),b'old output')
                self.assertEqual(self.temp.read_bytes(),b'validated new output')

    def test_original_path_cannot_be_destination_or_temporary_file(self):
        with self.assertRaises(ValueError):validate_output_paths(self.source,self.source)
        with self.assertRaises(ValueError):
            commit_validated_output(self.temp,self.source,self.source,lambda:False)
        with self.assertRaises(ValueError):
            commit_validated_output(self.source,self.source,self.output,lambda:False)
        self.assertEqual(self.source.read_bytes(),b'original source')
        self.assertEqual(self.output.read_bytes(),b'old output')

    def test_original_hardlink_is_rejected_in_initial_and_final_checks(self):
        alias=self.source.with_name('Alias.mov');os.link(self.source,alias)
        with self.assertRaises(ValueError):validate_output_paths(self.source,alias)
        with self.assertRaises(ValueError):
            commit_validated_output(self.temp,self.source,alias,lambda:False)
        self.assertTrue(self.source.samefile(alias))
        self.assertEqual(self.source.read_bytes(),b'original source')

    def test_destination_changed_to_source_alias_is_rechecked_at_commit(self):
        validate_output_paths(self.source,self.output)
        self.output.unlink();os.link(self.source,self.output)
        with self.assertRaises(ValueError):
            commit_validated_output(self.temp,self.source,self.output,lambda:False)
        self.assertTrue(self.source.samefile(self.output))
        self.assertEqual(self.source.read_bytes(),b'original source')
        self.assertEqual(self.temp.read_bytes(),b'validated new output')

    def test_original_symlink_is_rejected(self):
        alias=self.source.with_name('Symlink.mov')
        try:alias.symlink_to(self.source)
        except (OSError,NotImplementedError) as error:self.skipTest(str(error))
        with self.assertRaises(ValueError):validate_output_paths(self.source,alias)
        with self.assertRaises(ValueError):
            commit_validated_output(self.temp,self.source,alias,lambda:False)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.source.read_bytes(),b'original source')

    @unittest.skipUnless(os.name=='nt','Windows path casing')
    def test_original_path_with_different_case_is_rejected(self):
        alias=Path(str(self.source).upper())
        with self.assertRaises(ValueError):validate_output_paths(self.source,alias)

    def test_output_directory_is_rejected_and_preserved(self):
        directory=self.output.with_name('OutputFolder');directory.mkdir()
        marker=directory/'keep.txt';marker.write_text('keep',encoding='utf-8')
        with self.assertRaises(ValueError):validate_output_paths(self.source,directory)
        with self.assertRaises(ValueError):
            commit_validated_output(self.temp,self.source,directory,lambda:False)
        self.assertEqual(marker.read_text(encoding='utf-8'),'keep')
        self.assertTrue(self.temp.exists())

    def test_input_alias_error_does_not_import_gpu_dependencies(self):
        real_import=builtins.__import__
        def forbid_gpu(name,*args,**kwargs):
            if name in ('numpy','cupy','nvvfx'):self.fail('Dependencies loaded before path validation')
            return real_import(name,*args,**kwargs)
        with mock.patch('builtins.__import__',side_effect=forbid_gpu):
            with self.assertRaises(ValueError):
                render(dict(source=str(self.source),output=str(self.source)))


if __name__=='__main__':unittest.main()
