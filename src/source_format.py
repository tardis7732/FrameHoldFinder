"""FFmpeg source-format planning, independent of GUI and NVIDIA imports.

Matching encoding properties is not lossless or bitstream-identical rendering.
The VFG image path uses RGB10A2; encoded 12-bit tags do not imply 12-bit VFG.
"""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
COLOR_OPTIONS = {'color_range':'-color_range','color_space':'-colorspace',
                 'color_transfer':'-color_trc','color_primaries':'-color_primaries',
                 'chroma_location':'-chroma_sample_location'}
UNKNOWN = (None, '', 'unknown', 'unspecified', 'reserved', 'N/A')
PRORES_PROFILES = {'Proxy':'0','LT':'1','Standard':'2','HQ':'3','4444':'4','XQ':'5','4444 XQ':'5'}
H264_PROFILES = {'Constrained Baseline':'baseline','Baseline':'baseline','Main':'main','High':'high',
                 'High 10':'high10','High 4:2:2':'high422','High 4:4:4 Predictive':'high444'}
HEVC_PROFILES = {'Main':'main','Main 10':'main10'}


def probe_format(ffprobe, path, *, count_frames=False):
    args=[str(ffprobe),'-v','error','-show_streams','-show_format','-of','json']
    if count_frames:args += ['-count_frames']
    result=subprocess.run(args+[str(path)],capture_output=True,creationflags=FLAGS,check=True)
    return json.loads(result.stdout)


def video_stream(info):
    streams=[s for s in info.get('streams',[]) if s.get('codec_type')=='video']
    if len(streams)!=1:raise ValueError('Source format requires exactly one video stream.')
    return streams[0]


def _container(info, path):
    name=info.get('format',{}).get('format_name','').split(',')
    suffix=Path(path).suffix.lower()
    if 'mov' in name and suffix in ('.mov','.mp4','.m4v'):
        return 'mov' if suffix=='.mov' else 'mp4'
    if 'matroska' in name and suffix=='.mkv':return 'matroska'
    raise ValueError('Source container is not supported for matching export. Use MOV, MP4 or MKV, or explicitly select ProRes MOV.')


def _sar(value):
    try:
        result=Fraction(str(value).replace(':','/'))
        return result if result>0 else None
    except (ValueError,ZeroDivisionError):return None


