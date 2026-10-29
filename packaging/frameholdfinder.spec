# -*- mode: python ; coding: utf-8 -*-
"""Two entry points sharing one portable Windows dependency directory."""
from pathlib import Path
import shutil

packaging = Path(SPECPATH).resolve()
root = packaging.parent
source = root / 'src'


def executable(name):
    path = shutil.which(name)
    if not path:
        raise SystemExit(f'{name} is required on PATH to build the portable package.')
    return path


binaries = [(executable('ffmpeg'), 'bin'), (executable('ffprobe'), 'bin')]
# These modules run in the separately installed NVIDIA Python environment, so
# their source must remain available beside one another outside the PYZ archive.
data = [(str(source / 'sdk_worker.py'), '.'), (str(source / 'source_format.py'), '.')]

gui = Analysis([str(source / 'app.py')], pathex=[str(source)], binaries=binaries,
               datas=data, hiddenimports=[], hookspath=[], hooksconfig={},
               runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
cli = Analysis([str(source / 'cli.py')], pathex=[str(source)], binaries=[],
               datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
               runtime_hooks=[], excludes=[], noarchive=False, optimize=0)

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='FrameHoldFinder', debug=False, bootloader_ignore_signals=False,
              strip=False, upx=False, console=False,
              disable_windowed_traceback=False,
              icon=str(packaging / 'app.ico'), version=str(packaging / 'version_info.txt'))
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name='FrameHoldFinder-CLI', debug=False, bootloader_ignore_signals=False,
              strip=False, upx=False, console=True,
              icon=str(packaging / 'app.ico'), version=str(packaging / 'version_info.txt'))

# COLLECT normalizes duplicate destinations from the same interpreter and
# dependencies. Both launchers use this one _internal directory.
bundle = COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
                 strip=False, upx=False, name='FrameHoldFinder')
