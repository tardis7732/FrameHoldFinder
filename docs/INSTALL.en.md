# Installation and environment

[한국어](INSTALL.md) · [English](INSTALL.en.md) · [Home](../README.en.md)

## Windows package

Extract the entire release ZIP and run `FrameHoldFinder.exe`. Keep `_internal` beside the executables. Analysis, playback, graphs and manual range selection need neither a separate Python installation nor an NVIDIA GPU. The packaged application targets Windows x64.

Corrected-video export uses a separate NVIDIA SDK Python environment. You can review analysis before installing it.

## SDK setup

The app uses **VideoFrameGeneration from the NVIDIA Video Effects Python package**. This is separate from Nuke RIFE or game DLSS frame generation.

### Requirements

- Windows 10/11 64-bit; Python 3.11 x64 recommended.
- Windows VFG supports **Ada or Blackwell** GPUs, such as GeForce RTX 40/50 series. The broader GPU lists for other VFX effects do not apply to VFG. [NVIDIA requirements](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoFrameGeneration.html#hardware-requirements)
- A compatible NVIDIA driver. Tested: RTX 5070 Ti, driver 610.88.
- Tested system CUDA Toolkit: **12.8**. The commands below assume a CUDA 12.x Toolkit is installed. [CUDA downloads](https://developer.nvidia.com/cuda-downloads)

### Create a separate environment

Run in PowerShell; no change to an existing Nuke or other application's Python is necessary.

```powershell
py -3.11 -m venv C:\vfx-env
& C:\vfx-env\Scripts\python.exe -m pip install --upgrade pip setuptools
& C:\vfx-env\Scripts\python.exe -m pip install numpy "cupy-cuda12x==14.2.0"
& C:\vfx-env\Scripts\python.exe -m pip install "nvidia-vfx==0.2.0.0" --extra-index-url https://pypi.nvidia.com
& C:\vfx-env\Scripts\python.exe -m pip check
```

The distribution is named `nvidia-vfx`; the import is `nvvfx`. This NVIDIA wheel includes its runtime VFX libraries. The app ZIP does not redistribute NVIDIA SDK, models or CUDA. [NVIDIA package information](https://pypi.org/project/nvidia-vfx/)

CuPy also offers a `[ctk]` installation route without a system-wide CUDA Toolkit: replace its command with `"cupy-cuda12x[ctk]==14.2.0"`. Check driver/runtime compatibility. This alternative differs from this project's tested configuration. Do not install multiple CuPy variants in the same environment. [CuPy installation](https://docs.cupy.dev/en/stable/install.html)

### Connect the app

1. Open **More → Settings → SDK Setup**.
2. Choose `C:\vfx-env\Scripts\python.exe` using **Change Path**. Select the Python executable, not a folder, DLL or model.
3. Choose **Verify and Apply** and confirm the GPU/status.
4. Render a short range to verify memory availability and the actual source format.

The CLI saves the same setting:

```powershell
.\FrameHoldFinder-CLI.exe sdk-check --python "C:\vfx-env\Scripts\python.exe" --save
```

Language and SDK preferences are stored per user under `%LOCALAPPDATA%\FrameHoldFinder` and survive moving the application folder.

## Formats and limitations

| Property | Support |
| --- | --- |
| Input | SDR, forward constant-frame-rate video, one video stream |
| Default output | Same extension; supported MOV, MP4, M4V and MKV combinations |
| ProRes | MOV/MKV; supported Proxy, LT, Standard, HQ, 4444 and XQ pixel formats |
| H.264 | Supported Baseline/Main/High profiles and 8/10-bit YUV 4:2:0, 4:2:2 or 4:4:4 combinations |
| HEVC | Main/Main10, 8/10-bit YUV 4:2:0 |
| Audio | Original streams copied; no synthesis or time stretch |
| Unsupported | Alpha, PQ/HLG HDR or HDR side metadata, interlaced input, rotation metadata, VFR, nonzero video start timestamps, multiple video streams, subtitle/data streams |

Not every profile/pixel-format combination is guaranteed. Export stops if FFmpeg cannot preserve required properties. Interpolation uses 10-bit RGB, and the full video is re-encoded. Even unchanged frames are not guaranteed to retain identical compressed bytes.

For an unsupported source codec, make a separate review copy or explicitly choose MOV conversion with CLI `--format prores`. This does not bypass alpha/HDR or other interpolation-path restrictions.

## Running from source

Install Python 3.11 x64 and FFmpeg/FFprobe on PATH. FFmpeg should include libx264, libx265 and prores_ks encoders.

```powershell
git clone https://github.com/tardis7732/FrameHoldFinder.git
cd FrameHoldFinder
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe app.py
& .\.venv\Scripts\python.exe cli.py --help
```

Install FFmpeg using the [official download links](https://ffmpeg.org/download.html), or run `winget install --id Gyan.FFmpeg.Essentials -e` and open a fresh terminal.

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
& .\.venv\Scripts\python.exe -m pip install pyinstaller
.\build.ps1 -PythonExe .\.venv\Scripts\python.exe
```

## Troubleshooting

- `No module named nvvfx` / `cupy`: install packages in the exact **SDK Python** selected in Settings.
- CUDA DLL / NVRTC errors: check the CUDA 12.x Toolkit, CuPy package and driver combination.
- SDK check passes but rendering fails: inspect the error for source-format or GPU-memory issues; try a lower-resolution review copy.
- Too many jumps: intentional acceleration/camera movement can be flagged. Increase speed/support thresholds and review cuts separately.
- A still shot is marked as a hold: exclude intentional pauses. Motion analysis cannot establish creative intent.
