"""SDR repeated-frame candidate analysis. No Nuke or frame synthesis."""
import csv
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

import numpy as np

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def binary(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    for path in (root / 'bin' / (name + '.exe'), root / (name + '.exe')):
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if not found:
        raise RuntimeError('%s 실행 파일을 찾지 못했습니다.' % name)
    return found


def probe(path):
    p = subprocess.run([binary('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,color_transfer:format=duration',
        '-of', 'json', str(path)], capture_output=True, creationflags=FLAGS)
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[-1500:])
    data = json.loads(p.stdout)
    if not data.get('streams'):
        raise ValueError('영상 스트림이 없습니다.')
    s = data['streams'][0]
    def rate(value):
        a, b = value.split('/')
        return float(a) / float(b) if float(b) else 0
    fps = rate(s.get('avg_frame_rate', '0/1')) or rate(s.get('r_frame_rate', '0/1'))
    if fps <= 0:
        raise ValueError('프레임 속도를 읽지 못했습니다.')
    duration = float(data.get('format', {}).get('duration', 0))
    return dict(width=int(s['width']), height=int(s['height']), fps=fps,
        fps_fraction=s.get('avg_frame_rate') if rate(s.get('avg_frame_rate','0/1')) else s.get('r_frame_rate'),
        estimated=int(s.get('nb_frames', 0) or 0) if str(s.get('nb_frames','')).isdigit() else round(duration*fps),
        transfer=s.get('color_transfer', 'unknown'))


class Cancelled(Exception):
    pass


def scan(path, width=1280, progress=None, cancel=None):
    """Max of channel-wise mean absolute differences in normalized linear RGB."""
    initial_stat = os.stat(path)
    meta = probe(path)
    if meta['transfer'] in ('smpte2084', 'arib-std-b67'):
        raise ValueError('현재 버전은 SDR 영상용입니다. HDR 영상은 SDR 검토본으로 변환해 주세요.')
    w = min(width, meta['width']) if width else meta['width']
    h = max(1, round(meta['height'] * w / meta['width']))
    # 16-bit decoding avoids discarding low-level differences in high-bit-depth MOV.
    cmd = [binary('ffmpeg'), '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:v:0',
        '-an', '-sn', '-dn', '-vf', 'scale=%d:%d' % (w,h), '-fps_mode', 'passthrough',
        '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1']
    values = np.arange(65536, dtype=np.float32) / 65535
    if meta['transfer'] == 'iec61966-2-1':
        lut = np.where(values <= .04045, values/12.92, ((values+.055)/1.055)**2.4)
    else:
        lut = np.where(values < .081, values/4.5, ((values+.099)/1.099)**(1/.45))
    cancel = cancel or threading.Event()
    frame_size = w*h*3*2
    previous = None
    scores = []
    frames = 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, creationflags=FLAGS)
        # A watcher interrupts even a blocked decoder/read when Cancel is pressed.
        done = threading.Event()
        def watch():
            while not done.wait(.1):
                if cancel.is_set():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()
        try:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                data = bytearray()
                while len(data) < frame_size:
                    chunk = process.stdout.read(frame_size-len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if cancel.is_set():
                    raise Cancelled()
                if not data:
                    break
                if len(data) != frame_size:
                    raise RuntimeError('영상 디코딩 중 불완전한 프레임을 받았습니다.')
                current = lut[np.frombuffer(data,dtype='<u2').reshape(h,w,3)]
                frames += 1
                if previous is not None:
                    scores.append(float(np.abs(current-previous).mean(axis=(0,1), dtype=np.float64).max()))
                previous = current
                if progress:
                    progress(frames,meta['estimated'])
            code = process.wait()
            if cancel.is_set():
                raise Cancelled()
            if code:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8','replace')[-1800:])
        finally:
            done.set()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
    if not frames:
        raise ValueError('디코딩된 프레임이 없습니다.')
    end_stat = os.stat(path)
    if initial_stat.st_size != end_stat.st_size or initial_stat.st_mtime_ns != end_stat.st_mtime_ns:
        raise RuntimeError('분석 도중 영상 파일이 변경됐습니다. 다시 분석하세요.')
    return dict(path=str(path), metadata=meta, frames=frames, scores=scores, analysis_width=w,
                method='max-channel mean absolute linear-RGB difference',
                source_stat=dict(size=end_stat.st_size,mtime_ns=end_stat.st_mtime_ns))


def runs(scores, tolerance=.002):
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('임계값은 0 이상의 유한한 숫자여야 합니다.')
    result = []
    start = None
    for frame, score in enumerate(scores, 2):
        if not math.isfinite(score):
            raise ValueError('유효하지 않은 차이값입니다.')
        if score <= tolerance:
            if start is None:
                start = frame-1
        elif start is not None:
            result.append(dict(start=start,end=frame-1,copies=frame-start-2,
                               mean=float(np.mean(scores[start-1:frame-2]))))
            start = None
    if start is not None:
        end = len(scores)+1
        result.append(dict(start=start,end=end,copies=end-start,
                           mean=float(np.mean(scores[start-1:end-1]))))
    # Every run includes its first occurrence; copies count later frames only.
    for r in result:
        r['copies'] = r['end']-r['start']
    return result


def export_csv(path, analysis, tolerance):
    candidates = runs(analysis['scores'], tolerance)
    fps = analysis['metadata']['fps']
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f)
        w.writerow(['file','start_frame_1based','end_frame_1based','repeated_copies',
                    'start_seconds_approx','end_seconds_approx','mean_difference','tolerance','analysis_width'])
        for r in candidates:
            w.writerow([analysis['path'],r['start'],r['end'],r['copies'],
                (r['start']-1)/fps,(r['end']-1)/fps,r['mean'],tolerance,analysis['analysis_width']])


def preview(path, frame):
    p=subprocess.run([binary('ffmpeg'),'-v','error','-nostdin','-i',str(path),
        '-vf','select=eq(n\\,%d),scale=440:-1' % (frame-1),'-frames:v','1',
        '-f','image2pipe','-vcodec','png','pipe:1'],capture_output=True,creationflags=FLAGS)
    if p.returncode or not p.stdout:
        raise RuntimeError('프레임 미리보기를 불러오지 못했습니다.')
    return p.stdout