def plan_format(info, source, output, mode='source'):
    """Return output arguments and expectations; never silently change a format."""
    if mode not in ('source','prores'):raise ValueError('Format must be source or prores.')
    original=video_stream(info)
    pix=original.get('pix_fmt','')
    if pix.startswith(('yuva','rgba','argb','bgra','abgr','gbrap','ya')):
        raise ValueError('Alpha-channel video is not supported by this interpolation path.')
    if original.get('field_order') not in (None,'unknown','progressive'):
        raise ValueError('Interlaced video is not supported. Convert a review copy to progressive video first.')
    rotation=original.get('tags',{}).get('rotate',0)
    for data in original.get('side_data_list',[]):
        rotation=data.get('rotation',rotation)
        if any(word in data.get('side_data_type','').lower() for word in ('mastering','content light','dovi','hdr')):
            raise ValueError('HDR side metadata is not supported by source-format export.')
    if float(rotation or 0)%360:raise ValueError('Rotated video metadata is not supported. Normalize rotation before export.')
    if original.get('color_transfer') in ('smpte2084','arib-std-b67'):
        raise ValueError('HDR PQ/HLG video is not supported by this interpolation path.')
    if abs(float(original.get('start_time',0) or 0))>0.0001:
        raise ValueError('Non-zero video start timestamps are not supported by source-format export.')
    extra=[s for s in info.get('streams',[]) if s.get('codec_type') not in ('video','audio')]
    if extra:raise ValueError('Additional subtitle/data streams are not supported by this export path.')
    if mode=='source':
        container=_container(info,source)
        if Path(output).suffix.lower()!=Path(source).suffix.lower():
            raise ValueError('Source-format output must use the same file extension as the input.')
        expected=dict(original)
    else:
        if Path(output).suffix.lower()!='.mov':raise ValueError('ProRes export requires a .mov output path.')
        container='mov'
        expected=dict(original,codec_name='prores',profile='4444',pix_fmt='yuv444p12le',
                      color_range='tv',color_space='bt709',color_transfer='bt709',color_primaries='bt709')
        expected.pop('chroma_location',None)
        expected.pop('level',None)
        expected['codec_tag_string']='ap4h'
    codec=expected.get('codec_name');profile=expected.get('profile');pix=expected.get('pix_fmt')
    args=[]
    if codec=='prores':
        if container not in ('mov','matroska'):raise ValueError('ProRes requires a MOV or MKV container.')
        if profile not in PRORES_PROFILES:raise ValueError(f'Unsupported ProRes profile: {profile}.')
        if pix not in ('yuv422p10le','yuv444p12le'):
            raise ValueError(f'Unsupported ProRes pixel format: {pix}.')
        # prores_ks encodes 4444 from 10-bit planes; its decoded output is 12-bit.
        encode_pix='yuv444p10le' if pix=='yuv444p12le' else pix
        args=['-c:v','prores_ks','-profile:v',PRORES_PROFILES[profile],'-alpha_bits','0']
    elif codec=='h264':
        if profile not in H264_PROFILES:raise ValueError(f'Unsupported H.264 profile: {profile}.')
        if pix not in ('yuv420p','yuvj420p','yuv422p','yuvj422p','yuv444p','yuvj444p',
                       'yuv420p10le','yuv422p10le','yuv444p10le'):
            raise ValueError(f'Unsupported H.264 pixel format: {pix}.')
        encode_pix=pix
        args=['-c:v','libx264','-preset','medium','-crf','18','-profile:v',H264_PROFILES[profile]]
        if isinstance(expected.get('level'),int) and expected['level']>0:
            args += ['-level:v',f"{expected['level']/10:g}"]
    elif codec=='hevc':
        if profile not in HEVC_PROFILES or pix not in ('yuv420p','yuvj420p','yuv420p10le'):
            raise ValueError(f'Unsupported HEVC profile/pixel format: {profile} / {pix}.')
        encode_pix=pix
        args=['-c:v','libx265','-preset','medium','-crf','18','-profile:v',HEVC_PROFILES[profile]]
        params=['log-level=error']
        if isinstance(expected.get('level'),int) and expected['level']>0:
            params.append(f"level-idc={expected['level']/30:g}")
        args += ['-x265-params',':'.join(params)]
    else:raise ValueError(f'No matching encoder is configured for source codec {codec!r}. Select ProRes MOV explicitly to convert.')
    # Explicit format filter plus +pix_fmt prevents silent encoder fallback.
    out_range=expected.get('color_range')
    if out_range not in ('pc','tv'):out_range='pc' if str(pix).startswith('yuvj') else 'tv'
    matrix=expected.get('color_space')
    matrices={'bt709':'bt709','smpte170m':'smpte170m','bt470bg':'bt470','smpte240m':'smpte240m',
              'bt2020nc':'bt2020'}
    if matrix not in UNKNOWN and matrix not in matrices:
        raise ValueError(f'Unsupported source color matrix: {matrix}.')
    if original.get('color_space') not in (*UNKNOWN,*matrices):
        raise ValueError(f"Unsupported source color matrix: {original.get('color_space')}.")
    # This is the conversion matrix only; unknown source tags stay unspecified.
    conversion=matrices.get(matrix,'bt601')
    filters=[f'scale=in_range=pc:out_range={out_range}:out_color_matrix={conversion}',f'format={encode_pix}']
    # Tag the filtered frames as well as the encoder context. Recent FFmpeg
    # builds propagate AVFrame properties over context-only color options.
    frame_tags=[]
    for key,filter_key in [('color_range','range'),('color_space','colorspace'),
                           ('color_transfer','color_trc'),('color_primaries','color_primaries')]:
        value=expected.get(key)
        if value not in UNKNOWN:frame_tags.append(f'{filter_key}={value}')
    if frame_tags:filters.append('setparams='+':'.join(frame_tags))
    sar=_sar(original.get('sample_aspect_ratio'))
    if sar:filters.append(f'setsar={sar.numerator}/{sar.denominator}')
    args += ['-vf',','.join(filters),'-pix_fmt','+'+encode_pix]
    for key,option in COLOR_OPTIONS.items():
        value=expected.get(key)
        if value not in UNKNOWN:args += [option,str(value)]
    tag=expected.get('codec_tag_string')
    if container in ('mov','mp4') and tag and len(tag)==4 and tag.isalnum():args += ['-tag:v',tag]
    if container in ('mov','mp4'):
        base=Fraction(original.get('time_base','1/1000'))
        if base.numerator==1:args += ['-video_track_timescale',str(base.denominator)]
    args += ['-f',container]
    input_range=original.get('color_range')
    if input_range not in ('pc','tv'):input_range='pc' if original.get('pix_fmt','').startswith('yuvj') else 'tv'
    input_matrix=matrices.get(original.get('color_space'),'bt601')
    decode_filter=f'scale=in_range={input_range}:out_range=pc:in_color_matrix={input_matrix},format=rgb48le'
    return dict(mode=mode,container=container,video=expected,audio=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio'],
                decode_filter=decode_filter,
                encoder_args=args,extension=Path(output).suffix.lower(),precision='RGB10A2 (10-bit RGB)')


