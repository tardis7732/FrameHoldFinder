import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np
import cli


def fixture(source, speeds):
    points = np.array([(gx*64+8+k*4, gy*60+8+k*4) for gy in range(6) for gx in range(10) for k in range(4)], np.float32)
    steps = []
    for speed in speeds:
        vectors = np.zeros_like(points); vectors[:, 0] = speed
        steps.append(dict(ids=np.arange(len(points)), p=points.copy(), v=vectors, speed=np.linalg.norm(vectors, axis=1)))
    stat = source.stat()
    return dict(path=str(source), frames=len(speeds)+1, steps=steps, analysis_width=640, analysis_height=360,
        source_stat=dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        metadata=dict(width=640, height=360, fps=24., fps_fraction='24/1', estimated=len(speeds)+1, transfer='bt709'))


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder/'source 영상.mov'; self.source.write_bytes(b'untouched source')
        binary = mock.patch.object(cli.core, 'binary', return_value='ffprobe')
        binary.start(); self.addCleanup(binary.stop)

    def invoke(self, arguments, analysis=None):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), \
             mock.patch.object(cli.motion, 'scan', return_value=analysis) as scan:
            code = cli.main(arguments)
        return code, json.loads(output.getvalue()), error.getvalue(), scan

    def test_invalid_numeric_options_fail_before_scanning(self):
        for option, value in (('--threshold','nan'),('--threshold','-1'),('--hold-quiet','inf'),
                              ('--min-move','0'),('--support-percent','101'),('--support-percent','0'),
                              ('--min-points','1.5'),('--min-points','0'),('--width','-1')):
            with self.subTest(option=option,value=value):
                code, result, _, scan = self.invoke(['analyze',str(self.source),option,value])
                self.assertEqual(code,2); self.assertEqual(result['status'],'error'); scan.assert_not_called()

    def test_manual_syntax_and_overlap_rejected_before_scan(self):
        for args in (['--add','3-6'],['--add','3:4'],['--add','0:4'],
                     ['--add','3:6','--add','5:9'],['--manual-only'],['--exclude','jump:3:9']):
            with self.subTest(args=args):
                code, _, _, scan = self.invoke(['analyze',str(self.source),*args])
                self.assertNotEqual(code,0); scan.assert_not_called()

    def test_source_and_report_aliases_fail_before_scan_or_sdk(self):
        for extra in (['--report',str(self.source)],
                      ['--report',str(self.folder/'output.mov')]):
            with self.subTest(extra=extra), mock.patch.object(cli.sdk_bridge,'repair') as repair:
                code, result, _, scan = self.invoke(['render',str(self.source),'-o',str(self.folder/'output.mov'),*extra])
                self.assertEqual(code,1); self.assertIn('report',result['error']); scan.assert_not_called(); repair.assert_not_called()
        code, _, _, scan = self.invoke(['render',str(self.source),'-o',str(self.source)])
        self.assertEqual(code,1); scan.assert_not_called()

    def test_hardlink_alias_rejected(self):
        alias=self.folder/'alias.mov'
        try: os.link(self.source,alias)
        except OSError as error: self.skipTest(str(error))
        code, _, _, scan=self.invoke(['render',str(self.source),'-o',str(alias)])
        self.assertEqual(code,1); scan.assert_not_called(); self.assertEqual(self.source.read_bytes(),b'untouched source')

    def test_output_extension_matches_selected_mode_before_scan(self):
        for mode, suffix in (('source','.mp4'),('prores','.mkv')):
            code, result, _, scan=self.invoke(['render',str(self.source),'-o',str(self.folder/f'out{suffix}'),'--format',mode])
            self.assertEqual(code,1); self.assertIn('extension',result['error']); scan.assert_not_called()

    def test_defaults_match_desktop_and_emit_valid_json(self):
        analysis=fixture(self.source,[10,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--quiet'],analysis)
        self.assertEqual(code,0); self.assertEqual(result['replacement_frames'],1)
        options=result['analysis_options']
        self.assertEqual((options['threshold'],options['hold_quiet'],options['support_percent'],options['min_points']),(5.,.5,30.,20))
        self.assertEqual(options['min_move'],3.2)
        self.assertEqual(result['candidates'][0]['key'],'jump:3:4')
        self.assertEqual(result['repair_runs'][0]['targets'],[4]); self.assertEqual(result['frame_count_change'],0)

    def test_manual_protects_its_anchors_from_automatic_replacements(self):
        analysis=fixture(self.source,[10,30,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--add','3:6'],analysis)
        self.assertEqual(code,0)
        rows={r['key']:r for r in result['candidates']}
        self.assertTrue(rows['jump:2:3']['blocked_by_manual'])
        self.assertFalse(rows['jump:2:3']['included'])
        self.assertTrue(rows['jump:4:5']['blocked_by_manual'])
        self.assertEqual(result['repair_runs'][0]['targets'],[4,5])
        self.assertEqual(rows['replace:3:6']['repair_anchors'],[3,6])

    def test_excluding_manual_range_restores_automatic_candidate(self):
        analysis=fixture(self.source,[10,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--add','3:6','--exclude','replace:3:6'],analysis)
        self.assertEqual(code,0)
        self.assertEqual(result['repair_runs'][0]['targets'],[4])
        self.assertEqual(result['repair_runs'][0]['source_keys'],['jump:3:4'])

    def test_excluded_jump_is_not_rendered(self):
        analysis=fixture(self.source,[10,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--exclude','jump:3:4'],analysis)
        self.assertEqual(code,0); self.assertEqual(result['repair_runs'],[])
        self.assertFalse(result['candidates'][0]['included'])

    def test_flags_disable_automatic_candidates_but_keep_additions(self):
        analysis=fixture(self.source,[10,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--no-holds','--no-jumps','--add','2:5'],analysis)
        self.assertEqual(code,0); self.assertEqual([r['kind'] for r in result['candidates']],['replace'])

    def test_out_of_bounds_range_fails_before_sdk(self):
        analysis=fixture(self.source,[10]*5)
        code,result,_,_=self.invoke(['analyze',str(self.source),'--add','3:9'],analysis)
        self.assertEqual(code,1); self.assertIn('within the source',result['error'])

    def test_report_matches_stdout_and_source_is_untouched(self):
        report=self.folder/'report.json'
        analysis=fixture(self.source,[10,10,30,10,10,10])
        code,result,_,_=self.invoke(['analyze',str(self.source),'--report',str(report)],analysis)
        self.assertEqual(code,0); self.assertEqual(json.loads(report.read_text(encoding='utf-8')),result)
        self.assertEqual(self.source.read_bytes(),b'untouched source')

    def test_manual_only_uses_actual_count_not_estimate_and_skips_tracking(self):
        metadata=fixture(self.source,[10]*11)['metadata']; metadata['estimated']=999
        args=cli.parser().parse_args(['analyze',str(self.source),'--manual-only','--add','3:6','--add','6:9'])
        counted=subprocess.CompletedProcess([],0,stdout=b'{"streams":[{"nb_read_frames":"12"}]}',stderr=b'')
        with mock.patch.object(cli.core,'probe',return_value=metadata), mock.patch.object(cli.core,'binary',return_value='ffprobe'), \
             mock.patch.object(cli.subprocess,'run',return_value=counted), mock.patch.object(cli.motion,'scan') as scan:
            result=cli.execute(args,threading.Event())
        scan.assert_not_called(); self.assertEqual(result['frames'],12)
        self.assertEqual([r['targets'] for r in result['repair_runs']],[[4,5],[7,8]])

    def test_render_passes_source_format_and_quality_to_sdk(self):
        args=cli.parser().parse_args(['render',str(self.source),'-o',str(self.folder/'out.mov'),'--quality','MEDIUM'])
        analysis=fixture(self.source,[10,10,30,10,10,10])
        with mock.patch.object(cli.motion,'scan',return_value=analysis), \
             mock.patch.object(cli.source_format,'probe_format',return_value={}), \
             mock.patch.object(cli.source_format,'plan_format'), \
             mock.patch.object(cli.sdk_bridge,'repair',return_value={'frames':7,'delta':0}) as repair:
            result=cli.execute(args,threading.Event())
        self.assertEqual(result['status'],'rendered'); self.assertEqual(repair.call_args.kwargs['format_mode'],'source')
        self.assertEqual(repair.call_args.args[3],'MEDIUM'); self.assertEqual(repair.call_args.args[1][0]['targets'],[4])

    def test_dry_run_validates_format_and_never_calls_sdk(self):
        args=cli.parser().parse_args(['render',str(self.source),'-o',str(self.folder/'out.mov'),'--dry-run'])
        with mock.patch.object(cli.motion,'scan',return_value=fixture(self.source,[10]*6)), \
             mock.patch.object(cli.source_format,'probe_format',return_value={}), \
             mock.patch.object(cli.source_format,'plan_format') as fmt, mock.patch.object(cli.sdk_bridge,'repair') as repair:
            result=cli.execute(args,threading.Event())
        fmt.assert_called_once(); repair.assert_not_called(); self.assertEqual(result['status'],'dry_run')

    def test_unsupported_format_fails_before_tracking(self):
        args=cli.parser().parse_args(['render',str(self.source),'-o',str(self.folder/'out.mov')])
        with mock.patch.object(cli.source_format,'probe_format',return_value={}), \
             mock.patch.object(cli.source_format,'plan_format',side_effect=ValueError('Unsupported format')), \
             mock.patch.object(cli.motion,'scan') as scan, mock.patch.object(cli.sdk_bridge,'repair') as repair:
            with self.assertRaisesRegex(ValueError,'Unsupported format'):cli.execute(args,threading.Event())
        scan.assert_not_called(); repair.assert_not_called()

    def test_sdk_path_saved_only_after_successful_check(self):
        args=cli.parser().parse_args(['sdk-check','--python',str(self.folder/'python.exe'),'--save'])
        with mock.patch.object(cli.sdk_bridge,'check',side_effect=RuntimeError('bad environment')), mock.patch.object(cli.sdk_bridge,'save_runtime') as save:
            with self.assertRaises(RuntimeError):cli.execute(args,threading.Event())
            save.assert_not_called()
        with mock.patch.object(cli.sdk_bridge,'check',return_value={'event':'ready'}), mock.patch.object(cli.sdk_bridge,'save_runtime') as save:
            result=cli.execute(args,threading.Event())
        self.assertTrue(result['saved']); save.assert_called_once()

    def test_import_does_not_load_tk_or_gui(self):
        process=subprocess.run([sys.executable,'-c',
            "import cli,sys; assert 'tkinter' not in sys.modules; assert 'motion_app' not in sys.modules; print(cli.VERSION)"],
            cwd=Path(cli.__file__).parent,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr); self.assertEqual(process.stdout.strip(),'1.0')


if __name__=='__main__':
    unittest.main()
