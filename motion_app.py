"""Light desktop UI for motion-based hold and jump review."""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES, COPY, REFUSE_DROP
import numpy as np
import hold_core as core
import motion_core as motion
import sdk_bridge
from sdk_worker import validate_output_paths
from report_io import validate_report_path, write_report
from review_player import ReviewPlayer, hold_frame_marks, is_resolved_jump, event_for_frame
from motion_settings import default_min_move, valid_min_move
from ui_support import attach_tooltip, apply_design_style, motion_graph_limit, PALETTE
from detection_help import show_detection_help
from sdk_settings_dialog import show_sdk_settings
from manual_replacement_dialog import show_manual_replacement
from graph_viewport import FrameViewport
from i18n import load_language, set_language
from app_settings_dialog import show_app_settings


class CategoryLabel(tk.Text):
    """Wrapping label with only the correction-aware category phrase accented."""
    def __init__(self,parent,text='',textvariable=None,**kwargs):
        super().__init__(parent,width=1,height=1,wrap='word',borderwidth=0,highlightthickness=0,
            padx=0,pady=0,takefocus=False,cursor='arrow',background='white',
            font=kwargs.pop('font',('맑은 고딕',10)),**kwargs)
        self.tag_configure('category',foreground='#b85050');self._variable=textvariable;self._pending=None
        if textvariable is not None:textvariable.trace_add('write',lambda *_:self.set_text(textvariable.get()))
        self.bind('<Configure>',self._fit);self.bind('<Button-1>',lambda e:'break')
        self.bind('<B1-Motion>',lambda e:'break');self.set_text(textvariable.get() if textvariable is not None else text)

    def set_text(self,text):
        self.configure(state='normal');self.delete('1.0','end');self.insert('1.0',text)
        for phrase in ('보정 반영 점프','보정 반영','Correction-aware Jump','Correction-aware'):
            start='1.0'
            while True:
                start=self.search(phrase,start,stopindex='end')
                if not start:break
                end=f'{start}+{len(phrase)}c';self.tag_add('category',start,end);start=end
        self.configure(state='disabled');self._fit()

    def _fit(self,event=None):
        if self._pending is not None:self.after_cancel(self._pending)
        self._pending=self.after_idle(self._fit_height)

    def _fit_height(self):
        self._pending=None
        lines=self.count('1.0','end-1c','displaylines')
        height=max(1,(lines[0] if lines else 0)+1)
        if int(self.cget('height'))!=height:self.configure(height=height)


def test_video_choices():
    """Optional local testing shortcuts; remove the file to remove the UI."""
    folder=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    try:
        data=json.loads((folder/'test_videos.json').read_text(encoding='utf-8-sig'))
        if not isinstance(data,list):return {}
        return {item['label']:item['path'] for item in data if isinstance(item,dict)
                and isinstance(item.get('label'),str) and item['label'].strip()
                and isinstance(item.get('path'),str) and item['path'].strip()}
    except (OSError,ValueError):return {}


