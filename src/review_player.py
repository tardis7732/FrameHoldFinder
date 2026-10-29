"""Bounded, silent source-video review with frame-accurate navigation.

FFmpeg seeks by decoded frame index, not rounded timestamps. A seek may decode
the earlier part of a long source, but it never loads that part into RAM and is
cancelled when superseded. Cache: 48 RGB frames; worker queue: 8 frames.
"""
from collections import OrderedDict
import math
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import font as tkfont
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk
import hold_core as core
import motion_core as motion
from ui_support import PALETTE, attach_tooltip, motion_graph_limit


def event_for_frame(rows,frame):
    """Manual interiors win; their untouched endpoints keep their own event."""
    matches=[r for r in rows if (frame in r.get('targets',[]) if r['kind']=='replace'
                               else r['start']<frame<=r['end']+1)]
    active=[r for r in matches if r.get('included',True) and not r.get('blocked_by_manual')]
    return (next((r for r in active if r['kind']=='replace'),None)
            or next(iter(active),None)
            or next((r for r in matches if r['kind']!='replace'),None)
            or next(iter(matches),None))


def hold_frame_marks(model,rows):
    """Original held-image positions, including each run's first occurrence."""
    if not model or not model.get('hold_enabled'):return []
    included={r['key']:r.get('included',True) and not r.get('blocked_by_manual') for r in rows}
    return [(frame,not included.get(run['key'],run.get('active',True)))
            for run in model.get('holds',[])
            for frame in range(run['start'],run['end']+1)]


def is_resolved_jump(model,metric,threshold):
    """A raw peak that crosses below the threshold after active hold repair."""
    return bool(model and model.get('hold_enabled')
        and any(r.get('active',True) for r in model.get('holds',[]))
        and metric['raw_score']>threshold and metric['score']<=threshold)


class _Decoder:
    def __init__(self,path,start,width,height):
        self.path=path;self.start=start;self.width=width;self.height=height
        self.events=queue.Queue(maxsize=8);self.cancelled=threading.Event()
        self.lock=threading.Lock();self.process=None;self.finished=False
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:self.process.kill()
                except OSError:pass

    def _put(self,event):
        while not self.cancelled.is_set():
            try:self.events.put(event,timeout=.1);return True
            except queue.Full:pass
        return False

    def _run(self):
        process=None
        try:
            # select uses decoded zero-based n; -ss timestamp seeking is not
            # used because it can mislabel frames for VFR/nonzero start times.
            command=[core.binary('ffmpeg'),'-v','error','-nostdin','-noautorotate',
                '-i',self.path,'-map','0:v:0','-an','-sn','-dn',
                '-vf',f'select=gte(n\\,{self.start-1}),scale={self.width}:{self.height}',
                '-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo','pipe:1']
            with tempfile.TemporaryFile() as errors:
                with self.lock:
                    if self.cancelled.is_set():return
                    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,creationflags=core.FLAGS)
                    self.process=process
                size=self.width*self.height*3;frame=self.start
                while not self.cancelled.is_set():
                    data=bytearray()
                    while len(data)<size and not self.cancelled.is_set():
                        part=process.stdout.read(size-len(data))
                        if not part:break
                        data.extend(part)
                    if self.cancelled.is_set():return
                    if not data:break
                    if len(data)!=size:raise RuntimeError('불완전한 검토 프레임을 받았습니다.')
                    if not self._put(('frame',frame,bytes(data))):return
                    frame+=1
                code=process.wait()
                if self.cancelled.is_set():return
                if code:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode('utf-8','replace')[-800:] or '영상 디코딩이 실패했습니다.')
                self._put(('end',frame-1))
        except Exception as e:
            if not self.cancelled.is_set():self._put(('error',str(e)))
        finally:
            if process is not None:
                if process.poll() is None:
                    try:process.kill()
                    except OSError:pass
                process.wait()
                if process.stdout is not None:process.stdout.close()
            self.finished=True


