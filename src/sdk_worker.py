"""Runs inside the user's NVIDIA VFX Python environment, outside the GUI EXE."""
import argparse
from contextlib import nullcontext
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from source_format import probe_format, probe_json, plan_format, verify_format, video_stream


FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
if sys.stdout is not None and hasattr(sys.stdout,'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def replacement_runs(runs, frames):
    """Only replace interior source frames; never insert, delete, or reuse outputs."""
    if not isinstance(frames,int) or isinstance(frames,bool) or frames<3:
        raise ValueError('교체할 원본 프레임 수가 올바르지 않습니다.')
    valid=[];last_end=0
    for run in sorted(runs,key=lambda item:item['start']):
        left,end,count=(run.get(key) for key in ('start','end','generated'))
        if any(not isinstance(n,int) or isinstance(n,bool) for n in (left,end,count)):
            raise ValueError('교체 구간과 장수는 정수여야 합니다.')
        if not 1<=left<end<frames or left<=last_end:
            raise ValueError('교체 구간은 겹칠 수 없으며 양끝 원본 프레임이 필요합니다.')
        if count!=end-left:
            raise ValueError('싱크 유지 모드에서는 교체할 프레임 수와 생성 장수가 같아야 합니다.')
        valid.append((left,end,count));last_end=end
    if not valid:raise ValueError('양끝 원본 프레임이 있는 교체 구간을 선택하세요.')
    return valid


def validate_interpolated_pixels(pixels, left, right, frame, array_module):
    """Do not publish an endpoint-copy fallback as a generated interior frame."""
    # Compare packed RGB, ignoring the two alpha bits. Exact equality catches
    # SDK bypasses without imposing a motion threshold on valid interpolation.
    pixels=pixels & 0x3fffffff;left=left & 0x3fffffff;right=right & 0x3fffffff
    if (not bool(array_module.array_equal(left,right)) and
            (bool(array_module.array_equal(pixels,left)) or bool(array_module.array_equal(pixels,right)))):
        raise RuntimeError(f'{frame}프레임 보간 결과가 참조 프레임과 같습니다. 반복 프레임 저장을 중단했습니다.')


def validate_output_paths(source, output):
    """Allow replacing an output file, while protecting every input alias."""
    source=Path(source);output=Path(output)
    if not source.is_file():raise ValueError('입력 영상이 없습니다.')
    if output.is_dir():raise ValueError('출력 경로가 폴더입니다. 영상 파일 경로를 선택하세요.')
    if source.resolve()==output.resolve() or (output.exists() and source.samefile(output)):
        raise ValueError('원본 영상에는 저장할 수 없습니다.')
    return source,output


def commit_validated_output(temp, source, output, cancelled, *, other_inputs=()):
    """Publish a fully checked temporary video without deleting the old file."""
    if cancelled():raise InterruptedError('보간이 취소됐습니다.')
    source,output=validate_output_paths(source,output)
    temp=Path(temp)
    if not temp.is_file():raise ValueError('검증된 임시 영상이 없습니다.')
    # The worker creates this file beside the destination. Also protect the
    # source if this helper is accidentally called with an invalid temp path.
    validate_output_paths(source,temp)
    for other in other_inputs:
        validate_output_paths(other, output)
        validate_output_paths(other, temp)
    if temp.resolve()==output.resolve() or (output.exists() and temp.samefile(output)):
        raise ValueError('임시 영상과 최종 출력 경로는 달라야 합니다.')
    if temp.parent.resolve()!=output.parent.resolve():
        raise ValueError('임시 영상은 최종 출력과 같은 폴더에 있어야 합니다.')
    if cancelled():raise InterruptedError('보간이 취소됐습니다.')
    os.replace(temp,output)


def timewarp_frames(read_frame, lookup):
    """Stream an integer, monotone Source → Change lookup with one cached frame."""
    current = None
    decoded = 0
    for wanted in lookup:
        if type(wanted) is not int or wanted < 1 or wanted < decoded:
            raise ValueError('Invalid frame-match lookup.')
        while decoded < wanted:
            current = read_frame()
            if current is None:
                raise ValueError(f'Cannot read mapped Change frame {wanted}.')
            decoded += 1
        yield current


def _frame_overrides(job, frames, field='frame_overrides'):
    items = job.get(field, [])
    if not isinstance(items, list):
        raise ValueError('Frame overrides must be a list.')
    overrides = {}
    for item in items:
        if job.get('lookup') is None or not isinstance(item, dict):
            raise ValueError('Frame overrides require a reference timeline and edit frame numbers.')
        slot, edit = item.get('source_frame'), item.get('change_frame')
        if type(slot) is not int or type(edit) is not int or type(job.get('change_frames')) is not int:
            raise ValueError('Frame override numbers must be integers.')
        if not 1 <= slot <= frames or not 1 <= edit <= job['change_frames'] or slot in overrides:
            raise ValueError('Frame override outside the clip or duplicate source frame.')
        overrides[slot] = edit
    return overrides


def _selected_edit_pixels(binary, source, decode_filter, edits, size, log, processes, cancelled):
    """Read exact edit frames in the order requested by the writer and interpolator."""
    # Limit both raw RAM and the FFmpeg filter expression, including for 4K edits.
    batch_size = max(1, min(64, (96 * 1024 * 1024) // size))
    for start in range(0, len(edits), batch_size):
        batch = edits[start:start + batch_size]
        selected = sorted(set(batch))
        expression = '+'.join(f'eq(n,{frame - 1})' for frame in selected)
        proc = subprocess.Popen([binary, '-v', 'error', '-nostdin', '-noautorotate', '-i', str(source),
                                 '-map', '0:v:0', '-an', '-sn', '-dn', '-fps_mode', 'passthrough',
                                 '-vf', f"select='{expression}',{decode_filter}", '-pix_fmt', 'rgb48le',
                                 '-frames:v', str(len(selected)), '-f', 'rawvideo', 'pipe:1'],
                                stdout=subprocess.PIPE, stderr=log, creationflags=FLAGS)
        processes.append(proc)
        try:
            images = {}
            for frame in selected:
                if cancelled():
                    raise InterruptedError('보간이 취소됐습니다.')
                pixels = proc.stdout.read(size)
                if len(pixels) != size:
                    if cancelled():
                        raise InterruptedError('보간이 취소됐습니다.')
                    raise ValueError(f'Cannot read selected edit frame {frame}.')
                images[frame] = pixels
            if proc.wait():
                raise RuntimeError('Selected edit frames could not be decoded.')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()
            processes.remove(proc)
        for frame in batch:
            yield images[frame]


def packet_times(binary, path, cancelled):
    info = probe_json([binary,'-v','error','-select_streams','v:0','-show_packets',
                       '-show_entries','packet=pts_time','-of','json',str(path)],cancelled)
    return sorted(float(p['pts_time']) for p in info.get('packets',[]) if 'pts_time' in p)


def check_file_stat(path, expected):
    stat = path.stat()
    actual = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    if expected and actual != {key:expected.get(key) for key in actual}:
        raise ValueError('분석 이후 입력 파일이 변경됐습니다. 다시 분석하세요.')
    return actual


def render(job, *, report=emit, cancel_requested=None):
    source,output=validate_output_paths(job['source'],job['output'])
    cancelled = cancel_requested or Path(job['cancel_file']).exists
    if cancelled():
        raise InterruptedError('보간이 취소됐습니다.')
    initial_stat = check_file_stat(source, job.get('source_stat'))
    lookup = job.get('lookup')
    timing_source = source
    other_inputs = ()
    if lookup is not None:
        timing_source, _ = validate_output_paths(job['reference_path'], output)
        other_inputs = (timing_source,)
        timing_stat = check_file_stat(timing_source, job.get('reference_stat'))
    w,h=int(job['width']),int(job['height']);fps=float(job['fps']);frames=int(job['frames'])
    if lookup is not None and (len(lookup) != frames or frames < 1):
        raise ValueError('Frame-match lookup does not cover the reference timeline.')
    valid = [] if lookup is not None and not job['runs'] else replacement_runs(job['runs'],frames)
    overrides = _frame_overrides(job, frames)
    endpoints = _frame_overrides(job, frames, 'range_endpoints')
    output_edits = {**endpoints, **overrides}
    if overrides:
        valid = [(left, end, count) for left, end, count in valid
                 if any(slot not in overrides for slot in range(left + 1, end + 1))]
    # At each slot the output is written first, then any explicit images
    # needed to interpolate the run to its right are read. The normal decoded
    # left/right frames remain available to a neighboring automatic run.
    choices = {(slot, 0): edit for slot, edit in output_edits.items()}
    active = {left for left, _, _ in valid}
    edit_refs = {}
    for run in job['runs']:
        if 'left_change' not in run and 'right_change' not in run:
            continue
        a, b = run.get('left_change'), run.get('right_change')
        if (lookup is None or type(a) is not int or type(b) is not int or type(job.get('change_frames')) is not int
                or not 1 <= a < b <= job['change_frames']):
            raise ValueError('Interpolation references must be two distinct edited frames in forward order.')
        left, right = run['start'], run['end'] + 1
        if left in active:
            edit_refs[left] = (a, b)
            if a != lookup[left-1]:
                choices[(left, 1)] = a
            if b != lookup[right-1]:
                choices[(left, 2)] = b
    source_info=probe_format(job['ffprobe'],source,cancelled=cancelled)
    change_video = video_stream(source_info)
    if (w, h) != (change_video['width'], change_video['height']):
        raise ValueError('Input dimensions changed. Analyze again.')
    timing_video = change_video
    if lookup is not None:
        timing_info = probe_format(job['ffprobe'],timing_source,cancelled=cancelled)
        timing_video = video_stream(timing_info)
        if abs(float(timing_video.get('start_time', 0) or 0)) > .0001:
            raise ValueError('Reference export requires a zero-start CFR video timeline.')
        # Keep edited video properties, take the CFR clock and audio from Source.
        change_video['time_base'] = timing_video['time_base']
        source_info['streams'] = [s for s in source_info['streams'] if s.get('codec_type') != 'audio']
        source_info['streams'].extend(s for s in timing_info['streams'] if s.get('codec_type') == 'audio')
    format_plan=plan_format(source_info,source,output,job.get('format_mode','source'))
    import numpy as np
    times = packet_times(job['ffprobe'], timing_source, cancelled)
    tick = float(Fraction(timing_video['time_base']))
    if len(times)!=frames or (len(times)>1 and not np.allclose(np.diff(times),1/fps,rtol=.005,atol=max(.0001,tick*1.1))):
        raise ValueError('현재 보간 저장은 고정 프레임률(CFR) 영상용입니다. VFR 영상은 CFR 검토본으로 변환해 주세요.')
    output_frames=frames
    if cancelled():raise InterruptedError('보간이 취소됐습니다.')
    if valid:
        import cupy as cp
        import nvvfx
    temp=output.with_name('.'+output.stem+'.'+uuid.uuid4().hex+'.partial'+output.suffix)
    log_path=Path(job['log'])
    processes=[];done=threading.Event()
    def watch():
        while not done.wait(.1):
            if cancelled():
                for p in list(processes):
                    if p.poll() is None:
                        try:p.kill()
                        except OSError:pass
                return
    threading.Thread(target=watch,daemon=True).start()
    generated=0;written=0;start=time.time();size=w*h*6
    try:
        with open(log_path,'w',encoding='utf-8') as log:
            replacements = _selected_edit_pixels(job['ffmpeg'], source, format_plan['decode_filter'],
                                                 [choices[key] for key in sorted(choices)], size, log, processes, cancelled)
            dec=subprocess.Popen([job['ffmpeg'],'-v','error','-nostdin','-noautorotate','-i',str(source),
                '-map','0:v:0','-an','-sn','-dn','-fps_mode','passthrough',
                '-vf',format_plan['decode_filter'],'-pix_fmt','rgb48le',
                *(['-frames:v',str(lookup[-1])] if lookup is not None else []),'-f','rawvideo','pipe:1'],
                stdout=subprocess.PIPE,stderr=log,creationflags=FLAGS)
            processes.append(dec)
            enc=subprocess.Popen([job['ffmpeg'],'-v','error','-nostdin','-f','rawvideo','-pixel_format','rgb48le',
                '-video_size',f'{w}x{h}','-framerate',job.get('fps_fraction') or str(fps),'-i','pipe:0','-i',str(timing_source),
                *(['-i',str(source)] if lookup is not None else []),
                '-map','0:v:0','-map','1:a?','-map_metadata','1','-map_chapters','1',
                '-map_metadata:s:v:0','2:s:v:0' if lookup is not None else '1:s:v:0','-c:a','copy',
                *format_plan['encoder_args'],str(temp)],stdin=subprocess.PIPE,stderr=log,creationflags=FLAGS)
            processes.append(enc)
            def read_decoded():
                if cancelled():raise InterruptedError('보간이 취소됐습니다.')
                data=bytearray()
                while len(data)<size:
                    chunk=dec.stdout.read(size-len(data))
                    if not chunk:break
                    data.extend(chunk)
                if cancelled():raise InterruptedError('보간이 취소됐습니다.')
                if not data:return None
                if len(data)!=size:raise RuntimeError('입력 프레임 디코딩이 불완전합니다.')
                return np.frombuffer(data,dtype='<u2').reshape(h,w,3)
            if lookup is None:
                read = read_decoded
            else:
                mapped = timewarp_frames(read_decoded, lookup)
                def read():
                    return next(mapped, None)
            def write(image):
                nonlocal written
                if cancelled():raise InterruptedError('보간이 취소됐습니다.')
                pixels = next(replacements) if written + 1 in output_edits else image.tobytes()
                enc.stdin.write(pixels);written+=1
                report('progress',frame=written,total=output_frames,generated=generated)
            def pack(image):
                rgb=np.rint(image.astype(np.float32)*(1023/65535)).astype(np.uint32)
                return cp.asarray(rgb[:,:,0] | rgb[:,:,1]<<10 | rgb[:,:,2]<<20 | np.uint32(0xc0000000))
            def unpack(capsule,left,right,frame):
                owned=cp.from_dlpack(capsule).copy();cp.cuda.get_current_stream().synchronize()
                validate_interpolated_pixels(owned,left,right,frame,cp)
                pixels=cp.asnumpy(owned)
                rgb=np.stack((pixels&1023,(pixels>>10)&1023,(pixels>>20)&1023),axis=-1)
                return np.rint(rgb.astype(np.float32)*(65535/1023)).astype('<u2')
            run_map={left:(end,g) for left,end,g in valid}
            # Selected replacement anchors intentionally span missing/held motion.
            # Automatic cut detection can silently copy an endpoint for those pairs.
            effect_context = nullcontext()
            if valid:
                mode=getattr(nvvfx.VideoFrameGeneration.Mode,job.get('quality','HIGH'))
                effect_context=nvvfx.VideoFrameGeneration(mode=mode,automatic_shot_change_detection_enabled=False,
                                                         image_encoding=nvvfx.ImageEncoding.RGB10A2)
            with effect_context as effect:
                if valid:
                    effect.input_width=w;effect.input_height=h;effect.load()
                frame=1;current=read()
                while current is not None:
                    if frame not in run_map:
                        write(current);current=read();frame+=1;continue
                    end,count=run_map[frame];left=current
                    write(left)
                    for _ in range(end-frame):
                        if read() is None:raise RuntimeError('보간 구간 안에서 영상이 끝났습니다.')
                    right=read()
                    if right is None:raise RuntimeError('뒤쪽 정상 프레임을 읽지 못했습니다.')
                    ref_left = (np.frombuffer(next(replacements),dtype='<u2').reshape(h,w,3)
                                if (frame,1) in choices else left)
                    ref_right = (np.frombuffer(next(replacements),dtype='<u2').reshape(h,w,3)
                                 if (frame,2) in choices else right)
                    left_gpu,right_gpu=pack(ref_left),pack(ref_right)
                    for target in range(1,count+1):
                        if cancelled():raise InterruptedError('보간이 취소됐습니다.')
                        if frame + target in overrides:
                            write(None)
                            continue
                        fraction=target/(count+1)
                        image=unpack(effect.run_at_timestep(left_gpu,right_gpu,fraction,shot_change=False).image,
                                     left_gpu,right_gpu,frame+target)
                        generated+=1;write(image)
                    frame=end+1;current=right
            enc.stdin.close()
            if enc.wait()!=0 or dec.wait()!=0:raise RuntimeError('Video decoding or source-format encoding failed. Check the FFmpeg log and available encoder.')
            if cancelled():raise InterruptedError('보간이 취소됐습니다.')
            if written!=output_frames:raise RuntimeError('출력 프레임 수가 처리 계획과 다릅니다. 다시 분석하세요.')
        check=probe_format(job['ffprobe'],temp,count_frames=True,cancelled=cancelled)
        properties=verify_format(format_plan,check,output_frames,job.get('fps_fraction') or str(fps))
        actual_rate=Fraction(properties['fps_fraction'])
        # Only the complete, source-format/frame-count/fps-validated file may replace output.
        # A late destination alias or cancellation is checked again here.
        if lookup is not None:
            saved_times = packet_times(job['ffprobe'], temp, cancelled)
            if len(saved_times) != len(times) or not np.allclose(saved_times,times,rtol=0,atol=max(.00001,tick)):
                raise RuntimeError('Saved video timestamps differ from the reference timeline.')
            check_file_stat(timing_source,timing_stat)
        check_file_stat(source,initial_stat)
        commit_validated_output(temp,source,output,cancelled,other_inputs=other_inputs)
        runs_rendered = []
        for left, end, count in valid:
            targets = [slot for slot in range(left + 1, end + 1) if slot not in overrides]
            entry = dict(start=left + 1, end=end, anchors=[left, end + 1], count=len(targets))
            if left in edit_refs:
                entry['edit_anchors'] = list(edit_refs[left])
            if len(targets) != count:
                entry['targets'] = targets
            runs_rendered.append(entry)
        result=dict(output=str(output),frames=written,source_frames=frames,delta=written-frames,
             generated=generated,skipped=0,fps_fraction=str(actual_rate),preserve_frame_count=True,
             format_mode=format_plan['mode'],format=properties,interpolation_precision=format_plan['precision'] if valid else None,
             automatic_shot_change_detection=False,endpoint_copy_guard=True,
             replacements=runs_rendered,seconds=round(time.time()-start,2))
        if overrides:
            result['frame_overrides'] = job['frame_overrides']
        if endpoints:
            result['range_endpoints'] = job['range_endpoints']
        report('complete', **result)
        return result
    finally:
        done.set()
        for p in processes:
            if p.poll() is None:p.kill()
            p.wait()
            for pipe in (p.stdin, p.stdout):
                if pipe and not pipe.closed:
                    try:
                        pipe.close()
                    except BrokenPipeError:
                        pass
        if temp.exists():temp.unlink()


def main():
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--job');args=p.parse_args()
    try:
        if args.check:
            import cupy as cp
            import nvvfx
            properties=cp.cuda.runtime.getDeviceProperties(0)
            name=properties['name']
            if isinstance(name,bytes):name=name.decode()
            if not hasattr(nvvfx,'VideoFrameGeneration'):raise RuntimeError('Video Frame Generation을 지원하는 NVIDIA VFX SDK가 필요합니다.')
            emit('ready',gpu=name)
        else:render(json.loads(Path(args.job).read_text(encoding='utf-8')))
    except InterruptedError as e:emit('cancelled',message=str(e));raise SystemExit(2)
    except Exception as e:emit('error',message=str(e));raise SystemExit(1)


if __name__=='__main__':main()