class App:
    def __init__(self,root):
        load_language()
        TkinterDnD.require(root)
        self.root=root;self.busy=False;self.analysis=None;self.model=None;self.rows=[]
        self.excluded=set();self.events=queue.Queue();self.stop=threading.Event()
        self.sdk_ready=False;self.closing=False;self.dragging=False;self.pending=None
        self.sdk_check_generation=0;self.sdk_window=None;self.preferences_window=None
        self.manual_ranges=[];self.manual_window=None
        self.graph_view=FrameViewport();self.graph_panning=False
        self.options_pending=None;self.options_valid=True;self.player=None;self.playhead=1;self.last_selected_key=None
        self.media_generation=0;self.media_after=None;self.media_info=None;self.last_output=None
        root.title(tr('Frame Hold Finder · 모션 분석 1.0'));root.geometry('1440x930');root.minsize(1100,760)
        root.configure(bg=PALETTE['app']);s=ttk.Style();s.theme_use('clam')
        apply_design_style(s)
        s.configure('Graph.Horizontal.TScrollbar',width=12,arrowsize=12,
            background='#9ca3af',troughcolor=PALETTE['track'],bordercolor=PALETTE['track'],
            lightcolor='#9ca3af',darkcolor='#9ca3af')
        s.map('Graph.Horizontal.TScrollbar',background=[('disabled',PALETTE['track']),('active','#71717a')])
        body=ttk.Frame(root,padding=12);body.pack(fill='both',expand=True)

        source_card=ttk.Frame(body,style='Surface.TFrame',padding=(12,10));source_card.pack(fill='x')
        top=ttk.Frame(source_card,style='Surface.TFrame');top.pack(fill='x')
        self.path=tk.StringVar();self.test_videos=test_video_choices();self.test_selector=None
        self.test_choice=tk.StringVar(value='테스트 영상 선택')
        if self.test_videos:
            self.test_selector=ttk.Combobox(top,textvariable=self.test_choice,values=list(self.test_videos),width=15,state='readonly')
            self.test_selector.pack(side='left',padx=(0,8));self.test_selector.bind('<<ComboboxSelected>>',self.choose_test_video)
        self.path_entry=ttk.Entry(top,textvariable=self.path,font=('맑은 고딕',10))
        self.path_entry.pack(side='left',fill='x',expand=True)
        self.path_entry.bind('<Return>',self.open_entered_path)
        self.path_entry.bind('<KP_Enter>',self.open_entered_path)
        self.path_entry.bind('<Control-a>',self.select_path)
        self.path_entry.bind('<Control-A>',self.select_path)
        self.open_button=ttk.Button(top,text=tr('영상 선택'),command=self.choose);self.open_button.pack(side='left',padx=(8,6))
        self.scan_button=ttk.Button(top,text=tr('분석 시작'),style='Primary.TButton',command=self.start);self.scan_button.pack(side='left')
        self.cancel_slot=ttk.Frame(top,style='Surface.TFrame',width=54,height=34)
        self.cancel_slot.pack(side='left',padx=(6,0));self.cancel_slot.pack_propagate(False)
        self.cancel_button=ttk.Button(self.cancel_slot,text=tr('취소'),command=self.cancel,state='disabled',style='Tertiary.TButton')
        self.tools_button=ttk.Menubutton(top,text=tr('더보기'),style='Tools.TMenubutton')
        self.tools_button.pack(side='left',padx=(4,0))
        self.tools_menu=tk.Menu(self.tools_button,tearoff=False)
        self.tools_button.configure(menu=self.tools_menu)
        self.tool_items={}
        for key,label,command in (
            ('preferences','설정…',self.preferences),
            ('sdk','SDK 설정…',self.choose_sdk),
            ('result','최근 결과 열기',self.open_result),('folder','결과 폴더 열기',self.open_result_folder),
            ('path','파일 경로 복사',self.copy_source_path),
            ('exclusions','제외 초기화',self.reset_exclusions),
            ('defaults','기준 초기화',self.reset_analysis_defaults),('help','감지 도움말',self.settings)):
            if key in ('path','exclusions'):self.tools_menu.add_separator()
            self.tools_menu.add_command(label=tr(label),command=command)
            self.tool_items[key]=self.tools_menu.index('end')

        settings_card=ttk.Frame(body);settings_card.pack(fill='x',pady=(8,8))
        self.settings_card=settings_card
        self.settings_groups={};self.settings_boxes={}
        for column,name,weight in ((0,'전체 설정',1),(2,'홀드 설정',1),(4,'점프 설정',3)):
            settings_card.columnconfigure(column,weight=weight)
            border=tk.Frame(settings_card,background=PALETTE['surface'],highlightthickness=1,
                highlightbackground=PALETTE['border'],borderwidth=0)
            border.grid(row=0,column=column,sticky='nsew')
            group=ttk.Frame(border,style='Surface.TFrame',padding=(12,9));group.pack(fill='both',expand=True)
            self.settings_groups[name]=group
            self.settings_boxes[name]=border
        for column in (1,3):settings_card.columnconfigure(column,minsize=8)
        general=self.settings_groups['전체 설정'];hold=self.settings_groups['홀드 설정'];jump=self.settings_groups['점프 설정']
        self.holds=tk.BooleanVar(value=True);self.jumps=tk.BooleanVar(value=True)
        ttk.Label(general,text=tr('전체 설정'),style='Section.TLabel',font=('맑은 고딕',10,'bold')).pack(anchor='w',pady=(0,7))
        general_row=ttk.Frame(general,style='Surface.TFrame');general_row.pack(fill='x')
        ttk.Label(general_row,text=tr('분석 폭'),style='Surface.TLabel').pack(side='left',padx=(0,7));self.res=tk.StringVar(value='960')
        self.res_box=ttk.Combobox(general_row,textvariable=self.res,values=['640','960','1280'],width=6,state='readonly');self.res_box.pack(side='left')
        self.res_box.bind('<<ComboboxSelected>>',self.invalidate)
        ttk.Label(general_row,text=tr('px'),style='Muted.TLabel').pack(side='left',padx=(4,0))
        hold_heading=ttk.Frame(hold,style='Surface.TFrame');hold_heading.pack(fill='x',pady=(0,7))
        ttk.Label(hold_heading,text=tr('홀드 설정'),style='Section.TLabel',font=('맑은 고딕',10,'bold')).pack(side='left')
        self.hold_check=ttk.Checkbutton(hold_heading,text=tr('감지'),style='Surface.TCheckbutton',variable=self.holds,command=self.rebuild);self.hold_check.pack(side='left',padx=(14,0))
        jump_heading=ttk.Frame(jump,style='Surface.TFrame');jump_heading.pack(fill='x',pady=(0,7))
        jump_title=ttk.Label(jump_heading,text=tr('점프 설정'),style='Section.TLabel',font=('맑은 고딕',10,'bold'));jump_title.pack(side='left')
        self.jump_check=ttk.Checkbutton(jump_heading,text=tr('감지'),style='Surface.TCheckbutton',variable=self.jumps,command=self.refresh);self.jump_check.pack(side='left',padx=(14,0))
        attach_tooltip(jump_title,'점프 검출과 그래프 점수에 적용합니다.\n홀드 보정 후 남는 점프 평가에도 같은 기준을 사용합니다.')
        self.quiet_px=.5;self.min_move=default_min_move(int(self.res.get()));self.support_percent=30.;self.min_points=20
        self.min_move_auto=True;self._setting_min_move=False
        self.minimum_source_path='';self.minimum_width_choice=self.res.get()
        self.quiet_var=tk.StringVar(value='0.5');self.min_move_var=tk.StringVar(value=f'{self.min_move:g}');self.support_var=tk.StringVar(value='30')
        self.min_points_var=tk.StringVar(value='20');self.update_minimum_motion()
        hold_row=ttk.Frame(hold,style='Surface.TFrame');hold_row.pack(fill='x')
        ttk.Label(hold_row,text=tr('허용 이동'),style='Surface.TLabel').pack(side='left')
        self.quiet_entry=ttk.Spinbox(hold_row,from_=0,to=100,increment=.1,textvariable=self.quiet_var,width=6)
        self.quiet_entry.pack(side='left',padx=(7,4));ttk.Label(hold_row,text=tr('원본 px 이하'),style='Muted.TLabel').pack(side='left')
        jump_fields=ttk.Frame(jump,style='Surface.TFrame');jump_fields.pack(fill='x')
        jump_fields.columnconfigure(0,weight=1);jump_fields.columnconfigure(1,weight=1)
        speed_row=ttk.Frame(jump_fields,style='Surface.TFrame');speed_row.grid(row=0,column=0,sticky='w',padx=(0,18))
        ttk.Label(speed_row,text=tr('속도 기준'),style='Surface.TLabel',width=11).pack(side='left');self.threshold=tk.StringVar(value='5')
        self.threshold_entry=ttk.Spinbox(speed_row,from_=0,to=1000,increment=1,textvariable=self.threshold,width=6);self.threshold_entry.pack(side='left',padx=(0,4))
        ttk.Label(speed_row,text=tr('% 초과'),style='Muted.TLabel').pack(side='left');self.threshold_entry.bind('<Return>',lambda e:self.refresh())
        self.threshold.trace_add('write',self.schedule_refresh)
        movement_row=ttk.Frame(jump_fields,style='Surface.TFrame');movement_row.grid(row=0,column=1,sticky='w')
        ttk.Label(movement_row,text=tr('최소 기준 이동'),style='Surface.TLabel',width=13).pack(side='left')
        self.min_move_entry=ttk.Spinbox(movement_row,from_=.000001,to=10000,increment=.1,textvariable=self.min_move_var,width=6)
        self.min_move_entry.pack(side='left',padx=(0,4));ttk.Label(movement_row,text=tr('px'),style='Muted.TLabel').pack(side='left')
        support_row=ttk.Frame(jump_fields,style='Surface.TFrame');support_row.grid(row=1,column=0,sticky='w',padx=(0,18),pady=(6,0))
        ttk.Label(support_row,text=tr('추적점 비율'),style='Surface.TLabel',width=11).pack(side='left')
        self.support_entry=ttk.Spinbox(support_row,from_=1,to=100,increment=1,textvariable=self.support_var,width=6);self.support_entry.pack(side='left')
        ttk.Label(support_row,text=tr('% 이상'),style='Muted.TLabel').pack(side='left',padx=(4,0))
        points_row=ttk.Frame(jump_fields,style='Surface.TFrame');points_row.grid(row=1,column=1,sticky='w',pady=(6,0))
        ttk.Label(points_row,text=tr('최소 추적점'),style='Surface.TLabel',width=13).pack(side='left')
        self.min_points_entry=ttk.Spinbox(points_row,from_=1,to=10000,increment=1,textvariable=self.min_points_var,width=6);self.min_points_entry.pack(side='left')
        ttk.Label(points_row,text=tr('개'),style='Muted.TLabel').pack(side='left',padx=(4,0))
        for entry in (self.quiet_entry,self.min_move_entry,self.support_entry,self.min_points_entry):entry.bind('<Return>',lambda e:self.apply_analysis_options())

        # Reserve actions and status before the expandable review workspace.
        footer=ttk.Frame(body,style='Surface.TFrame',padding=(12,7));footer.pack(side='bottom',fill='x',pady=(8,0))
        bottom=ttk.Frame(footer,style='Surface.TFrame');bottom.pack(fill='x')
        self.save_summary=DisplayVar(value='')
        self.save_summary_label=ttk.Label(bottom,textvariable=self.save_summary,style='Surface.TLabel')
        self.save_summary_label.pack(side='left',fill='x',expand=True)
        self.render_button=ttk.Button(bottom,text=tr('보정 영상 저장'),style='Primary.TButton',command=self.render);self.render_button.pack(side='right')
        self.quality=tk.StringVar(value='HIGH')
        self.quality_box=ttk.Combobox(bottom,textvariable=self.quality,values=['HIGH','MEDIUM','LOW'],width=8,state='readonly');self.quality_box.pack(side='right',padx=(7,12))
        ttk.Label(bottom,text=tr('품질'),style='Muted.TLabel').pack(side='right')
        self.status=DisplayVar(value='')
        self.sdk_status=DisplayVar(value='NVIDIA SDK 확인 중…')
        self.status_host=ttk.Frame(footer,style='Surface.TFrame',height=22);self.status_host.pack(fill='x',pady=(3,0));self.status_host.pack_propagate(False)
        self.status_label=ttk.Label(self.status_host,textvariable=self.status,style='Muted.TLabel',anchor='w');self.status_label.pack(side='left',fill='both',expand=True)
        progress_host=ttk.Frame(footer,style='Surface.TFrame',height=3)
        progress_host.pack(fill='x',pady=(2,0));progress_host.pack_propagate(False)
        self.bar=ttk.Progressbar(progress_host,length=1);self.bar.pack(fill='both',expand=True)

        self.workspace=ttk.Panedwindow(body,orient='horizontal');self.workspace.pack(fill='both',expand=True)
        self.player=ReviewPlayer(self.workspace,analysis=None,on_frame=self.player_frame,embedded=True,show_plot=False)
        review=ttk.Frame(self.workspace,style='Surface.TFrame',padding=(12,10));self.review_panel=review
        self.workspace.add(self.player.widget,weight=56);self.workspace.add(review,weight=44)
        root.after_idle(lambda:self.workspace.sashpos(0,int(self.workspace.winfo_width()*.56)))
        graph_heading=ttk.Frame(review,style='Surface.TFrame');graph_heading.pack(fill='x',pady=(0,4))
        self.summary=DisplayVar(value='검출 구간')
        self.summary_label=ttk.Label(graph_heading,textvariable=self.summary,style='Section.TLabel',font=('맑은 고딕',11,'bold'))
        self.summary_label.pack(side='left',fill='x',expand=True)
        self.add_range_button=ttk.Button(graph_heading,text=tr('추가'),style='Compact.TButton',command=self.open_manual_range)
        self.add_range_button.pack(side='left',padx=(0,8))
        self.previous_candidate_button=ttk.Button(graph_heading,text=tr('‹'),width=3,style='Compact.TButton',command=lambda:self.nav_candidate(-1));self.previous_candidate_button.pack(side='left')
        self.next_candidate_button=ttk.Button(graph_heading,text=tr('›'),width=3,style='Compact.TButton',command=lambda:self.nav_candidate(1));self.next_candidate_button.pack(side='left',padx=(3,0))
        self.graph=tk.Canvas(review,height=115,bg='white',highlightthickness=0)
        self.graph.pack(fill='x');self.graph.bind('<Configure>',lambda e:self.draw_graph())
        self.graph.bind('<Button-1>',self.graph_down);self.graph.bind('<B1-Motion>',self.graph_drag)
        self.graph.bind('<ButtonRelease-1>',lambda e:setattr(self,'dragging',False))
        self.graph.bind('<Motion>',self.graph_cursor)
        self.graph.bind('<Leave>',lambda e:self.graph.configure(cursor=''))
        self.graph.bind('<MouseWheel>',self.graph_wheel)
        self.graph.bind('<Button-4>',self.graph_wheel);self.graph.bind('<Button-5>',self.graph_wheel)
        self.graph.bind('<Button-2>',self.graph_pan_start);self.graph.bind('<B2-Motion>',self.graph_pan_drag)
        self.graph.bind('<ButtonRelease-2>',lambda e:setattr(self,'graph_panning',False))
        graph_navigation=ttk.Frame(review,style='Surface.TFrame')
        graph_navigation.pack(fill='x',pady=(0,2))
        self.graph_range=tk.StringVar(value='—')
        ttk.Label(graph_navigation,textvariable=self.graph_range,style='Muted.TLabel',width=13,font=('맑은 고딕',9)).pack(side='left')
        self.graph_fit_button=ttk.Button(graph_navigation,text=tr('전체'),width=4,style='Compact.TButton',command=self.graph_fit)
        self.graph_fit_button.pack(side='right',padx=(3,0))
        self.graph_zoom_in_button=ttk.Button(graph_navigation,text=tr('+'),width=2,style='Compact.TButton',command=lambda:self.graph_zoom(1.5))
        self.graph_zoom_in_button.pack(side='right',padx=(3,0))
        self.graph_zoom_out_button=ttk.Button(graph_navigation,text=tr('−'),width=2,style='Compact.TButton',command=lambda:self.graph_zoom(1/1.5))
        self.graph_zoom_out_button.pack(side='right',padx=(4,0))
        self.graph_scrollbar=ttk.Scrollbar(graph_navigation,orient='horizontal',style='Graph.Horizontal.TScrollbar',command=self.graph_scroll,takefocus=True)
        self.graph_scrollbar.pack(side='left',fill='x',expand=True)
        self.graph_hint=DisplayVar();self.update_graph_hint()
        self.graph_hint_label=CategoryLabel(review,textvariable=self.graph_hint,foreground=PALETTE['muted'],font=('맑은 고딕',9));self.graph_hint_label.pack(fill='x',pady=(0,6))
        detail=ttk.Frame(review,style='Surface.TFrame',height=50);self.detail_host=detail
        detail.pack(side='bottom',fill='x',pady=(5,0));detail.pack_propagate(False)
        self.toggle_button=ttk.Button(detail,text=tr('선택 제외'),style='Tertiary.TButton',command=self.toggle_selected)
        self.toggle_button.pack(side='right',padx=(8,0))
        self.delete_range_button=ttk.Button(detail,text=tr('삭제'),style='Compact.TButton',command=self.delete_manual_ranges)
        self.detail=DisplayVar(value='')
        self.detail_label=ttk.Label(detail,textvariable=self.detail,style='Muted.TLabel',anchor='w',width=1);self.detail_label.pack(side='left',fill='both',expand=True)
        review.bind('<Configure>',self.resize_review)
        tableframe=ttk.Frame(review,style='Surface.TFrame');tableframe.pack(fill='both',expand=True,pady=(7,0))
        tableframe.rowconfigure(0,weight=1);tableframe.columnconfigure(0,weight=1)
        cols=('include','kind','range','speed','plan','anchors','generated')
        self.table=ttk.Treeview(tableframe,columns=cols,displaycolumns=('include','range','kind','anchors','generated'),show='headings',height=5,selectmode='extended')
        for c,t,w,minimum in (
                ('include','포함',42,38),('kind','유형',132,116),('range','원본 구간',88,78),
                ('speed','속도',75,75),('plan','교체 대상',165,165),
                ('anchors','참조 프레임',92,82),('generated','생성 프레임',92,82)):
            self.table.heading(c,text=tr(t));self.table.column(c,width=w,minwidth=minimum,anchor='center',stretch=c!='include')
        self.table.tag_configure('excluded',foreground=PALETTE['excluded']);self.table.tag_configure('prediction',foreground='#675880');self.table.tag_configure('jump',foreground='#a76021')
        self.table.tag_configure('blocked',foreground=PALETTE['disabled_fg'],background=PALETTE['subtle'])
        self.table.tag_configure('manual',foreground=PALETTE['primary'])
        self.kind_overlays=[];self.kind_overlay_pending=None
        scroll=ttk.Scrollbar(tableframe,orient='vertical',command=self.table.yview)
        self.table_xscroll=ttk.Scrollbar(tableframe,orient='horizontal',command=self.table.xview)
        self.table.configure(yscrollcommand=lambda first,last:(scroll.set(first,last),self.schedule_kind_overlays()),
            xscrollcommand=lambda first,last:(self.table_xscroll.set(first,last),self.schedule_kind_overlays()))
        self.table.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
        self.empty_results=ttk.Label(tableframe,text=tr('분석 후 검출 구간이 표시됩니다.'),style='Muted.TLabel',anchor='center')
        self.empty_results.place(relx=.5,rely=.4,anchor='center')
        self.table.bind('<<TreeviewSelect>>',self.selected);self.table.bind('<Button-1>',self.table_click)
        for event in ('<Configure>','<<TreeviewSelect>>','<B1-Motion>','<ButtonRelease-1>'):self.table.bind(event,self.schedule_kind_overlays,add='+')

        attach_tooltip(self.path_entry,lambda:self.path.get() or '검토할 영상을 선택하세요.')
        attach_tooltip(self.open_button,'영상 선택 · Ctrl+O');attach_tooltip(self.scan_button,'모션 분석 시작 · Ctrl+Enter')
        attach_tooltip(self.threshold_entry,'같은 점의 앞뒤 이동량보다 모두 이 비율 이상 빨라야 점프 후보입니다.')
        attach_tooltip(self.quiet_entry,'홀드로 판단할 정지 허용 이동량입니다. 원본 픽셀 기준이며 기본값은 0.5px입니다.')
        attach_tooltip(self.min_move_entry,lambda:f'점프 평가에 사용할 최소 이동량(원본 px). 새 영상·분석 폭 선택 시 기본값 {self.min_move_default:g}px. 직접 입력값은 그대로 사용합니다.')
        attach_tooltip(self.support_entry,'유효 추적점 중 속도 기준을 넘는 점의 비율입니다. 최소 추적점 조건도 함께 만족해야 합니다.')
        attach_tooltip(self.min_points_entry,'속도 기준을 넘어야 하는 실제 추적점의 최소 개수입니다.')
        attach_tooltip(self.toggle_button,'Ctrl/Shift로 여러 항목을 선택해 함께 제외하거나 포함할 수 있습니다.')
        attach_tooltip(self.summary_label,lambda:getattr(self,'summary_detail',''))
        attach_tooltip(self.render_button,'포함된 교체 대상을 저장합니다. 원본 프레임 수·FPS·길이를 유지하고 오디오를 복사합니다. NVIDIA SDK가 필요합니다.')
        attach_tooltip(self.status_label,lambda:self.status.get())
        attach_tooltip(self.detail_label,lambda:self.detail.get())
        attach_tooltip(self.previous_candidate_button,'이전 검출 구간')
        attach_tooltip(self.next_candidate_button,'다음 검출 구간')
        self.path.trace_add('write',self.invalidate);root.after(80,self.pump);root.after(150,self.check_sdk)
        for var in (self.quiet_var,self.support_var,self.min_points_var):var.trace_add('write',self.schedule_options)
        self.min_move_var.trace_add('write',self.minimum_motion_edited)
        root.bind('<Control-o>',lambda e:self.shortcut(self.choose));root.bind('<Control-Return>',lambda e:self.shortcut(self.start))
        self.playback_key_tag='WorkspacePlayback_'+str(id(self))
        root.bind_class(self.playback_key_tag,'<space>',self.playback_shortcut)
        self.focus_order=[self.test_selector,self.path_entry,self.open_button,self.scan_button,self.cancel_button,self.tools_button,
            self.res_box,self.hold_check,self.quiet_entry,
            self.jump_check,self.threshold_entry,self.min_move_entry,self.support_entry,self.min_points_entry,
            self.player.screen,self.player.timeline,self.player.speed_box,self.player.loop_check,self.player.vector_check,
            self.player.previous_button,self.player.play_button,self.player.next_button,self.player.frame_entry,
            self.add_range_button,self.previous_candidate_button,self.next_candidate_button,
            self.graph_scrollbar,self.graph_zoom_out_button,self.graph_zoom_in_button,self.graph_fit_button,
            self.table,self.toggle_button,self.delete_range_button,self.quality_box,self.render_button]
        root.bind_class(self.playback_key_tag,'<Tab>',lambda e:self.move_focus(e,1))
        root.bind_class(self.playback_key_tag,'<Shift-Tab>',lambda e:self.move_focus(e,-1))
        self.bind_playback_shortcut(root)
        self.drop_targets=(source_card,top,self.path_entry,self.player.screen_host,self.player.screen)
        for target in self.drop_targets:
            target.drop_target_register(DND_FILES)
            target.dnd_bind('<<DropEnter>>',self.drop_action)
            target.dnd_bind('<<DropPosition>>',self.drop_action)
            target.dnd_bind('<<Drop>>',self.drop_video)
        root.protocol('WM_DELETE_WINDOW',self.close);self.update_buttons()


    def choose(self):
        if self.busy:return
        p=filedialog.askopenfilename(filetypes=tr([('영상','*.mov *.mp4 *.mxf *.mkv *.avi *.webm'),('모든 파일','*.*')]))
        if p:self.path.set(p);self.path_entry.xview_moveto(0)

    def select_path(self,_event=None):
        if not self.busy:
            self.path_entry.selection_range(0,'end');self.path_entry.icursor('end')
        return 'break'

    def drop_action(self,_event=None):
        if self.busy or self.closing or self.root.grab_current() is not None:return REFUSE_DROP
        return COPY

    def drop_video(self,event):
        if self.drop_action()==REFUSE_DROP:return REFUSE_DROP
        try:paths=self.root.tk.splitlist(event.data)
        except tk.TclError:paths=()
        if len(paths)!=1:
            self.status.set('영상은 한 번에 하나씩 드롭해 주세요.');return REFUSE_DROP
        path=Path(paths[0])
        video_types={'.mov','.mp4','.mxf','.mkv','.avi','.webm','.m4v','.mpg','.mpeg','.wmv','.mts','.m2ts','.ts','.vob','.ogv'}
        try:valid=path.suffix.lower() in video_types and path.is_file()
        except OSError:valid=False
        if not valid:
            self.status.set('영상 파일을 드롭해 주세요.');return REFUSE_DROP
        self.path.set(str(path));self.path_entry.xview_moveto(0)
        if self.media_after:self.root.after_cancel(self.media_after);self.media_after=None
        self.load_player_source()
        return COPY

    def open_entered_path(self,event=None):
        # Ctrl+Enter keeps its existing "analyze" command on the root binding.
        if event is not None and event.state & 0x0004:return
        if self.busy:return 'break'
        path=self.path.get().strip().strip('"')
        if path!=self.path.get():self.path.set(path)
        if self.media_after:self.root.after_cancel(self.media_after);self.media_after=None
        if not path:self.status.set('영상 경로를 입력하거나 파일을 선택하세요.');return 'break'
        if not Path(path).is_file():self.status.set('파일을 찾을 수 없습니다. 입력한 경로를 확인하세요.');return 'break'
        if self.analysis is None:self.load_player_source()
        return 'break'

    def copy_source_path(self):
        if not self.path.get():return
        self.root.clipboard_clear();self.root.clipboard_append(self.path.get())
        self.status.set('파일 경로를 복사했습니다.')

    def resize_review(self,event):
        height=100 if event.height<500 else 115
        if int(self.graph.cget('height'))!=height:self.graph.configure(height=height)

    def shortcut(self,command):
        if not self.busy:command()
        return 'break'

    def bind_playback_shortcut(self,widget):
        if widget.winfo_class()=='Menu':return
        tags=list(widget.bindtags())
        if self.playback_key_tag not in tags:
            tags.insert(1,self.playback_key_tag);widget.bindtags(tuple(tags))
        for child in widget.winfo_children():self.bind_playback_shortcut(child)

    def playback_shortcut(self,event):
        if event.widget.winfo_class() in ('Entry','TEntry','TSpinbox','Spinbox','TCombobox','Text','Menubutton','TMenubutton','Menu'):
            return
        if event.state & 0x000c:return
        if self.player and self.player.is_open():self.player.toggle()
        return 'break'

    def move_focus(self,event,direction):
        available=[w for w in self.focus_order if w is not None and w.winfo_exists() and w.winfo_ismapped()
                   and not (hasattr(w,'instate') and w.instate(['disabled']))]
        if not available:return 'break'
        try:index=available.index(event.widget);target=available[(index+direction)%len(available)]
        except ValueError:target=available[0 if direction>0 else -1]
        target.focus_set()
        return 'break'

    def nav_candidate(self,direction):
        if self.busy:return
        keys=list(self.table.get_children())
        if not keys:return
        selected=[keys.index(key) for key in self.table.selection() if key in keys]
        if selected:
            index=(max(selected)+1 if direction>0 else min(selected)-1)%len(keys)
        else:index=0 if direction>0 else len(keys)-1
        key=keys[index]
        self.table.selection_set(key);self.table.focus(key);self.table.see(key)
        self.last_selected_key=None;self.selected();self.schedule_kind_overlays()
        return key

    def reset_analysis_defaults(self):
        if self.busy:return
        self.threshold.set('5');self.quiet_var.set('0.5');self.support_var.set('30');self.min_points_var.set('20')
        self.min_move_auto=True;self.update_minimum_motion()
        if self.pending:self.root.after_cancel(self.pending);self.pending=None
        if not self.apply_analysis_options(rebuild=False):return
        if self.analysis is not None:self.rebuild()
        else:self.draw_graph();self.update_buttons()
        self.status.set(f'기준 초기화 · 최소 기준 이동 {self.min_move:g}px · 감지 모드·분석 폭·제외 항목 유지')

    def open_result(self):
        return self._open_saved_result(folder=False)

    def open_result_folder(self):
        return self._open_saved_result(folder=True)

    def _open_saved_result(self,folder=False):
        if self.busy or not self.last_output:return False
        path=Path(self.last_output)
        if not path.is_file():
            self.status.set('저장한 결과 파일을 찾을 수 없습니다.');self.update_buttons();return False
        try:os.startfile(str(path.parent if folder else path))
        except OSError as error:
            self.status.set(f'열기 실패 · {error}');return False
        return True

    def schedule_kind_overlays(self,*_):
        if self.kind_overlay_pending is None:
            self.kind_overlay_pending=self.root.after_idle(self.draw_kind_overlays)

    def forward_kind_event(self,event,sequence):
        x=event.x_root-self.table.winfo_rootx();y=event.y_root-self.table.winfo_rooty()
        if sequence=='<Button-1>':self.table.focus_set()
        args=dict(x=x,y=y,state=event.state)
        if sequence=='<MouseWheel>':args['delta']=event.delta
        else:args['time']=event.time
        self.table.event_generate(sequence,**args)
        return 'break'

    def draw_kind_overlays(self):
        self.kind_overlay_pending=None
        if not self.table.winfo_exists():return
        # Only visible rows get labels, so long clips never create one widget per candidate.
        keys=[]
        for y in range(0,self.table.winfo_height(),14):
            key=self.table.identify_row(y)
            if key and key not in keys:keys.append(key)
        predicted={r['key'] for r in self.rows if r.get('predicted_peak')}
        excluded={r['key'] for r in self.rows if not r.get('included',True) or not r.get('repairable',True)}
        selected=set(self.table.selection());visible=[]
        for key in keys:
            bounds=self.table.bbox(key,'kind')
            if key in predicted and bounds:visible.append((key,bounds))
        while len(self.kind_overlays)<len(visible):
            label=tk.Label(self.table,foreground='#b85050',borderwidth=0,highlightthickness=0,
                anchor='center',font=('맑은 고딕',10),takefocus=False)
            for sequence in ('<Button-1>','<ButtonRelease-1>','<B1-Motion>','<MouseWheel>'):
                label.bind(sequence,lambda e,s=sequence:self.forward_kind_event(e,s))
            self.kind_overlays.append(label)
        selected_background=ttk.Style(self.root).lookup('Treeview','background',('selected',))
        for label,(key,(x,y,w,h)) in zip(self.kind_overlays,visible):
            label.configure(text=tr(self.table.set(key,'kind')),foreground=PALETTE['primary_fg'] if key in selected else PALETTE['excluded'] if key in excluded else '#b85050',
                background=selected_background if key in selected else 'white')
            label.place(x=x+1,y=y+1,width=max(1,w-2),height=max(1,h-2));label.lift()
        for label in self.kind_overlays[len(visible):]:label.place_forget()

    def choose_test_video(self,_=None):
        if self.busy:return
        path=self.test_videos.get(self.test_choice.get())
        if path and self.path.get()!=path:self.path.set(path)

    def invalidate(self,*_):
        if self.busy:return
        self.close_manual_window()
        normalized=self.path.get().strip().strip('"').replace('\\','/').casefold()
        if normalized!=self.minimum_source_path or self.res.get()!=self.minimum_width_choice:
            self.min_move_auto=True
        self.minimum_source_path=normalized;self.minimum_width_choice=self.res.get()
        media=self.analysis or self.player.analysis or self.media_info
        same_source=media and str(media['path']).replace('\\','/').casefold()==normalized
        if not same_source:self.manual_ranges=[]
        manual_excluded={r['key'] for r in self.manual_ranges if r['key'] in self.excluded}
        self.media_info=dict(path=media['path'],metadata=media['metadata']) if same_source else None
        label=next((label for label,path in self.test_videos.items()
                    if path.replace('\\','/').casefold()==normalized),'테스트 영상 선택')
        self.test_choice.set(label)
        self.media_generation+=1
        if self.media_after:self.root.after_cancel(self.media_after);self.media_after=None
        self.player.clear();self.playhead=1;self.last_selected_key=None
        self.graph_view.reset();self.graph_panning=False;self.dragging=False
        self.analysis=None;self.model=None;self.rows=[];self.excluded=manual_excluded
        self.update_minimum_motion()
        self.table.delete(*self.table.get_children());self.summary.set('검출 구간')
        self.schedule_kind_overlays()
        self.detail.set('')
        self.draw_graph();self.update_buttons()
        self.status.set('분석 시작을 눌러 검출 구간을 확인하세요.' if self.path.get() else '')
        self.media_after=self.root.after(300,self.load_player_source)

    def load_player_source(self):
        self.media_after=None
        if self.busy or self.closing:return
        token=self.media_generation;path=self.path.get().strip().strip('"')
        if not path:return
        def worker():
            try:
                if not Path(path).is_file():return
                stat=Path(path).stat()
                meta=core.probe(path)
                if meta['estimated']<1:return
                basic=dict(path=path,metadata=meta,frames=meta['estimated'],steps=[],
                    analysis_width=meta['width'],analysis_height=meta['height'],
                    source_stat=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns),manual_only=True)
                self.events.put(('media_ready',token,basic))
            except Exception as e:self.events.put(('media_error',token,str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def update_buttons(self):
        for widget in (self.open_button,self.scan_button,self.hold_check,self.jump_check,self.tools_button,
                       self.threshold_entry,self.toggle_button,
                       self.quiet_entry,self.min_move_entry,self.support_entry,self.min_points_entry):
            widget.configure(state='disabled' if self.busy else 'normal')
        self.path_entry.configure(state='disabled' if self.busy else 'normal')
        analyzed=self.analysis is not None and not self.analysis.get('manual_only')
        self.scan_button.configure(text=tr('재분석' if analyzed else '분석 시작'),
            style='TButton' if analyzed else 'Primary.TButton',
            state='normal' if not self.busy and self.path.get().strip().strip('"') else 'disabled')
        self.open_button.configure(style='Primary.TButton' if not self.path.get().strip().strip('"') else 'TButton')
        if self.test_selector is not None:self.test_selector.configure(state='disabled' if self.busy else 'readonly')
        for widget in (self.res_box,self.quality_box):widget.configure(state='disabled' if self.busy else 'readonly')
        self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        if self.busy:self.cancel_button.pack(fill='both',expand=True)
        else:self.cancel_button.pack_forget()
        valid=self.analysis is not None and not self.busy and self.options_valid and self.options_pending is None
        actions=motion.render_runs(self.rows)
        self.render_button.configure(state='normal' if valid and self.sdk_ready and actions else 'disabled')
        for widget in (self.previous_candidate_button,self.next_candidate_button):
            widget.configure(state='normal' if self.rows and not self.busy else 'disabled')
        selection=self.table.selection()
        selected_rows=[r for r in self.rows if r['key'] in selection]
        toggleable=[r for r in selected_rows if not r.get('blocked_by_manual')]
        self.toggle_button.configure(state='normal' if toggleable and not self.busy else 'disabled',
            text=tr('선택 포함' if toggleable and all(r['key'] in self.excluded for r in toggleable) else '선택 제외'))
        media=self.analysis or (self.player.analysis if self.player else None)
        self.add_range_button.configure(state='normal' if not self.busy and media and media['frames']>=3 else 'disabled')
        if any(r['kind']=='replace' for r in selected_rows):
            self.delete_range_button.pack(side='right',padx=(8,0),before=self.detail_label)
            self.delete_range_button.configure(state='disabled' if self.busy else 'normal')
        else:self.delete_range_button.pack_forget()
        output_ready=not self.busy and self.last_output and Path(self.last_output).is_file()
        states=dict(sdk=not self.busy,result=output_ready,folder=output_ready,
            path=bool(self.path.get()) and not self.busy,exclusions=bool(self.excluded) and not self.busy,
            defaults=not self.busy,help=not self.busy)
        for key,enabled in states.items():
            self.tools_menu.entryconfigure(self.tool_items[key],state='normal' if enabled else 'disabled')
        if self.analysis is None:
            self.save_summary.set('')
        elif not actions:
            self.save_summary.set('교체할 프레임 없음')
        else:
            replaced=len({f for run in actions for f in run['targets']})
            self.save_summary.set(f'{replaced}프레임 교체'+(' · SDK 확인 필요' if not self.sdk_ready else ''))
        if self.rows:self.empty_results.place_forget()
        else:
            self.empty_results.configure(text=tr('현재 기준에서 검출된 구간이 없습니다.' if self.analysis else '분석 후 검출 구간이 표시됩니다.'))
            self.empty_results.place(relx=.5,rely=.4,anchor='center')
        self.update_graph_controls()

    def schedule_refresh(self,*_):
        if self.pending:self.root.after_cancel(self.pending)
        self.pending=self.root.after(70,self.refresh)

    def schedule_options(self,*_):
        if self.options_pending:self.root.after_cancel(self.options_pending)
        self.options_pending=self.root.after(250,self.apply_analysis_options)
        self.update_buttons()

    def minimum_motion_edited(self,*_):
        if self._setting_min_move:return
        self.min_move_auto=False
        self.schedule_options()

    def apply_analysis_options(self,rebuild=True):
        if self.options_pending:self.root.after_cancel(self.options_pending);self.options_pending=None
        if self.busy:return False
        try:
            quiet=float(self.quiet_var.get());minimum=valid_min_move(self.min_move_var.get());support=float(self.support_var.get())
            min_points=motion.validate_min_points(self.min_points_var.get())
            if not math.isfinite(quiet) or quiet<0:raise ValueError()
            if not math.isfinite(support) or not 1<=support<=100:raise ValueError()
        except ValueError:
            self.options_valid=False;self.update_buttons()
            self.status.set('홀드는 0 이상 px, 최소 이동은 0 초과 px, 추적점 비율은 1~100%, 최소 추적점은 1~10000 사이의 정수를 입력하세요.')
            return False
        changed=(quiet,minimum,support,min_points)!=(self.quiet_px,self.min_move,self.support_percent,self.min_points)
        was_invalid=not self.options_valid
        self.options_valid=True;self.quiet_px=quiet;self.min_move=minimum;self.support_percent=support;self.min_points=min_points
        self.update_minimum_motion()
        self.update_graph_hint()
        if changed and rebuild and self.analysis is not None:self.rebuild()
        self.update_buttons()
        if changed or was_invalid:self.status.set('기준이 자동 적용됐습니다.' if self.analysis is not None else '분석 기준을 변경했습니다.')
        return True

    def update_minimum_motion(self):
        # Width only seeds a convenient default. A manually entered pixel
        # value is already in source-pixel units and is never rescaled.
        media=self.analysis or (self.player.analysis if self.player else None) or self.media_info
        source_width=media['metadata']['width'] if media else int(self.res.get())
        analysis_width=self.analysis['analysis_width'] if self.analysis else min(int(self.res.get()),source_width)
        self.min_move_default=default_min_move(analysis_width);self.min_move_analysis_width=analysis_width
        if self.min_move_auto:
            self.min_move=self.min_move_default;self._setting_min_move=True
            try:self.min_move_var.set(f'{self.min_move:g}')
            finally:self._setting_min_move=False
        self.min_move_settings=dict(min_move=self.min_move,min_move_auto=self.min_move_auto,
                                    default_min_move=self.min_move_default,analysis_width=analysis_width)
        return self.min_move_settings

    def update_graph_hint(self):
        self.graph_hint.set('분홍 홀드 · 주황 점프 · 보라 보정 반영')

    def rebuild(self):
        if self.busy or self.analysis is None:return
        if not self.apply_analysis_options(rebuild=False):return
        try:self.model=motion.prepare(self.analysis,self.holds.get(),self.quiet_px,self.min_move,
                                     excluded=self.excluded,support_percent=self.support_percent,min_points=self.min_points,
                                     manual_ranges=self.manual_ranges)
        except Exception as e:self.status.set(str(e));return
        self.refresh()

    def refresh(self):
        self.pending=None
        if self.busy or self.model is None:return
        try:threshold=float(self.threshold.get());rows=motion.candidates(self.model,threshold,self.jumps.get(),excluded=self.excluded)
        except ValueError:self.status.set('점프 기준에는 0 이상의 유한한 숫자를 입력하세요.');self.render_button.configure(state='disabled');return
        selection=self.table.selection();self.rows=rows;self.table.delete(*self.table.get_children())
        # Final grouping determines the actual reference pair shown in each row.
        actions=motion.render_runs(rows)
        for r in rows:
            if r['kind']=='replace':
                kind='추가';span=f'{r["targets"][0]}–{r["targets"][-1]}' if len(r['targets'])>1 else str(r['targets'][0])
            elif r['kind']=='hold':
                kind='홀드+점프' if r.get('jump') else '홀드';span=f'{r["start"]}–{r["end"]}'
            else:
                kind='보정 반영 점프' if r.get('predicted_peak') else '점프';span=f'{r["start"]}→{r["right"]}'
            tag='blocked' if r.get('blocked_by_manual') else 'excluded' if not r['included'] or not r.get('repairable') else 'manual' if r['kind']=='replace' else 'prediction' if r.get('predicted_peak') else 'jump' if r.get('jump') else None
            anchors,generated=self.frame_columns(r)
            self.table.insert('','end',iid=r['key'],values=tr(('—' if r.get('blocked_by_manual') else '☑' if r['included'] else '☐',kind,span,
                f'+{r["score"]:.1f}%' if r['kind']!='replace' and (r['kind']!='hold' or r.get('jump') or r.get('predicted_peak')) else '—',
                self.compact_repair_plan(r),anchors,generated)),tags=(tag,) if tag else ())
        chosen=[r for r in rows if r['included']]
        replaced=len({f for run in actions for f in run['targets']})
        available=[r for r in rows if not r.get('blocked_by_manual')]
        hold_count=sum(r['kind']=='hold' for r in available);jump_count=sum(bool(r.get('jump')) for r in available)
        manual_count=sum(r['kind']=='replace' for r in rows);blocked=len(rows)-len(available)
        excluded_count=sum(not r['included'] for r in available)
        prediction_count=sum(bool(r.get('jump') and r.get('predicted_peak')) for r in rows)
        counts=f'홀드 {hold_count} · 점프 {jump_count} (보정 반영 {prediction_count})'
        self.summary_detail=counts+f'\n추가 {manual_count} · 비활성 {blocked} · 제외 {excluded_count} · 교체 {replaced}프레임 · 원본 {self.analysis["frames"]}프레임 유지'
        self.summary.set(f'홀드 {hold_count} · 점프 {jump_count}'+(f' · 추가 {manual_count}' if manual_count else '')+(f' · 제외 {excluded_count}' if excluded_count else ''))
        keep=[k for k in selection if self.table.exists(k)]
        if keep:self.table.selection_set(keep)
        else:self.detail.set('')
        self.draw_graph();self.update_buttons();self.schedule_kind_overlays()
        if self.player and self.player.is_open():self.player.set_model(self.model,self.rows,threshold)

    @staticmethod
    def frame_columns(row):
        if not row.get('included') or not row.get('repairable') or row.get('blocked_by_manual'):
            return '',''
        anchors=row.get('repair_anchors',[]);targets=row.get('targets',[])
        if len(anchors)!=2 or not targets:return '',''
        # Shared runs use shared references, but each row names only its own
        # target frames so adjacent jump rows do not double-list the whole run.
        generated=str(targets[0]) if len(targets)==1 else f'{targets[0]}–{targets[-1]}'
        return f'{anchors[0]}·{anchors[1]}',generated

    def repair_plan_text(self,row):
        if row.get('blocked_by_manual'):return '비활성 · 수동 추가 구간과 겹침'
        if not row.get('included',True):return '제외 · 원본 유지'
        if not row.get('repairable'):return '보류 · '+row.get('reason','참조 프레임 확인 필요')
        span=row.get('repair_span');anchors=row.get('repair_anchors')
        if not span or not anchors:return '보류 · 참조 프레임 확인 필요'
        target=str(span[0]) if span[0]==span[1] else f'{span[0]}–{span[1]}'
        grouped=span[1]-span[0]+1>len(row.get('targets',[]))
        return f'{target} 교체 · {anchors[0]}·{anchors[1]} 참조'+(' · 묶음' if grouped else '')

    def compact_repair_plan(self,row):
        if row.get('blocked_by_manual'):return '수동 추가 구간과 겹침'
        if not row.get('included',True):return '제외 · 원본 유지'
        if not row.get('repairable'):return '보류 · 상세 확인'
        span=row.get('repair_span')
        if not span:return '보류 · 상세 확인'
        target=str(span[0]) if span[0]==span[1] else f'{span[0]}–{span[1]}'
        return f'{target} 교체'+(' · 묶음' if span[1]-span[0]+1>len(row.get('targets',[])) else '')

    def graph_geometry(self):
        w=max(100,self.graph.winfo_width());h=max(100,self.graph.winfo_height())
        try:t=max(0,float(self.threshold.get()))
        except ValueError:t=5
        ymax=motion_graph_limit(self.model['metrics'] if self.model else [])
        return w,h,68,w-16,14,h-24,ymax,t

    def update_graph_controls(self):
        self.graph_view.set_frames(self.analysis['frames'] if self.analysis else 0)
        view=self.graph_view;valid=self.model is not None and not self.busy
        visible=view.visible_frames
        self.graph_range.set((str(visible.start) if len(visible)==1 else f'{visible.start}–{visible.stop-1}') if self.analysis else '—')
        self.graph_scrollbar.set(*view.fractions())
        self.graph_scrollbar.state(['!disabled'] if valid and view.zoomed else ['disabled'])
        self.graph_zoom_in_button.configure(state='normal' if valid and view.span>min(4,view.full_span)+1e-8 else 'disabled')
        for widget in (self.graph_zoom_out_button,self.graph_fit_button):
            widget.configure(state='normal' if valid and view.zoomed else 'disabled')

    def graph_zoom(self,factor,anchor=None):
        if self.busy or not self.model or self.dragging:return
        view=self.graph_view
        if anchor is None:anchor=self.playhead if view.left<=self.playhead<=view.right else (view.left+view.right)/2
        if view.zoom(factor,anchor):self.draw_graph()

    def graph_fit(self):
        if self.busy or not self.model:return
        self.graph_view.reset(self.analysis['frames']);self.draw_graph()

    def graph_scroll(self,*args):
        if self.busy or not self.model or self.dragging:return
        if args[0]=='moveto':changed=self.graph_view.moveto(float(args[1]))
        else:
            step=self.graph_view.span*.8 if args[2]=='pages' else max(1,self.graph_view.span*.1)
            changed=self.graph_view.pan(int(args[1])*step)
        if changed:self.draw_graph()

    def graph_wheel(self,event):
        if self.busy or not self.model or self.dragging:return 'break'
        _,_,x0,x1,_,_,_,_=self.graph_geometry()
        if not x0<=event.x<=x1:return 'break'
        delta=1 if getattr(event,'num',None)==4 else -1 if getattr(event,'num',None)==5 else getattr(event,'delta',0)/120
        delta=max(-3,min(3,delta))
        if not delta:return 'break'
        if event.state & 0x0001:
            if self.graph_view.pan(-delta*max(1,self.graph_view.span*.15)):self.draw_graph()
        else:
            anchor=self.graph_view.left+(event.x-x0)/max(1,x1-x0)*self.graph_view.span
            self.graph_zoom(1.5**delta,anchor)
        return 'break'

    def graph_pan_start(self,event):
        if self.busy or not self.model or self.dragging or not self.graph_view.zoomed:return 'break'
        _,_,x0,x1,_,_,_,_=self.graph_geometry()
        if not x0<=event.x<=x1:return 'break'
        self.graph_panning=True;self.graph_pan_x=event.x;self.graph.configure(cursor='fleur')
        return 'break'

    def graph_pan_drag(self,event):
        if not self.graph_panning or self.busy:return 'break'
        _,_,x0,x1,_,_,_,_=self.graph_geometry()
        delta=(self.graph_pan_x-event.x)/max(1,x1-x0)*self.graph_view.span
        self.graph_pan_x=event.x
        if self.graph_view.pan(delta):self.draw_graph()
        return 'break'

    def draw_graph(self):
        self.update_graph_controls()
        g=self.graph;g.delete('all');w,h,x0,x1,y0,y1,ymax,t=self.graph_geometry()
        view=self.graph_view;x_at=lambda frame:x0+view.position(frame)*(x1-x0)
        if self.model and self.analysis:
            cell=(x1-x0)/max(1,view.span)
            for frame,excluded in hold_frame_marks(self.model,self.rows):
                if frame<2 or frame+.44<view.left or frame-.44>view.right:continue
                x=x_at(frame)
                g.create_rectangle(max(x0,x-cell*.44),y0,min(x1,x+cell*.44),y1,
                    fill='#fff5f8' if excluded else '#fbe7ee',outline='#edceda' if excluded else '',
                    dash=(3,3) if excluded else (),tags=('hold_frame',f'hold_frame:{frame}'))
            for row in self.rows:
                if row['kind']!='replace':continue
                if row['targets'][-1]+.44<view.left or row['targets'][0]-.44>view.right:continue
                left=x_at(row['targets'][0]);right=x_at(row['targets'][-1])
                g.create_rectangle(max(x0,left-cell*.44),y0,min(x1,right+cell*.44),y1,
                    fill='' if not row['included'] else '#edf2ff',
                    outline=PALETTE['excluded'] if not row['included'] else PALETTE['primary'],
                    dash=(3,3) if not row['included'] else (),tags=('manual_range',row['key']))
        for value in np.linspace(0,ymax,3):
            y=y1-value/ymax*(y1-y0);g.create_line(x0,y,x1,y,fill='#eeeeee')
            g.create_text(x0-28,y,text=tr(f'{value:.3g}%'),anchor='e',fill=PALETTE['muted'],font=('맑은 고딕',8))
        y=y1-min(t,ymax)/ymax*(y1-y0)
        g.create_line(x0,y,x1,y,fill='#dd8b24',width=2,dash=(6,3))
        hy=max(y0+10,min(y1-10,y))
        g.create_rectangle(x0-22,hy-10,x0-4,hy+10,fill='#dd8b24',outline='#a65c0b',tags='threshold_handle')
        for dy in (-3,3):g.create_line(x0-18,hy+dy,x0-8,hy+dy,fill='white',width=1,tags='threshold_handle')
        if not self.model:g.create_text(w/2,h/2,text=tr('분석 후 표시됩니다.'),fill=PALETTE['muted']);return
        included={r['key']:r['included'] for r in self.rows}
        selected=set(self.table.selection())
        metrics=self.model['metrics']
        for m in metrics[max(0,math.ceil(view.left)-2):max(0,math.floor(view.right)-1)]:
            x=x_at(m['end'])
            yr=y1-min(ymax,m['raw_score'])/ymax*(y1-y0)
            if is_resolved_jump(self.model,m,t):
                g.create_line(x,y1,x,yr,fill='#c8bfe8',width=2,dash=(2,3),tags=('resolved_stem',f'resolved_stem:{m["end"]}'))
                g.create_oval(x-3,yr-3,x+3,yr+3,fill='#c8bfe8',outline='',tags=('resolved_jump',f'resolved_jump:{m["end"]}'))
            else:g.create_oval(x-1.5,yr-1.5,x+1.5,yr+1.5,fill='#c7ced7',outline='')
            event=event_for_frame(self.rows,m['end'])
            exc=event and not included[event['key']]
            actionable=event and event.get('jump',event.get('kind')=='jump')
            predicted=event and event.get('predicted_peak',False)
            color='#98a5b4' if exc else PALETTE['primary'] if event and event['kind']=='replace' else '#827d9f' if predicted else '#d97b24' if actionable else '#1688b4' if m['occupied'] else '#4c86a8'
            yp=y1-min(ymax,m['score'])/ymax*(y1-y0)
            if event and event['key'] in selected:g.create_line(x,y0,x,y1,fill='#dbe9f5')
            g.create_line(x,y1,x,yp,fill=color,width=2,dash=(3,3) if predicted else ())
            tags=(('jump_candidate',) if actionable else ())+(('prediction',f'prediction:{m["end"]}') if predicted else ())
            g.create_oval(x-3,yp-3,x+3,yp+3,outline=color,fill='white' if exc or predicted else color,width=1.5,tags=tags)
            if m['score']>ymax:g.create_text(x,y0-6,text=tr('↑'),fill=color)
        for frame in view.ticks(max(2,int((x1-x0)/65))):
            g.create_text(x_at(frame),y1+15,text=tr(str(frame)),fill=PALETTE['muted'],font=('맑은 고딕',8))
        self.draw_playhead()

    def draw_playhead(self):
        self.graph.delete('playhead')
        if not self.analysis or not self.graph_view.left<=self.playhead<=self.graph_view.right:return
        _,_,x0,x1,y0,y1,_,_=self.graph_geometry()
        x=x0+self.graph_view.position(self.playhead)*(x1-x0)
        self.graph.create_line(x,y0,x,y1,fill='#24354a',dash=(3,3),tags='playhead')
        self.graph.create_text(x,y0+1,text=tr('▼'),fill='#24354a',anchor='n',tags='playhead')

    def player_frame(self,frame):
        self.playhead=frame
        if self.model and self.player and not self.player.playing and self.graph_view.reveal(frame):self.draw_graph()
        else:self.draw_playhead()

    def player_seek(self,frame):
        if self.model and self.graph_view.reveal(frame):self.draw_graph()
        if self.player and self.player.is_open():self.player.seek(frame)

    def graph_down(self,e):
        if self.busy or not self.model:return
        w,h,x0,x1,y0,y1,ymax,t=self.graph_geometry();line=y1-min(t,ymax)/ymax*(y1-y0)
        handle=self.graph.bbox('threshold_handle')
        if handle and handle[0]<=e.x<=handle[2] and handle[1]<=e.y<=handle[3]:
            self.dragging=True;self.drag_ymax=ymax;self.drag_line_offset=e.y-line;return
        if not x0<=e.x<=x1:return
        frame=self.graph_view.frame_at((e.x-x0)/max(1,x1-x0))
        row=event_for_frame(self.rows,frame)
        if row:
            # A graph click selects this exact frame, not the first frame of
            # a longer table row that may lie outside the zoomed view.
            self.last_selected_key=row['key']
            self.table.selection_set(row['key']);self.table.see(row['key'])
        else:
            self.table.selection_remove(*self.table.selection());self.last_selected_key=None
            self.detail.set('')
            self.update_buttons()
        # Task status stays independent of the playhead; an old graph click
        # must not leave a conflicting frame number below the player.
        self.status.set('')
        self.root.after_idle(lambda:self.player_seek(frame))

    def graph_cursor(self,e):
        handle=self.graph.bbox('threshold_handle')
        on_handle=handle and handle[0]<=e.x<=handle[2] and handle[1]<=e.y<=handle[3]
        self.graph.configure(cursor='sb_v_double_arrow' if on_handle and self.model and not self.busy else '')

    def graph_drag(self,e):
        if not self.dragging or self.busy:return
        _,_,_,_,y0,y1,_,_=self.graph_geometry();ymax=self.drag_ymax
        line=e.y-getattr(self,'drag_line_offset',0)
        self.threshold.set(f'{max(0,min(ymax,(y1-line)/max(1,y1-y0)*ymax)):.1f}')

    def table_click(self,e):
        if self.busy:return
        key=self.table.identify_row(e.y)
        if key and self.table.identify_column(e.x)=='#1':
            self.table.selection_set(key);self.toggle_selected();return 'break'
        if key:
            r=next((r for r in self.rows if r['key']==key),None)
            if r:self.root.after_idle(lambda:self.player_seek(self.row_frame(r)))

    def toggle_selected(self):
        if self.busy:return
        keys=[k for k in self.table.selection() if any(r['key']==k and not r.get('blocked_by_manual') for r in self.rows)]
        if not keys:return
        include=all(k in self.excluded for k in keys)
        for k in keys:
            if include:self.excluded.discard(k)
            else:self.excluded.add(k)
        self.rebuild()

    def reset_exclusions(self):
        if self.busy:return
        self.excluded.clear();self.rebuild()

    @staticmethod
    def row_frame(row):
        if row['kind']=='replace':return row['targets'][0]
        return row['end'] if row['kind']=='hold' else row['right']

    def close_manual_window(self):
        if self.manual_window is not None:
            if self.manual_window.winfo_exists():self.manual_window.destroy()
            self.manual_window=None

    def open_manual_range(self):
        if self.busy:return
        media=self.analysis or self.player.analysis
        if not media or media['frames']<3:return
        if self.manual_window is not None and self.manual_window.winfo_exists():
            self.manual_window.lift();return
        generation=self.media_generation
        def add(left,right):
            if generation!=self.media_generation:raise ValueError('영상이 변경됐습니다. 추가 창을 다시 열어주세요.')
            self.add_manual_range(left,right)
        self.manual_window=show_manual_replacement(self.root,media['frames'],
            lambda:max(1,self.player.current_frame),add,self.player_seek)

    def add_manual_range(self,left,right):
        if self.busy:raise ValueError('진행 중인 작업이 끝난 뒤 추가하세요.')
        media=self.analysis or self.player.analysis
        if not media:raise ValueError('먼저 영상을 선택하세요.')
        proposed=motion.validate_manual_ranges(self.manual_ranges+[dict(left=left,right=right)],media['frames'])
        if not self.apply_analysis_options(rebuild=False):raise ValueError('분석 설정의 입력값을 확인하세요.')
        try:
            threshold=float(self.threshold.get())
            if not math.isfinite(threshold) or threshold<0:raise ValueError()
        except ValueError:raise ValueError('속도 기준에는 0 이상의 숫자를 입력하세요.') from None
        analysis=self.analysis
        if analysis is None:
            width=min(int(self.res.get()),media['metadata']['width'])
            analysis=dict(media,analysis_width=width,
                analysis_height=max(1,round(media['metadata']['height']*width/media['metadata']['width'])))
        # Prepare before committing so a rejected range leaves existing work intact.
        model=motion.prepare(analysis,self.holds.get(),self.quiet_px,self.min_move,
            excluded=self.excluded,support_percent=self.support_percent,min_points=self.min_points,manual_ranges=proposed)
        self.manual_ranges=proposed;self.analysis=analysis;self.model=model
        self.refresh()
        key=f'replace:{int(left)}:{int(right)}'
        self.table.selection_set(key);self.table.see(key);self.selected()
        self.status.set(f'{left}·{right} 참조 · {right-left-1}프레임 생성')

    def delete_manual_ranges(self):
        if self.busy:return
        keys={r['key'] for r in self.rows if r['kind']=='replace' and r['key'] in self.table.selection()}
        if not keys:return
        self.manual_ranges=[r for r in self.manual_ranges if r['key'] not in keys]
        self.excluded.difference_update(keys);self.last_selected_key=None
        self.rebuild();self.status.set('추가 구간을 삭제했습니다.')

    def selected(self,_=None):
        selected=self.table.selection()
        if not selected or not self.analysis:
            self.detail.set('');self.last_selected_key=None;self.draw_graph();self.update_buttons();return
        r=next((r for r in self.rows if r['key']==selected[0]),None)
        if not r:return
        if len(selected)>1:
            excluded=sum(key in self.excluded for key in selected)
            self.detail.set(f'선택 {len(selected)}개\n포함 {len(selected)-excluded} · 제외 {excluded}')
        else:
            span=((str(r['targets'][0]) if len(r['targets'])==1 else f'{r["targets"][0]}–{r["targets"][-1]}') if r['kind']=='replace' else f'{r["start"]}–{r["end"]}' if r['kind']=='hold' else f'{r["start"]}→{r["right"]}')
            kind=self.table.set(r['key'],'kind')
            score=f' · +{r["score"]:.1f}%' if r['kind']!='replace' and (r.get('jump') or r.get('predicted_peak') or r['kind']!='hold') else ''
            self.detail.set(f'선택: {kind} {span}{score}\n{self.repair_plan_text(r)}')
        self.draw_graph()
        if self.last_selected_key!=r['key']:self.player_seek(self.row_frame(r))
        self.last_selected_key=r['key'];self.update_buttons()

    def settings(self):
        if self.busy:return
        self.update_minimum_motion()
        previous=getattr(self,'help_window',None)
        if previous is not None and previous.winfo_exists():previous.destroy()
        self.help_window=show_detection_help(self.root,dict(
            quiet_px=self.quiet_px,min_move=self.min_move,min_move_default=self.min_move_default,
            analysis_width=self.min_move_analysis_width,support_percent=self.support_percent,
            min_points=self.min_points,threshold=self.threshold.get()))

    def start(self):
        if self.busy:return
        if not self.apply_analysis_options(rebuild=False):return
        path=self.path.get().strip().strip('"')
        if not Path(path).is_file():messagebox.showerror(tr('파일'),tr('영상을 선택하세요.'));return
        self.invalidate();self.stop=threading.Event();self.busy=True;self.update_buttons();self.bar['value']=0
        width=int(self.res.get());self.status.set('모션벡터 추적 중…')
        def worker():
            try:self.events.put(('scan_done',motion.scan(path,width,lambda f,n:self.events.put(('progress',f,n)),self.stop)))
            except core.Cancelled:self.events.put(('cancelled',))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def render(self,output=None):
        if self.busy or not self.analysis or not self.sdk_ready:return
        if not self.apply_analysis_options():return
        try:
            threshold=float(self.threshold.get())
            if not math.isfinite(threshold) or threshold<0:return
        except ValueError:return
        self.refresh()
        runs=motion.render_runs(self.rows)
        if not runs:return
        if output is None:
            source=Path(self.analysis['path']);extension=source.suffix or '.mov'
            output=filedialog.asksaveasfilename(title=tr('보간 결과 저장'),defaultextension=extension,
                initialfile=source.stem+'_motionfix'+extension,
                filetypes=[(tr('원본 형식')+' ('+extension+')','*'+extension)])
        if not output:return
        try:validate_output_paths(self.analysis['path'],output)
        except (ValueError,OSError) as e:messagebox.showerror(tr('저장 경로'),tr(str(e)));return
        a=self.analysis;quality=self.quality.get();manual_ranges=[dict(r) for r in self.manual_ranges]
        plan=[{k:v for k,v in r.items() if k!='stats'} for r in self.rows]
        analysis_options=dict(self.min_move_settings,quiet_px=self.quiet_px,hold_min_move=self.model.get('hold_min_move',1.),
                              support_percent=self.support_percent,min_points=self.min_points)
        replaced=len({f for run in runs for f in run['targets']})
        self.close_manual_window()
        self.busy=True;self.stop=threading.Event();self.update_buttons();self.bar['value']=0
        if self.player:self.player.pause()
        self.status.set(f'NVIDIA 교체 · {replaced}프레임 · 원본 {a["frames"]}프레임·FPS 유지 · 오디오 그대로 복사')
        def worker():
            try:
                result=sdk_bridge.repair(a,runs,output,quality,lambda f,n:self.events.put(('progress',f,n)),self.stop)
                report=dict(source=a['path'],output=result,threshold=float(self.threshold_value),holds=self.hold_value,jumps=self.jump_value,
                    mode='fixed_frame_replacement',source_frames=a['frames'],source_fps=a['metadata']['fps'],
                    replacement_frames=replaced,repair_runs=runs,candidates=plan,manual_ranges=manual_ranges,analysis_options=analysis_options,
                    audio='copied unchanged; source frame count and fps preserved')
                try:write_report(str(output)+'.analysis.json',report,protected_paths=[a['path'],output])
                except (ValueError,OSError) as e:result['report_error']=str(e)
                self.events.put(('render_done',result))
            except core.Cancelled:self.events.put(('cancelled',))
            except Exception as e:self.events.put(('error',str(e)))
        self.threshold_value=self.threshold.get();self.hold_value=self.holds.get();self.jump_value=self.jumps.get()
        threading.Thread(target=worker,daemon=True).start()

    def preferences(self):
        if self.preferences_window is not None and self.preferences_window.winfo_exists():
            self.preferences_window.lift();return
        self.preferences_window=show_app_settings(self.root,self.change_language,self.choose_sdk)

    def change_language(self,value):
        try:set_language(value,self.root)
        except OSError as error:
            messagebox.showerror(tr('설정'),str(error),parent=self.root)

    def choose_sdk(self):
        if self.busy:return
        if self.sdk_window is not None and self.sdk_window.winfo_exists():
            self.sdk_window.lift();self.sdk_window.focus_set();return
        self.sdk_window=show_sdk_settings(self.root,self.sdk_applied)

    def sdk_applied(self,path,result):
        # A startup check may finish after the user validates a new environment.
        self.sdk_check_generation+=1
        self.sdk_ready=True;self.sdk_status.set(result['gpu']);self.update_buttons()

    def check_sdk(self):
        self.sdk_check_generation+=1;generation=self.sdk_check_generation
        executable=sdk_bridge.runtime()
        self.sdk_ready=False;self.update_buttons()
        def worker():
            try:self.events.put(('sdk_ready',generation,sdk_bridge.check(executable or '')))
            except Exception as e:self.events.put(('sdk_error',generation,str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def cancel(self):self.stop.set();self.status.set('취소 중…')

    def pump(self):
        try:
            while True:
                ev=self.events.get_nowait();kind=ev[0]
                if kind=='media_ready':
                    if ev[1]==self.media_generation and not self.busy and self.analysis is None:
                        self.media_info=dict(path=ev[2]['path'],metadata=ev[2]['metadata'])
                        self.player.load(ev[2]);self.update_minimum_motion();self.update_buttons()
                elif kind=='media_error':
                    if ev[1]==self.media_generation and not self.busy:self.status.set('영상 정보를 읽지 못했습니다: '+ev[2])
                elif kind=='progress':
                    f,n=ev[1:];self.bar['value']=min(99,f/max(1,n)*100);self.status.set(f'처리 중 · {f} / {n}프레임')
                elif kind=='scan_done':
                    self.busy=False;self.analysis=ev[1];self.bar['value']=100
                    self.media_info=dict(path=self.analysis['path'],metadata=self.analysis['metadata'])
                    self.player.load(self.analysis);self.update_minimum_motion();self.rebuild()
                    self.status.set('분석 완료')
                elif kind in ('cancelled','error'):
                    self.busy=False;self.update_buttons();self.status.set('취소됐습니다.' if kind=='cancelled' else ev[1])
                    if kind=='error' and not self.closing:messagebox.showerror(tr('작업 실패'),tr(ev[1]))
                elif kind=='sdk_ready':
                    if ev[1]==self.sdk_check_generation:
                        self.sdk_ready=True;self.sdk_status.set(ev[2]['gpu']);self.update_buttons()
                elif kind=='sdk_error':
                    if ev[1]==self.sdk_check_generation:
                        self.sdk_ready=False;self.sdk_status.set('SDK 환경 확인 필요');self.update_buttons()
                elif kind=='render_done':
                    self.busy=False;self.bar['value']=100;r=ev[1];self.last_output=r['output'];self.update_buttons()
                    self.status.set(f'저장 완료 · 원본 {r["frames"]}프레임·FPS 유지 · '+r['output']+
                                    (' · 분석 내역 저장 실패: '+r['report_error'] if r.get('report_error') else ''))
        except queue.Empty:pass
        if self.closing and not self.busy:self.root.destroy();return
        self.root.after(80,self.pump)

    def close(self):
        self.close_manual_window()
        if self.media_after:self.root.after_cancel(self.media_after);self.media_after=None
        if self.player and self.player.is_open():self.player.close()
        self.closing=True;self.stop.set()
        if not self.busy:self.root.destroy()


def main():
    p=argparse.ArgumentParser();p.add_argument('video',nargs='?');p.add_argument('--analyze');p.add_argument('--report')
    p.add_argument('--jump-threshold',type=float,default=5)
    p.add_argument('--min-move',type=float,default=None,help='최소 기준 이동 원본 px (0 초과, 생략 시 분석 가로 × 0.005)')
    p.add_argument('--support-percent',type=float,default=30.,help='유효 추적점 비율 1~100%% (기본 30%%)')
    p.add_argument('--min-points',type=motion.validate_min_points,default=20,help='최소 추적점 개수 1~10000 (기본 20)')
    p.add_argument('--holds-only',action='store_true');p.add_argument('--jumps-only',action='store_true')
    p.add_argument('--repair');p.add_argument('--output');p.add_argument('--sdk-check',action='store_true');p.add_argument('--smoke-test')
    args=p.parse_args()
    if args.min_move is not None:
        try:valid_min_move(args.min_move)
        except ValueError:p.error('--min-move는 0 초과의 유한한 픽셀값이어야 합니다.')
    if not math.isfinite(args.support_percent) or not 1<=args.support_percent<=100:
        p.error('--support-percent는 1~100 사이의 유한한 숫자여야 합니다.')
    report_protected=[] if args.sdk_check else [path for path in (args.analyze,args.repair,args.video,args.output) if path]
    if args.report:
        try:validate_report_path(args.report,protected_paths=report_protected)
        except (ValueError,OSError) as e:
            if sys.stdout is not None:print(json.dumps(dict(error=str(e)),ensure_ascii=False,indent=2))
            return 1
    if args.sdk_check or args.analyze or args.repair:
        try:
            if args.sdk_check:result=sdk_bridge.check()
            else:
                a=motion.scan(args.analyze or args.repair)
                automatic=args.min_move is None;default=default_min_move(a['analysis_width'])
                minimum=default if automatic else args.min_move
                model=motion.prepare(a,not args.jumps_only,min_move=minimum,support_percent=args.support_percent,min_points=args.min_points)
                rows=motion.candidates(model,args.jump_threshold,not args.holds_only)
                runs=motion.render_runs(rows)
                result=dict(mode='fixed_frame_replacement',frames=a['frames'],fps=a['metadata']['fps'],
                    threshold=args.jump_threshold,holds=not args.jumps_only,jumps=not args.holds_only,
                    analysis_options=dict(min_move=minimum,min_move_auto=automatic,default_min_move=default,
                                          analysis_width=a['analysis_width'],quiet_px=.5,hold_min_move=model.get('hold_min_move',1.),
                                          support_percent=args.support_percent,min_points=args.min_points),
                    replacement_frames=len({f for run in runs for f in run['targets']}),repair_runs=runs,
                    candidates=[{k:v for k,v in row.items() if k!='stats'} for row in rows])
                if args.repair:
                    if not args.output:raise ValueError('--output이 필요합니다.')
                    result['render']=sdk_bridge.repair(a,runs,args.output,'HIGH',lambda f,n:None,threading.Event())
            code=0
        except Exception as e:result=dict(error=str(e));code=1
        if args.report:
            try:write_report(args.report,result,protected_paths=report_protected)
            except (ValueError,OSError) as e:
                result['report_error']=str(e);code=1
                if sys.stdout is not None:print(json.dumps(result,ensure_ascii=False,indent=2))
        elif sys.stdout is not None:print(json.dumps(result,ensure_ascii=False,indent=2))
        return code
    root=tk.Tk();app=App(root)
    if args.video:app.path.set(args.video)
    app.support_var.set(f'{args.support_percent:g}')
    app.min_points_var.set(str(args.min_points))
    if args.min_move is not None:app.min_move_var.set(f'{args.min_move:g}')
    app.apply_analysis_options()
    if args.smoke_test:
        root.update();Path(args.smoke_test).write_text('Motion UI ready',encoding='utf-8');root.after(700,app.close)
    root.mainloop()


if __name__=='__main__':raise SystemExit(main() or 0)
