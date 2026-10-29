import copy
import unittest
from source_format import plan_format, verify_format


def fixture(codec='prores',profile='HQ',pix='yuv422p10le'):
    return dict(format=dict(format_name='mov,mp4,m4a,3gp,3g2,mj2'),streams=[dict(
        codec_type='video',codec_name=codec,profile=profile,pix_fmt=pix,width=640,height=360,
        avg_frame_rate='30000/1001',time_base='1/30000',nb_frames='12',sample_aspect_ratio='1:1',
        color_range='tv',field_order='progressive',start_time='0.000000')])


class SourceFormatTests(unittest.TestCase):
    def test_prores_hq_stays_422_10bit(self):
        info=fixture();plan=plan_format(info,'in.mov','out.mov')
        self.assertEqual(plan['mode'],'source')
        self.assertEqual(plan['video']['pix_fmt'],'yuv422p10le')
        self.assertIn('+yuv422p10le',plan['encoder_args'])
        self.assertEqual(verify_format(plan,info,12,'30000/1001')['profile'],'HQ')

    def test_prores_4444_explicit_encoder_plane_format(self):
        info=fixture(profile='4444',pix='yuv444p12le')
        plan=plan_format(info,'in.mov','out.mov')
        self.assertIn('+yuv444p10le',plan['encoder_args'])
        self.assertEqual(plan['video']['pix_fmt'],'yuv444p12le')
        self.assertEqual(plan['precision'],'RGB10A2 (10-bit RGB)')

    def test_h264_hevc_and_matroska(self):
        for codec,profile,pix in [('h264','High','yuv420p'),('hevc','Main 10','yuv420p10le')]:
            info=fixture(codec,profile,pix)
            plan=plan_format(info,'in.mp4','out.mp4')
            self.assertIn('libx264' if codec=='h264' else 'libx265',plan['encoder_args'])
            self.assertEqual(verify_format(plan,info,12,'30000/1001')['codec'],codec)
        info['format']['format_name']='matroska,webm'
        self.assertEqual(plan_format(info,'in.mkv','out.mkv')['container'],'matroska')

    def test_explicit_prores_conversion(self):
        plan=plan_format(fixture('h264','High','yuv420p'),'in.mp4','out.mov','prores')
        self.assertEqual(plan['video']['profile'],'4444')
        self.assertEqual(plan['video']['color_space'],'bt709')

    def test_no_silent_codec_container_or_profile_fallback(self):
        cases=[(fixture('vp9','Profile 0','yuv420p'),'in.mp4','out.mp4'),
               (fixture(),'in.mov','out.mp4'),(fixture(),'in.avi','out.avi'),
               (fixture('hevc','Rext','yuv444p12le'),'in.mp4','out.mp4')]
        for args in cases:
            with self.subTest(args=args),self.assertRaises(ValueError):plan_format(*args)

    def test_reject_alpha_interlace_hdr_rotation_nonzero_start(self):
        values=[('pix_fmt','yuva444p12le'),('field_order','tt'),('color_transfer','smpte2084'),
                ('side_data_list',[dict(side_data_type='Display Matrix',rotation=90)]),
                ('side_data_list',[dict(side_data_type='Mastering display metadata')]),('start_time','1.0')]
        for key,value in values:
            info=fixture();info['streams'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):plan_format(info,'in.mov','out.mov')

    def test_reject_extra_streams(self):
        for stream in [dict(codec_type='video'),dict(codec_type='subtitle')]:
            info=fixture();info['streams'].append(stream)
            with self.assertRaises(ValueError):plan_format(info,'in.mov','out.mov')

    def test_known_colors_sar_and_range_are_preserved(self):
        info=fixture();info['streams'][0].update(color_space='bt709',color_transfer='bt709',
            color_primaries='bt709',sample_aspect_ratio='4:3')
        plan=plan_format(info,'in.mov','out.mov')
        self.assertIn('setsar=4/3',','.join(plan['encoder_args']))
        self.assertIn('in_color_matrix=bt709',plan['decode_filter'])
        for key,value in [('color_space','smpte170m'),('color_range','pc'),('sample_aspect_ratio','1:1')]:
            changed=copy.deepcopy(info);changed['streams'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):verify_format(plan,changed,12,'30000/1001')

    def test_frame_count_fps_profile_and_pixel_fallback_rejected(self):
        info=fixture();plan=plan_format(info,'in.mov','out.mov')
        for key,value in [('nb_frames','11'),('avg_frame_rate','30/1'),('profile','4444'),('pix_fmt','yuv420p')]:
            changed=copy.deepcopy(info);changed['streams'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):verify_format(plan,changed,12,'30000/1001')

    def test_codec_level_tag_timebase_and_container_are_verified(self):
        info=fixture('h264','High','yuv420p');info['streams'][0].update(level=31,codec_tag_string='avc1')
        plan=plan_format(info,'in.mp4','out.mp4')
        for key,value in [('level',30),('codec_tag_string','avc3'),('time_base','1/24000')]:
            changed=copy.deepcopy(info);changed['streams'][0][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):verify_format(plan,changed,12,'30000/1001')
        changed=copy.deepcopy(info);changed['format']['format_name']='matroska,webm'
        with self.assertRaises(RuntimeError):verify_format(plan,changed,12,'30000/1001')

    def test_audio_properties_and_timing_verified(self):
        info=fixture();info['streams'].append(dict(codec_type='audio',codec_name='pcm_s16le',sample_rate='48000',
            channels=2,channel_layout='stereo',duration='0.4004',start_time='0.000000'))
        plan=plan_format(info,'in.mov','out.mov')
        self.assertEqual(verify_format(plan,info,12,'30000/1001')['audio_streams'],1)
        for key,value in [('codec_name','aac'),('channels',1),('start_time','0.1'),('duration','0.5')]:
            changed=copy.deepcopy(info);changed['streams'][1][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):verify_format(plan,changed,12,'30000/1001')


if __name__=='__main__':unittest.main()
