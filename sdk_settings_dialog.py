"""Inspect and validate an optional SDK environment before applying it."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from i18n import tr, DisplayVar
from tkinter import filedialog, ttk

import sdk_bridge
from ui_support import PALETTE


class SDKSettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_applied):
        super().__init__(parent)
        self.on_applied = on_applied
        self.closed = False
        self.busy = False
        self.events = queue.Queue()
        self.request_id = 0
        self.poll_after = None
        self.activate_after = None
        self.last_result = None
        self.last_error = ''
        self.title(tr('SDK 설정'))
        self.configure(bg=PALETTE['surface'])
        self.transient(parent.winfo_toplevel())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', self._close)
        self.bind('<Destroy>', self._destroyed, add='+')
        self._fit_window(parent)

        self.registered_path, available, read_error = self._registered_path()
        self.current_path = self.registered_path
        self.selected_path = self.registered_path
        self.status_var = DisplayVar()
        self.status = self.status_var
        body = ttk.Frame(self, padding=(20, 14), style='Surface.TFrame')
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=tr('NVIDIA VFX SDK 연결'), style='Surface.TLabel',
                  font=('맑은 고딕', 12, 'bold')).pack(anchor='w', pady=(0, 12))

        # Reserve the actions first so a long path or error cannot push them out.
        footer = ttk.Frame(body, style='Surface.TFrame')
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        self.change_button = ttk.Button(footer, text=tr('경로 변경'), command=self.choose_path, style='Compact.TButton')
        self.change_button.pack(side='left')
        self.check_button = ttk.Button(footer, text=tr('연결 확인'), command=lambda: self.check_connection(False), style='Compact.TButton')
        self.check_button.pack(side='left', padx=(7, 0))
        self.close_button = ttk.Button(footer, text=tr('닫기'), command=self.destroy, style='Compact.TButton')
        self.close_button.pack(side='right')
        self.apply_button = ttk.Button(footer, text=tr('확인 후 적용'), command=lambda: self.check_connection(True), style='Primary.TButton')
        self.apply_button.pack(side='right', padx=(0, 7))

        ttk.Label(body, text=tr('현재 등록 경로'), style='Surface.TLabel').pack(anchor='w', pady=(0, 5))
        self.current_path_host, self.current_path_text = self._path_box(body, height=3)
        self.current_path_host.pack(fill='x')
        self.path_text = self.current_path_text
        self._put_path(self.current_path_text, self.registered_path or '등록된 SDK 경로가 없습니다.')

        # A second path appears only while a genuinely different choice is pending.
        self.selection_host = ttk.Frame(body, style='Surface.TFrame')
        ttk.Label(self.selection_host, text=tr('선택한 경로 · 아직 적용하지 않음'), style='Surface.TLabel').pack(anchor='w', pady=(0, 4))
        self.selected_path_host, self.selected_path_text = self._path_box(self.selection_host, height=2)
        self.selected_path_host.pack(fill='x')

        self.guide_host = ttk.Frame(body, style='Surface.TFrame')
        self.guide_host.pack(fill='x', pady=(12, 0))
        ttk.Separator(self.guide_host).pack(fill='x', pady=(0, 10))
        self.guide_label = ttk.Label(self.guide_host, text=(
            tr('NVIDIA VFX SDK가 설치된 Python 환경의 …\\Scripts\\python.exe를 선택하세요.\n'
            '폴더나 모델 파일이 아닌 Python 실행 파일을 선택합니다.\n'
            '분석·재생은 SDK 없이 가능하며, 보정 영상 저장에는 SDK 연결이 필요합니다.')),
            style='Muted.TLabel', font=('맑은 고딕', 9), justify='left', wraplength=750)
        self.guide_label.pack(fill='x')
        self.status_label = tk.Label(body, textvariable=self.status_var, height=3, anchor='w', justify='left',
                                     bg=PALETTE['surface'], fg=PALETTE['text'], font=('맑은 고딕', 9),
                                     wraplength=750, borderwidth=0, padx=0, pady=0)
        self.status_label.pack(fill='x', pady=(8, 0))
        self.progress = ttk.Progressbar(body, mode='indeterminate', style='Horizontal.TProgressbar')
        body.bind('<Configure>', self._resize_text)

        if read_error:
            self.status_var.set('등록 경로를 읽는 중 오류가 있었습니다. 경로를 다시 선택할 수 있습니다.\n' + read_error[:200])
        elif self.registered_path and not available:
            self.status_var.set('저장된 실행 파일을 찾을 수 없습니다. 경로를 변경해 주세요.')
        elif self.registered_path:
            self.status_var.set('등록된 경로입니다. 연결 확인으로 SDK와 GPU 연결을 검사할 수 있습니다.')
        else:
            self.status_var.set('경로 변경에서 SDK Python 실행 파일을 선택해 주세요.')
        self._update_buttons()
        # Mapping first avoids platform-specific errors when taking a local grab.
        self.activate_after = self.after_idle(self._activate)

    @staticmethod
    def _registered_path():
        error = ''
        try:
            path = sdk_bridge.runtime()
            if path:
                return str(path), True, ''
        except Exception as exc:
            error = str(exc)
        try:
            path = sdk_bridge.configured_runtime()
            return (str(path) if path else None), False, error
        except Exception as exc:
            return None, False, error or str(exc)

    def _fit_window(self, parent):
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(800, max(320, screen_w - 48)), min(480, max(360, screen_h - 80))
        self.minsize(min(760, width), min(480, height))
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        x, y = max(0, min(screen_w - width, x)), max(0, min(screen_h - height - 45, y))
        self.geometry(f'{width}x{height}+{x}+{y}')

    def _path_box(self, parent, height):
        host = ttk.Frame(parent, style='Surface.TFrame')
        text = tk.Text(host, height=height, wrap='char', state='disabled', takefocus=True,
                       font=('맑은 고딕', 9), bg=PALETTE['subtle'], fg=PALETTE['text'],
                       relief='flat', borderwidth=0, padx=9, pady=6,
                       highlightthickness=1, highlightbackground=PALETTE['border'], highlightcolor=PALETTE['focus'],
                       selectbackground=PALETTE['primary'], selectforeground=PALETTE['primary_fg'])
        scroll = ttk.Scrollbar(host, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        text.bind('<Control-a>', lambda event: self._select_all(event.widget))
        text.bind('<Control-A>', lambda event: self._select_all(event.widget))
        return host, text

    @staticmethod
    def _select_all(widget):
        widget.tag_add('sel', '1.0', 'end-1c')
        widget.mark_set('insert', '1.0')
        widget.see('1.0')
        return 'break'

    @staticmethod
    def _put_path(widget, path):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', tr(str(path)))
        widget.configure(state='disabled')
        widget.yview_moveto(0)

    def _resize_text(self, event):
        width = max(280, event.width - 40)
        self.guide_label.configure(wraplength=width)
        self.status_label.configure(wraplength=width)

    def _activate(self):
        self.activate_after = None
        if self.closed:
            return
        if not self.winfo_viewable():
            self.activate_after = self.after(25, self._activate)
            return
        self.grab_set()
        self.lift()
        self.change_button.focus_set()

    def _update_buttons(self):
        self.change_button.configure(state='disabled' if self.busy else 'normal')
        state = 'normal' if self.selected_path and not self.busy else 'disabled'
        self.check_button.configure(state=state)
        self.apply_button.configure(state=state)

    def choose_path(self):
        if self.closed or self.busy:
            return
        options = dict(parent=self, title=tr('NVIDIA VFX SDK Python 선택'),
                       filetypes=tr([('Python 실행 파일', 'python.exe'), ('실행 파일', '*.exe')]))
        if self.selected_path:
            folder = Path(self.selected_path).parent
            if folder.is_dir():
                options['initialdir'] = str(folder)
        path = filedialog.askopenfilename(**options)
        if not path or self.closed:
            return
        self.selected_path = str(path)
        self.last_result = None
        self.last_error = ''
        self._show_selection()
        self.status_var.set('선택한 경로는 아직 저장하지 않았습니다. 확인 후 적용을 누르면 검증한 뒤 저장합니다.'
                            if self.selected_path != self.registered_path else '현재 등록된 경로를 선택했습니다.')
        self._update_buttons()

    def _show_selection(self):
        if self.selected_path != self.registered_path:
            self._put_path(self.selected_path_text, self.selected_path)
            self.selection_host.pack(fill='x', pady=(9, 0), before=self.guide_host)
        else:
            self.selection_host.pack_forget()

    def check_connection(self, apply=False):
        if self.closed or self.busy:
            return
        path = self.selected_path
        if not path:
            self.status_var.set('먼저 SDK Python 실행 파일을 선택해 주세요.')
            return
        self.request_id += 1
        request_id = self.request_id
        self.busy = True
        self.last_error = ''
        self.status_var.set('SDK와 GPU 연결을 확인하고 있습니다…' + ('\n성공하면 선택한 경로를 적용합니다.' if apply else ''))
        self.progress.configure(value=0)
        self.progress.pack(fill='x', pady=(4, 0))
        self.progress.start(12)
        self._update_buttons()

        def worker():
            # No Tk calls and no settings writes from the background thread.
            try:
                result = sdk_bridge.check(path)
                self.events.put((request_id, path, apply, True, result))
            except Exception as exc:
                self.events.put((request_id, path, apply, False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.poll_after = self.after(60, self._poll)

    def _poll(self):
        self.poll_after = None
        if self.closed:
            return
        try:
            while True:
                request_id, path, apply, success, result = self.events.get_nowait()
                if request_id != self.request_id:
                    continue
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                self._update_buttons()
                if not success:
                    self.last_error = str(result)
                    self.status_var.set('연결 실패 · 기존 등록 경로는 유지됩니다.\n' + self.last_error[:320])
                    continue
                self.last_result = result
                gpu = result.get('gpu', 'SDK 연결 정상') if isinstance(result, dict) else 'SDK 연결 정상'
                if not apply:
                    self.status_var.set(f'연결 정상 · {gpu}\n등록 경로는 변경하지 않았습니다.')
                    continue
                try:
                    # Closing the dialog invalidates the request before this point.
                    sdk_bridge.save_runtime(path)
                except Exception as exc:
                    self.last_error = str(exc)
                    self.status_var.set('경로를 적용하지 못했습니다.\n' + self.last_error[:320])
                    continue
                self.registered_path = self.current_path = self.selected_path = path
                self._put_path(self.current_path_text, path)
                self._show_selection()
                self.status_var.set(f'적용 완료 · {gpu}')
                try:
                    if self.on_applied is not None:
                        self.on_applied(path, result)
                except Exception as exc:
                    self.last_error = str(exc)
                    self.status_var.set('경로는 저장했지만 앱 상태 갱신 중 오류가 발생했습니다.\n' + self.last_error[:240])
        except queue.Empty:
            pass
        if self.busy and not self.closed:
            self.poll_after = self.after(60, self._poll)

    def _close(self, _event=None):
        self.destroy()
        return 'break'

    def _cleanup(self):
        if self.closed:
            return
        self.closed = True
        self.request_id += 1
        for name in ('poll_after', 'activate_after'):
            pending = getattr(self, name, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        try:
            self.progress.stop()
            if self.grab_current() is self:
                self.grab_release()
        except (tk.TclError, AttributeError):
            pass

    def _destroyed(self, event):
        if event.widget is self:
            self._cleanup()

    def destroy(self):
        self._cleanup()
        super().destroy()


def show_sdk_settings(parent, on_applied):
    """Return the modal settings window without blocking the caller's event loop."""
    return SDKSettingsDialog(parent, on_applied)