def verify_format(plan, info, frames, fps_fraction):
    """Validate the file before atomic publication, including copied audio."""
    actual=video_stream(info);expected=plan['video']
    count=actual.get('nb_read_frames') or actual.get('nb_frames')
    if int(count or 0)!=frames:raise RuntimeError('Saved frame count differs from the source.')
    if Fraction(actual['avg_frame_rate'])!=Fraction(fps_fraction):raise RuntimeError('Saved frame rate differs from the source.')
    for key in ('codec_name','profile','pix_fmt','width','height',*COLOR_OPTIONS):
        value=expected.get(key)
        if value in UNKNOWN:continue
        if actual.get(key)!=value:raise RuntimeError(f'Source-format verification failed: {key}: {value!r} → {actual.get(key)!r}.')
    if isinstance(expected.get('level'),int) and expected['level']>0 and actual.get('level')!=expected['level']:
        raise RuntimeError('Saved codec level differs from the source.')
    names=info.get('format',{}).get('format_name','').split(',')
    if ('matroska' if plan['container']=='matroska' else 'mov') not in names:
        raise RuntimeError('Saved container differs from the source.')
    if plan['container'] in ('mov','mp4'):
        tag=expected.get('codec_tag_string')
        if tag and len(tag)==4 and tag.isalnum() and actual.get('codec_tag_string')!=tag:
            raise RuntimeError('Saved codec tag differs from the source.')
        if Fraction(expected['time_base'])!=Fraction(actual['time_base']):
            raise RuntimeError('Saved video time base differs from the source.')
    sar=_sar(expected.get('sample_aspect_ratio'))
    if sar and _sar(actual.get('sample_aspect_ratio'))!=sar:raise RuntimeError('Saved sample aspect ratio differs from the source.')
    after=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    if len(plan['audio'])!=len(after):raise RuntimeError('Saved audio stream count differs from the source.')
    for before,result in zip(plan['audio'],after):
        for key in ('codec_name','sample_rate','channels','channel_layout'):
            if before.get(key) not in UNKNOWN and before.get(key)!=result.get(key):
                raise RuntimeError(f'Copied audio differs from the source: {key}.')
        for key in ('start_time','duration'):
            if before.get(key) not in UNKNOWN and result.get(key) not in UNKNOWN:
                tolerance=max(0.001,1/float(before.get('sample_rate') or 48000))
                if not math.isclose(float(before[key]),float(result[key]),abs_tol=tolerance,rel_tol=0):
                    raise RuntimeError(f'Copied audio timing differs from the source: {key}.')
    return dict(container=plan['container'],codec=actual['codec_name'],profile=actual.get('profile'),
                pixel_format=actual['pix_fmt'],width=actual['width'],height=actual['height'],
                fps_fraction=str(Fraction(actual['avg_frame_rate'])),audio_streams=len(after))
