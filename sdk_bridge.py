"""Launches the optional, installed NVIDIA SDK environment without bundling it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import hold_core as core


def app_dir():
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent


def worker():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'sdk_worker.py'


def settings_file():
    folder=Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'FrameHoldFinder'
    folder.mkdir(parents=True,exist_ok=True)
    return folder/'sdk_settings.json'


def _configured_paths():
    for config in (settings_file(),app_dir()/'sdk_settings.json'):
        try:
            value=json.loads(config.read_text(encoding='utf-8-sig'))['python']
            if isinstance(value,str) and value.strip():yield value
        except (OSError,ValueError,KeyError,TypeError):pass


def configured_runtime():
    """Keep a missing saved path visible so it can be corrected in settings."""
    return next(_configured_paths(),None)


def runtime():
    for value in _configured_paths():
        if Path(value).is_file():return value
    return None


def save_runtime(path):
    if not Path(path).is_file():raise ValueError('SDK Python 실행 파일이 없습니다.')
    settings_file().write_text(json.dumps(dict(python=str(path)),ensure_ascii=False),encoding='utf-8')


def check(executable=None):
    executable=runtime() if executable is None else executable
    if not executable:raise RuntimeError('NVIDIA SDK 환경을 선택해 주세요.')
    if not Path(executable).is_file():raise RuntimeError('SDK Python 실행 파일을 찾을 수 없습니다. 경로를 다시 선택하세요.')
    p=subprocess.run([executable,str(worker()),'--check'],capture_output=True,creationflags=core.FLAGS,timeout=45)
    for line in reversed(p.stdout.decode('utf-8','replace').splitlines()):
        try:
            item=json.loads(line)
            if item['event']=='ready':return item
            if item['event']=='error':raise RuntimeError(item['message'])
        except (ValueError,KeyError):continue
    raise RuntimeError(p.stderr.decode('utf-8','replace')[-900:] or 'SDK 확인이 실패했습니다.')


def repair(analysis,candidates,output,quality,progress,cancel,*,format_mode='source'):
    executable=runtime()
    if not executable:raise RuntimeError('NVIDIA SDK 환경을 선택해 주세요.')
    metadata=analysis['metadata']
    with tempfile.TemporaryDirectory(prefix='FrameHoldFinder-') as folder:
        folder=Path(folder);cancel_file=folder/'cancel';log=folder/'render.log'
        request=dict(source=analysis['path'],output=str(output),width=metadata['width'],height=metadata['height'],
            fps=metadata['fps'],fps_fraction=metadata.get('fps_fraction'),frames=analysis['frames'],runs=candidates,quality=quality,
            source_stat=analysis.get('source_stat'),preserve_frame_count=True,format_mode=format_mode,
            cancel_file=str(cancel_file),log=str(log),
            ffmpeg=core.binary('ffmpeg'),ffprobe=core.binary('ffprobe'))
        path=folder/'job.json';path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
        complete=None;error=None;done=threading.Event()
        with open(folder/'sdk.log','w',encoding='utf-8') as sdklog:
            p=subprocess.Popen([executable,str(worker()),'--job',str(path)],stdout=subprocess.PIPE,stderr=sdklog,
                text=True,encoding='utf-8',errors='replace',creationflags=core.FLAGS)
            def watch():
                while not done.wait(.1):
                    if cancel.is_set():
                        cancel_file.touch()
                        if not done.wait(20) and p.poll() is None:
                            subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=core.FLAGS)
                        return
            threading.Thread(target=watch,daemon=True).start()
            try:
                for line in p.stdout:
                    try:item=json.loads(line)
                    except ValueError:continue
                    if item.get('event')=='progress':progress(item['frame'],item['total'])
                    elif item.get('event')=='complete':complete=item
                    elif item.get('event')=='error':error=item.get('message')
                code=p.wait()
            finally:
                done.set()
                if p.poll() is None:p.kill()
                p.wait();p.stdout.close()
        if complete:
            if complete.get('frames')!=analysis['frames'] or complete.get('delta')!=0:
                raise RuntimeError('싱크 유지 검증 실패: 출력 프레임 수가 원본과 다릅니다.')
            return complete
        if cancel.is_set():raise core.Cancelled()
        if code or not complete:
            detail=log.read_text(encoding='utf-8',errors='replace')[-1200:] if log.exists() else ''
            sdkdetail=(folder/'sdk.log').read_text(encoding='utf-8',errors='replace')[-1000:]
            raise RuntimeError(error or detail or sdkdetail or 'NVIDIA 보간이 실패했습니다.')
