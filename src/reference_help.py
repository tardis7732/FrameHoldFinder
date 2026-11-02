"""Modeless guide for the source-timed Video Edit workspace."""
import tkinter as tk
from tkinter import ttk

from i18n import tr
from ui_support import PALETTE, make_help_notebook


_PAGES = (
    ('그래프 읽기', (
        ('원본 프레임', '가로축은 원본 프레임 번호이고 세로축은 매치 신뢰도입니다. 그래프를 클릭하면 그 프레임의 영상과 대응 행으로 이동합니다.'),
        ('▲ 누락 후보', '호박색 띠와 삼각형은 편집본에 대응 프레임이 없는 원본 위치입니다. 분홍 띠는 편집본의 이미지 홀드, 주황 점은 확인할 대응입니다.'),
        ('프레임 대응표', '표 제목을 눌러 접거나 펼칩니다. 전체 프레임을 스크롤하거나 ‘검토 대상만’으로 확인할 행을 좁힐 수 있습니다.'),
    )),
    ('대응 수정', (
        ('매치 프레임 지정', '원본 행을 선택하고 편집본을 앞뒤로 움직여 같은 순간을 찾습니다. ‘이 쌍으로 지정’을 누르면 이 쌍을 기준으로 대응을 다시 계산합니다.'),
        ('구간 추가', '양끝이 맞는 원본 행 두 개를 선택합니다. 예를 들어 94번과 98번을 기준으로 적용하면, 두 행에 대응하는 편집본으로 원본 95~97번을 채웁니다.'),
        ('사용 프레임 변경', '선택한 원본 한 장에 쓸 편집 프레임을 직접 고릅니다. 표의 ‘저장 방식’에서 사용할 프레임이나 보간 위치를 확인할 수 있습니다.'),
    )),
    ('비교와 저장', (
        ('차이 보기', '분석과 차이 보기는 두 영상의 가로폭과 세로 중심을 맞춥니다. 겹치는 영역의 RGB 차이를 표시하며, ‘차이 강도’로 작은 차이를 밝게 볼 수 있습니다.'),
        ('미리보기 렌더', '현재 대응표대로 보정본을 만든 뒤 ‘보정 전’과 ‘보정 후’를 전환해 같은 원본 위치에서 비교합니다.'),
        ('원본 타이밍으로 저장', '편집본 이미지로 원본의 프레임 수와 FPS에 맞춰 저장합니다. 오디오는 원본에서 가져옵니다.'),
        ('설정 초기화', '나란히 보기, 보정 전, 차이 끔·1×, 전체 대응표와 그래프 기본 범위, 플레이어 기본 높이로 돌아갑니다.'),
    )),
)


class ReferenceHelp(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent.winfo_toplevel())
        self.title(tr('원본 비교 가이드'))
        self.configure(bg=PALETTE['surface'])
        self.bind('<Escape>', lambda _: self.destroy())

        body = ttk.Frame(self, style='Surface.TFrame', padding=(16, 12))
        body.pack(fill='both', expand=True)
        ttk.Button(body, text=tr('닫기'), command=self.destroy, style='Compact.TButton').pack(
            side='bottom', anchor='e', pady=(10, 0))
        self.notebook = make_help_notebook(body)
        self.notebook.pack(fill='both', expand=True)
        for title, sections in _PAGES:
            page = ttk.Frame(self.notebook, style='Surface.TFrame', padding=(14, 10))
            self.notebook.add(page, text=tr(title))
            page.columnconfigure(1, weight=1)
            for index, (label, detail) in enumerate(sections):
                row = 2 * index
                if index:
                    ttk.Separator(page).grid(row=row-1, column=0, columnspan=2, sticky='ew')
                ttk.Label(page, text=tr(label), style='Section.TLabel').grid(
                    row=row, column=0, sticky='nw', padx=(0, 16), pady=7)
                explanation = ttk.Label(page, text=tr(detail), style='Muted.TLabel',
                                        wraplength=400, justify='left')
                explanation.grid(row=row, column=1, sticky='new', pady=7)
                explanation.bind('<Configure>', lambda event: event.widget.configure(
                    wraplength=max(120, event.width - 4)))
        self.minsize(560, 400)
        owner = parent.winfo_toplevel()
        x = owner.winfo_rootx() + max(0, (owner.winfo_width() - 720) // 2)
        y = owner.winfo_rooty() + max(0, (owner.winfo_height() - 400) // 2)
        self.geometry(f'720x400{x:+d}{y:+d}')
        self.notebook.focus_set()