class ReviewPlayer:
    CACHE_FRAMES=48
    PREFETCH_FRAMES=12
    PLAY_TEXT='▶ 재생'
    PAUSE_TEXT='❚❚ 정지'
    EMPTY_TEXT='영상을 선택하면 여기서 재생할 수 있습니다.'

    def __init__(self,parent,analysis=None,on_frame=None,embedded=False,show_plot=True):
        self.analysis=None;self.on_frame=on_frame;self.embedded=bool(embedded);self.show_plot=bool(show_plot)
        self.path='';self.frames=0;self.fps=24.;self.source_width=960;self.source_height=540
        self.width=960;self.height=540
        self.cache=OrderedDict();self.decoder=None;self.received=0
        self.current_frame=0;self.playing=False;self.closed=False;self.pending_frame=None
        self.current_bytes=None;self.photo=None;self.model=None;self.rows=[];self.threshold=5.
        self.after_id=None;self.scrub_id=None;self.slider_updating=False;self.resume_scrub=False
        self.next_due=time.monotonic();self.buffering=False
        if self.embedded:
            self.window=ttk.Frame(parent,style='Surface.TFrame')
        else:
            self.window=tk.Toplevel(parent);self.window.title(tr('영상 검토'))
            self.window.geometry('1060x850');self.window.minsize(800,650)
            self.window.configure(bg=PALETTE['app']);self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.widget=self.window
        self.window.bind('<Destroy>',self._destroyed,add='+')
        style=ttk.Style(self.window)
        # The main workspace owns these shared styles; fallbacks also keep a
        # standalone player consistent when opened without the main window.
        for name,settings in (
                ('Surface.TFrame',dict(background=PALETTE['surface'])),
                ('Surface.TLabel',dict(background=PALETTE['surface'],foreground=PALETTE['text'])),
                ('Muted.TLabel',dict(background=PALETTE['surface'],foreground=PALETTE['muted'])),
                ('Compact.TButton',dict(padding=(9,5))),
                ('Accent.TButton',dict(background=PALETTE['secondary'],foreground=PALETTE['secondary_fg'],padding=(12,6)))):
            if not style.configure(name):style.configure(name,**settings)
        style.configure('Player.TCheckbutton',background=PALETTE['surface'],foreground=PALETTE['text'])
        style.map('Player.TCheckbutton',background=[('active',PALETTE['surface'])],foreground=[('disabled',PALETTE['muted'])])
        style.configure('Player.Horizontal.TScale',background=PALETTE['primary'],troughcolor=PALETTE['track'],
            bordercolor=PALETTE['track'],lightcolor=PALETTE['primary'],darkcolor=PALETTE['primary'],focuscolor=PALETTE['focus'],
            borderwidth=0,troughborderwidth=0,sliderlength=16,relief='flat',troughrelief='flat')
        thumb_states=[('disabled',PALETTE['track']),('pressed',PALETTE['primary_pressed']),
                      ('active',PALETTE['primary_hover']),('!disabled',PALETTE['primary'])]
        style.map('Player.Horizontal.TScale',background=thumb_states,lightcolor=thumb_states,darkcolor=thumb_states)
        self.body=body=ttk.Frame(self.window,padding=2 if self.embedded else 12,style='Surface.TFrame');body.pack(fill='both',expand=True)
        # The image row alone absorbs a small host's height reduction; playback
        # controls retain their requested size instead of falling below it.
        body.columnconfigure(0,weight=1);body.rowconfigure(2,weight=1)
        # An image must not force the embedded pane to request its 960px width.
        self.screen_host=ttk.Frame(body,width=560 if self.embedded else 960,height=315 if self.embedded else 540,style='Surface.TFrame')
        self.screen_host.grid(row=2,column=0,sticky='nsew');self.screen_host.pack_propagate(False)
        self.screen=tk.Label(self.screen_host,bg=PALETTE['app'],fg=PALETTE['muted'],text=tr(self.EMPTY_TEXT),
            font=('맑은 고딕',11),anchor='center',takefocus=True,borderwidth=0,padx=0,pady=0,
            highlightthickness=1,highlightbackground=PALETTE['border'],highlightcolor=PALETTE['focus'])
        self.screen.pack(fill='both',expand=True)
        self.screen.bind('<Button-1>',lambda e:self._focus_and_toggle())
        self.screen.bind('<Configure>',lambda e:self._redraw())
        self.info=DisplayVar(value='')
        # Normal playback has no extra status row. Errors use a bounded row
        # until the next valid seek or source change.
        info_font=tkfont.Font(root=self.window,font=ttk.Style(self.window).lookup('TLabel','font') or 'TkDefaultFont')
        self.info_host=ttk.Frame(body,height=info_font.metrics('linespace')*2+4,style='Surface.TFrame')
        self.info_host.grid(row=3,column=0,sticky='ew',pady=(4,2));self.info_host.pack_propagate(False)
        self.info_label=ttk.Label(self.info_host,textvariable=self.info,style='Surface.TLabel',foreground=PALETTE['muted'],wraplength=560,anchor='w')
        self.info_label.pack(fill='both',expand=True)
        self.info_host.grid_remove()
        self.plot=None
        if self.show_plot:
            self.plot=tk.Canvas(body,height=67,bg=PALETTE['surface'],highlightthickness=1,highlightbackground=PALETTE['border'])
            self.plot.grid(row=4,column=0,sticky='ew');self.plot.bind('<Configure>',lambda e:self._draw_plot())
            self.plot.bind('<Button-1>',self._plot_click)
        self.position=tk.DoubleVar(value=0)
        self.timeline_host=tk.Frame(body,bg=PALETTE['surface'],borderwidth=0,
            highlightthickness=1,highlightbackground=PALETTE['surface'])
        self.timeline_host.grid(row=5,column=0,sticky='ew',pady=(4,4));self.timeline_host.columnconfigure(0,weight=1)
        self.ruler_font=tkfont.Font(root=self.window,family='맑은 고딕',size=9,weight='bold')
        self.timeline_ruler=tk.Canvas(self.timeline_host,height=31,bg=PALETTE['surface'],highlightthickness=0,takefocus=False)
        self.ruler=self.timeline_ruler
        self.timeline_ruler.grid(row=0,column=0,sticky='ew')
        self.timeline_ruler.bind('<Configure>',lambda e:self._draw_timeline_ruler())
        self.timeline_ruler.bind('<ButtonPress-1>',self._ruler_press)
        self.timeline_ruler.bind('<B1-Motion>',self._ruler_drag)
        self.timeline_ruler.bind('<ButtonRelease-1>',self._ruler_release)
        self.timeline=ttk.Scale(self.timeline_host,from_=0,to=1,variable=self.position,command=self._scrub_changed,style='Player.Horizontal.TScale')
        self.timeline.grid(row=1,column=0,sticky='ew')
        self.ruler_trace=self.position.trace_add('write',lambda *args:self._draw_timeline_ruler())
        self.timeline.bind('<ButtonPress-1>',self._scrub_press,add='+')
        self.timeline.bind('<ButtonRelease-1>',self._scrub_release,add='+')
        self.timeline.bind('<FocusIn>',lambda e:self.timeline_host.configure(highlightbackground=PALETTE['focus']),add='+')
        self.timeline.bind('<FocusOut>',lambda e:self.timeline_host.configure(highlightbackground=PALETTE['surface']),add='+')
        self.controls_host=controls=ttk.Frame(body,style='Surface.TFrame');controls.grid(row=6,column=0,sticky='ew',pady=1)
        # Equal side columns keep the transport at the pane's exact center,
        # even though playback options and the frame entry have different widths.
        controls.columnconfigure(0,weight=1,uniform='player_sides');controls.columnconfigure(2,weight=1,uniform='player_sides')
        self.transport_host=transport=ttk.Frame(controls,style='Surface.TFrame');transport.grid(row=0,column=1)
        self.previous_button=ttk.Button(transport,text=tr('−1'),command=lambda:self.step(-1),width=3,style='Compact.TButton');self.previous_button.pack(side='left')
        self.play_button=ttk.Button(transport,text=tr(self.PLAY_TEXT),command=self.toggle,width=7,style='Accent.TButton');self.play_button.pack(side='left',padx=4)
        self.next_button=ttk.Button(transport,text=tr('+1'),command=lambda:self.step(1),width=3,style='Compact.TButton');self.next_button.pack(side='left')
        self.options_host=options=ttk.Frame(controls,style='Surface.TFrame');options.grid(row=0,column=0,sticky='w')
        self.speed=tk.StringVar(value='1×')
        self.speed_box=ttk.Combobox(options,values=['0.25×','0.5×','1×','2×'],textvariable=self.speed,width=5,state='readonly')
        self.speed_box.pack(side='left');self.speed_box.bind('<<ComboboxSelected>>',lambda e:self._speed_changed())
        self.loop=tk.BooleanVar(value=True)
        self.loop_check=ttk.Checkbutton(options,text=tr('반복'),variable=self.loop,style='Player.TCheckbutton');self.loop_check.pack(side='left',padx=(4,4))
        self.vectors=tk.BooleanVar(value=True)
        self.vector_check=ttk.Checkbutton(options,text=tr('벡터'),variable=self.vectors,command=self._redraw,style='Player.TCheckbutton');self.vector_check.pack(side='left')
        self.frame_group=frame_group=ttk.Frame(controls,style='Surface.TFrame');frame_group.grid(row=0,column=2,sticky='e')
        self.frame_text=tk.StringVar(value='0')
        self.frame_entry=ttk.Entry(frame_group,textvariable=self.frame_text,width=5,justify='right');self.frame_entry.pack(side='left')
        self.frame_entry.bind('<Return>',self._frame_enter);self.frame_entry.bind('<FocusIn>',lambda e:self.pause())
        self.total_text=tk.StringVar(value='/ 0');ttk.Label(frame_group,textvariable=self.total_text,style='Muted.TLabel').pack(side='left',padx=(5,0))
        attach_tooltip(self.previous_button,'이전 프레임으로 이동합니다.')
        attach_tooltip(self.play_button,'재생 / 일시정지\n미리보기는 소리 없이 재생됩니다.')
        attach_tooltip(self.next_button,'다음 프레임으로 이동합니다.')
        attach_tooltip(self.speed_box,'재생 속도')
        attach_tooltip(self.frame_entry,'프레임 번호를 입력하고 Enter를 누르세요.')
        attach_tooltip(self.vector_check,'화살표 길이만 3배로 표시합니다.\n실제 이동량과 검출 기준은 변하지 않습니다.')
        self.controls=(self.previous_button,self.play_button,self.next_button,self.frame_entry,self.timeline,self.loop_check,self.vector_check)
        # A unique bindtag scopes standalone-player shortcuts. The workspace
        # can place its playback bindtag first without registering them twice.
        self.key_tag='ReviewPlayerKeys_'+str(id(self))
        self.window.bind_class(self.key_tag,'<space>',self._space)
        self.window.bind_class(self.key_tag,'<Left>',lambda e:self._arrow(e,-1))
        self.window.bind_class(self.key_tag,'<Right>',lambda e:self._arrow(e,1))
        self.window.bind_class(self.key_tag,'<Home>',lambda e:self._boundary(e,False))
        self.window.bind_class(self.key_tag,'<End>',lambda e:self._boundary(e,True))
        self._bind_descendants(self.window)
        body.bind('<Configure>',lambda e:self.info_label.configure(wraplength=max(240,e.width-20)),add='+')
        self._set_controls(False)
        if analysis is not None:self.load(analysis)
        self.after_id=self.window.after(10,self._pump)

    def _bind_descendants(self,widget):
        tags=list(widget.bindtags())
        if self.key_tag not in tags:widget.bindtags(tuple([tags[0],self.key_tag]+tags[1:]))
        for child in widget.winfo_children():self._bind_descendants(child)

    def _set_error(self,text):
        if not self.is_open():return
        self.info.set(text)
        if text:self.info_host.grid()
        else:self.info_host.grid_remove()

    def _set_controls(self,enabled):
        state='normal' if enabled else 'disabled'
        for widget in self.controls:widget.configure(state=state)
        self.speed_box.configure(state='readonly' if enabled else 'disabled')

    def load(self,analysis):
        if not self.is_open():return
        if analysis is None:self.clear();return
        frames=int(analysis['frames']);meta=analysis['metadata'];fps=float(meta['fps'])
        width=int(meta['width']);height=int(meta['height'])
        if frames<1 or width<1 or height<1 or not math.isfinite(fps) or fps<=0:
            raise ValueError('유효한 영상 크기, 프레임 수, fps가 필요합니다.')
        self.clear()
        self.analysis=analysis;self.path=str(analysis['path']);self.frames=frames;self.fps=fps
        self.source_width=width;self.source_height=height
        factor=min(1.,960/width,540/height)
        self.width=max(1,round(width*factor));self.height=max(1,round(height*factor))
        self.total_text.set(f'/ {frames}')
        if not self.embedded:self.window.title(tr('영상 검토')+' · '+Path(self.path).name)
        self.slider_updating=True;self.timeline.configure(from_=1,to=max(2,frames));self.position.set(1);self.slider_updating=False
        self.frame_text.set('1');self.screen.configure(text=tr('첫 프레임을 불러오는 중…'))
        self._set_controls(True);self._draw_plot();self.seek(1,play=False)

    def clear(self):
        if not self.is_open():return
        if self.decoder is not None:self.decoder.stop();self.decoder=None
        if self.scrub_id:
            self.window.after_cancel(self.scrub_id);self.scrub_id=None
        self.playing=False;self.pending_frame=None;self.current_frame=0;self.received=0
        self.buffering=False;self.resume_scrub=False;self.current_bytes=None;self.photo=None
        self.cache.clear();self.analysis=None;self.path='';self.frames=0;self.fps=24.
        self.model=None;self.rows=[];self.frame_text.set('0');self.total_text.set('/ 0')
        self.slider_updating=True;self.timeline.configure(from_=0,to=1);self.position.set(0);self.slider_updating=False
        self.play_button.configure(text=tr(self.PLAY_TEXT));self._set_controls(False)
        self.screen.configure(image='',text=tr(self.EMPTY_TEXT))
        self._set_error('')
        if self.plot is not None:self.plot.delete('all')
        if not self.embedded:self.window.title(tr('영상 검토'))

    def is_open(self):
        if self.closed:return False
        try:return bool(self.window.winfo_exists())
        except tk.TclError:return False

    def close(self):
        if self.closed:return
        self._cleanup()
        try:self.window.destroy()
        except tk.TclError:pass

    def _destroyed(self,event):
        if event.widget is self.window:self._cleanup()

    def _cleanup(self):
        if self.closed:return
        self.closed=True;self.playing=False
        if self.decoder is not None:self.decoder.stop();self.decoder=None
        for task in (self.after_id,self.scrub_id):
            if task:
                try:self.window.after_cancel(task)
                except tk.TclError:pass
        for key in ('<space>','<Left>','<Right>','<Home>','<End>'):
            try:self.window.unbind_class(self.key_tag,key)
            except tk.TclError:pass
        if self.ruler_trace:
            try:self.position.trace_remove('write',self.ruler_trace)
            except tk.TclError:pass
            self.ruler_trace=None
        self.cache.clear();self.current_bytes=None;self.photo=None

    def _restart(self,frame):
        if self.decoder is not None:self.decoder.stop()
        self.decoder=_Decoder(self.path,frame,self.width,self.height);self.received=frame-1

    def _request(self,frame):
        self.pending_frame=frame
        if frame in self.cache:
            self._display(frame);self.pending_frame=None;return
        if self.decoder is None or frame<=self.received or frame<self.decoder.start or self.decoder.finished:
            self._restart(frame)
        self.buffering=True

    def seek(self,frame,play=False):
        if not self.is_open() or self.analysis is None or self.frames<1:return
        try:frame=max(1,min(self.frames,int(frame)))
        except (ValueError,TypeError):return
        self._set_error('')
        self.playing=bool(play);self.play_button.configure(text=tr(self.PAUSE_TEXT if self.playing else self.PLAY_TEXT))
        self.next_due=time.monotonic()+self._period()
        self._request(frame)

    def step(self,offset):
        if self.analysis is None:return
        base=self.pending_frame if self.pending_frame is not None else max(1,self.current_frame)
        self.seek(base+offset,play=False)

    def toggle(self):
        if self.analysis is None:return
        if self.playing:self.pause()
        else:
            frame=max(1,self.current_frame)
            if frame>=self.frames:frame=1
            self.seek(frame,play=True)

    def pause(self):
        self.playing=False
        if self.is_open():self.play_button.configure(text=tr(self.PLAY_TEXT))

    def _focus_and_toggle(self):
        self.screen.focus_set();self.toggle()

    def _typing(self,event):
        return event.widget.winfo_class() in ('Entry','TEntry','Spinbox','TSpinbox','TCombobox','Text')

    def _space(self,event):
        if self._typing(event) or event.widget.winfo_class() in ('Button','TButton','Checkbutton','TCheckbutton'):return
        if self.analysis is None:return 'break'
        self.toggle();return 'break'

    def _arrow(self,event,direction):
        if self._typing(event):return
        if self.analysis is None:return 'break'
        self.step(direction);return 'break'

    def _boundary(self,event,last):
        if self._typing(event):return
        if self.analysis is None:return 'break'
        self.seek(self.frames if last else 1,play=False)
        return 'break'

    def _frame_enter(self,event=None):
        if self.analysis is None:return 'break'
        try:frame=int(self.frame_text.get())
        except ValueError:self._set_error('프레임 번호에 정수를 입력하세요.');return 'break'
        self.seek(frame);self.screen.focus_set();return 'break'

    def _period(self):return 1/(self.fps*float(self.speed.get().replace('×','')))

    def _speed_changed(self):self.next_due=time.monotonic()+self._period()

    def _ruler_limits(self):
        width=max(24,self.timeline_ruler.winfo_width())
        return 10.,float(width-10)

    def _draw_timeline_ruler(self):
        if not self.is_open():return
        canvas=self.timeline_ruler;canvas.delete('all');x0,x1=self._ruler_limits()
        canvas.create_line(x0,28,x1,28,fill=PALETTE['border'],tags='ruler_baseline')
        if self.frames>1:
            span=self.frames-1
            desired=max(1.,span/max(1.,(x1-x0)/7))
            magnitude=10**math.floor(math.log10(desired))
            step=next(int(choice*magnitude) for choice in (1,2,5,10) if choice*magnitude>=desired)
            ticks=list(range(1,self.frames+1,step))
            if ticks[-1]!=self.frames:ticks.append(self.frames)
            positions=[(x0+(frame-1)/span*(x1-x0),frame==self.frames or (frame-1)%(step*5)==0) for frame in ticks]
        else:
            positions=[(x0+(x1-x0)*i/10,i%5==0) for i in range(11)]
        for x,major in positions:
            canvas.create_line(x,20 if major else 24,x,28,fill=PALETTE['border'] if major else PALETTE['track'],tags='ruler_tick')
        try:frame=round(self.position.get())
        except (ValueError,tk.TclError):frame=max(0,self.current_frame)
        frame=max(1,min(self.frames,frame)) if self.frames else 0
        x=x0+(frame-1)/max(1,self.frames-1)*(x1-x0) if self.frames else x0
        half=self.ruler_font.measure(str(frame))/2+2
        label_x=max(half,min(x1+10-half,x))
        canvas.create_text(label_x,0,text=tr(str(frame)),font=self.ruler_font,fill=PALETTE['primary'] if self.frames else PALETTE['muted'],anchor='n',tags='current_frame_label')
        canvas.create_line(x,17,x,29,fill=PALETTE['primary'] if self.frames else PALETTE['border'],tags='current_frame_marker')

    def _set_ruler_position(self,event):
        if self.analysis is None:return
        x0,x1=self._ruler_limits()
        fraction=max(0.,min(1.,(event.x-x0)/max(1.,x1-x0)))
        self.position.set(1+round(fraction*max(0,self.frames-1)))
        self._scrub_changed(str(self.position.get()))

    def _ruler_press(self,event):
        if self.analysis is None:return 'break'
        self.timeline.focus_set();self._scrub_press(event);self._set_ruler_position(event)
        return 'break'

    def _ruler_drag(self,event):
        self._set_ruler_position(event)
        return 'break'

    def _ruler_release(self,event):
        if self.analysis is None:return 'break'
        self._set_ruler_position(event);self._scrub_release(event)
        return 'break'

    def _scrub_press(self,event):
        if self.analysis is None:return
        self.resume_scrub=self.playing;self.pause()

    def _scrub_changed(self,value):
        if self.slider_updating or self.closed or self.analysis is None:return
        if self.scrub_id:self.window.after_cancel(self.scrub_id)
        self.scrub_id=self.window.after(80,self._apply_scrub)

    def _apply_scrub(self):
        self.scrub_id=None;self.seek(round(self.position.get()),play=False)

    def _scrub_release(self,event):
        if self.analysis is None:return
        if self.scrub_id:self.window.after_cancel(self.scrub_id);self.scrub_id=None
        self.seek(round(self.position.get()),play=self.resume_scrub)

    def _plot_click(self,event):
        if self.plot is None or self.analysis is None:return
        width=max(2,self.plot.winfo_width()-20)
        frame=1+round((event.x-10)/width*max(1,self.frames-1))
        self.seek(frame,play=self.playing)

    def set_model(self,model,rows,threshold):
        if not self.is_open():return
        self.model=model;self.rows=list(rows or [])
        try:value=float(threshold);self.threshold=value if math.isfinite(value) and value>=0 else 5.
        except (ValueError,TypeError):self.threshold=5.
        self._draw_plot();self._redraw()

    def _event_at(self,frame):
        return event_for_frame(self.rows,frame)

    def _display(self,frame):
        data=self.cache.get(frame)
        if data is None:return
        changed=frame!=self.current_frame
        self.current_frame=frame;self.current_bytes=data;self.buffering=False
        self.cache.move_to_end(frame)
        self.slider_updating=True;self.position.set(frame);self.slider_updating=False
        self.frame_text.set(str(frame));self._redraw();self._cursor()
        if changed and self.on_frame is not None:
            try:self.on_frame(frame)
            except Exception as e:self._set_error('프레임 표시 연결 오류: '+str(e))

    def _redraw(self):
        if not self.is_open() or self.current_bytes is None:return
        image=Image.frombytes('RGB',(self.width,self.height),self.current_bytes)
        if self.vectors.get() and self.current_frame>=1:
            # Frame 1 has no incoming step: preview 1→2 at its source points.
            # All later frames retain their existing previous→current mapping.
            steps=self.analysis.get('steps',[]);index=max(0,self.current_frame-2)
            if 0<=index<len(steps):
                points,vectors,ids=self._display_vectors(index);draw=ImageDraw.Draw(image)
                sx=self.width/self.analysis['analysis_width'];sy=self.height/self.analysis['analysis_height']
                vx=self.width/self.source_width*3;vy=self.height/self.source_height*3
                fast=self._fast_points(index,ids)
                fast_color=self._fast_color(index)
                # Keep small local groups of fast points visible instead of
                # losing them in a uniform every-nth-point sampling pattern.
                fast_indices=np.flatnonzero(fast);normal_indices=np.flatnonzero(~fast)
                fast_stride=max(1,math.ceil(len(fast_indices)/280));chosen_fast=fast_indices[::fast_stride]
                budget=max(0,280-len(chosen_fast))
                normal_stride=max(1,math.ceil(len(normal_indices)/max(1,budget)))
                chosen_normal=normal_indices[::normal_stride][:budget]
                for j in np.r_[chosen_normal,chosen_fast]:
                    p,v=points[j],vectors[j]
                    color=fast_color if fast[j] else '#28c6e3'
                    x,y=float(p[0])*sx,float(p[1])*sy;dx,dy=float(v[0])*vx,float(v[1])*vy
                    if not all(math.isfinite(a) for a in (x,y,dx,dy)):continue
                    end=(x+dx,y+dy);draw.line((x,y,*end),fill=color,width=2)
                    length=math.hypot(dx,dy)
                    if length>3:
                        ux,uy=dx/length,dy/length
                        draw.polygon([end,(end[0]-4*ux+2*uy,end[1]-4*uy-2*ux),
                            (end[0]-4*ux-2*uy,end[1]-4*uy+2*ux)],fill=color)
        available=(max(1,self.screen.winfo_width()-2),max(1,self.screen.winfo_height()-2))
        image.thumbnail(available)
        photo=ImageTk.PhotoImage(image,master=self.window)
        self.screen.configure(image=photo,text=tr(''))
        self.photo=photo

    def _display_vectors(self,index):
        """Use the very same predicted vectors/IDs that the metric evaluates."""
        steps=self.analysis.get('steps',[]) if self.analysis else []
        empty=(np.empty((0,2),np.float32),np.empty((0,2),np.float32),np.empty(0,dtype=np.int64))
        if not 0<=index<len(steps):return empty
        step=steps[index]
        metrics=self.model.get('metrics',[]) if self.model else []
        if index>=len(metrics):return step['p'],step['v'],step['ids']
        metric=metrics[index]
        if 'vector_ids' not in metric or 'vectors' not in metric:
            return empty if metric.get('occupied') else (step['p'],step['v'],step['ids'])
        vector_ids=np.asarray(metric['vector_ids']);vectors=np.asarray(metric['vectors'])
        if not len(vector_ids):return empty
        ids,point_indices,vector_indices=np.intersect1d(step['ids'],vector_ids,assume_unique=True,return_indices=True)
        return step['p'][point_indices],vectors[vector_indices],ids

    def _fast_points(self,index,ids):
        """Both candidate types use shared fast IDs, regardless of frame count."""
        flags=np.zeros(len(ids),dtype=bool)
        metrics=self.model.get('metrics',[]) if self.model else []
        if not 0<=index<len(metrics):return flags
        metric=metrics[index];event=self._event_at(index+2)
        if not event or not event.get('included',True) or not (event.get('jump') or event.get('predicted_peak')):return flags
        return np.isin(ids,motion.fast_feature_ids(metric,self.threshold))

    def _fast_color(self,index):
        event=self._event_at(index+2)
        return '#9488b4' if event and event.get('predicted_peak') else '#ed7956'

    def _raw_fast_points(self,index):
        """Compatibility alias; colors now follow shared predicted statistics."""
        steps=self.analysis.get('steps',[]) if self.analysis else []
        if not 0<=index<len(steps):return np.zeros(0,dtype=bool)
        return self._fast_points(index,steps[index]['ids'])

    def _draw_plot(self):
        if not self.is_open() or self.plot is None:return
        c=self.plot;c.delete('all');w=max(40,c.winfo_width());h=max(30,c.winfo_height());x0,x1=10,w-10;y0,y1=12,h-13
        cell=(x1-x0)/max(1,self.frames-1)
        for frame,excluded in hold_frame_marks(self.model,self.rows):
            x=x0+(frame-1)/max(1,self.frames-1)*(x1-x0)
            c.create_rectangle(max(x0,x-cell*.44),y0,min(x1,x+cell*.44),y1,
                fill='#fff5f8' if excluded else '#fbe7ee',outline='#edceda' if excluded else '',
                dash=(3,3) if excluded else (),tags=('hold_frame',f'hold_frame:{frame}'))
        c.create_line(x0,y1,x1,y1,fill=PALETTE['border'])
        metrics=self.model.get('metrics',[]) if self.model else []
        maximum=motion_graph_limit(metrics)
        if metrics:
            y=y1-min(maximum,self.threshold)/maximum*(y1-y0)
            c.create_line(x0,y,x1,y,fill='#df902c',dash=(4,3))
            stride=max(1,math.ceil(len(metrics)/650))
            for start in range(0,len(metrics),stride):
                metric=max(metrics[start:start+stride],key=lambda m:m['score']);f=metric['end']
                x=x0+(f-1)/max(1,self.frames-1)*(x1-x0);y=y1-min(maximum,metric['score'])/maximum*(y1-y0)
                if is_resolved_jump(self.model,metric,self.threshold):
                    raw_y=y1-min(maximum,metric['raw_score'])/maximum*(y1-y0)
                    c.create_line(x,y1,x,raw_y,fill='#c8bfe8',width=2,dash=(2,3),tags=('resolved_stem',f'resolved_stem:{f}'))
                    c.create_oval(x-3,raw_y-3,x+3,raw_y+3,fill='#c8bfe8',outline='',tags=('resolved_jump',f'resolved_jump:{f}'))
                event=self._event_at(f);excluded=event and not event.get('included',True)
                actionable=event and event.get('jump',event.get('kind')=='jump')
                predicted=event and event.get('predicted_peak',False)
                color='#99a5b3' if excluded else '#827d9f' if predicted else '#dd7e26' if actionable else '#188fbe' if metric.get('occupied') else '#518bab'
                c.create_line(x,y1,x,y,fill=color,width=2,dash=(3,3) if predicted else ())
                tags=(('jump_candidate',) if actionable else ())+(('prediction',f'prediction:{f}') if predicted else ())
                c.create_oval(x-2,y-2,x+2,y+2,fill='white' if excluded or predicted else color,outline=color,tags=tags)
            c.create_text(x0,3,anchor='nw',text=tr('분홍: 반복 프레임 · 연보라: 보정 후 해소 예상'),fill=PALETTE['muted'],font=('맑은 고딕',8))
        else:
            for f in range(1,self.frames+1,max(1,math.ceil(self.frames/300))):
                x=x0+(f-1)/max(1,self.frames-1)*(x1-x0);c.create_oval(x-2,y1-2,x+2,y1+2,fill='#518bab',outline='')
        self._cursor()

    def _cursor(self):
        if not self.is_open() or self.plot is None or self.analysis is None:return
        self.plot.delete('cursor');w=max(40,self.plot.winfo_width());h=max(30,self.plot.winfo_height())
        x=10+(max(1,self.current_frame)-1)/max(1,self.frames-1)*(w-20)
        self.plot.create_line(x,15,x,h-5,fill=PALETTE['primary'],width=2,tags='cursor')
        self.plot.create_text(x,15,text=tr('▼'),fill=PALETTE['primary'],font=('맑은 고딕',8),anchor='n',tags='cursor')

    def _pump(self):
        if not self.is_open():return
        job=self.decoder
        if job is not None:
            for _ in range(8):
                ahead=sum(1 for f in self.cache if f>self.current_frame)
                if self.pending_frame is None and ahead>=self.PREFETCH_FRAMES:break
                try:event=job.events.get_nowait()
                except queue.Empty:break
                if event[0]=='frame':
                    f,data=event[1:];self.received=max(self.received,f);self.cache[f]=data
                    while len(self.cache)>self.CACHE_FRAMES:self.cache.popitem(last=False)
                elif event[0]=='error':
                    self.pause();self.pending_frame=None;self.buffering=False;self._set_error('검토 재생 오류: '+event[1])
                elif event[0]=='end':
                    if self.pending_frame is not None and self.pending_frame>event[1]:
                        self.pause();self.pending_frame=None;self.buffering=False
                        self._set_error('요청한 프레임을 읽지 못했습니다. 원본과 분석 프레임 수를 확인하세요.')
        if self.pending_frame is not None and self.pending_frame in self.cache:
            target=self.pending_frame;self.pending_frame=None;self._display(target)
            self.next_due=time.monotonic()+self._period()
        now=time.monotonic()
        if self.playing and self.pending_frame is None and self.current_frame and now>=self.next_due:
            if self.current_frame>=self.frames:
                if self.loop.get():self.seek(1,play=True)
                else:self.pause()
            else:
                self._request(self.current_frame+1)
                self.next_due=max(self.next_due+self._period(),now)
        self.after_id=self.window.after(10 if self.analysis is not None else 60,self._pump)

