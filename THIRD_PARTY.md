# Third-party components / 외부 구성요소

The application source is MIT licensed. Bundled dependencies keep their own licenses. Demo imagery/video is excluded from the application MIT license.

앱 소스는 MIT 라이선스이며, 외부 구성요소는 각자의 라이선스를 따릅니다. 데모 이미지·영상에는 앱의 MIT 라이선스가 적용되지 않습니다.

| Component / 구성요소 | Use / 용도 | Upstream / 출처 |
| --- | --- | --- |
| Python 3.11 + Tcl/Tk | Runtime and desktop UI / 런타임·UI | [Python](https://www.python.org/), [Tcl/Tk](https://www.tcl.tk/) |
| NumPy 2.4.6 | Motion data / 모션 데이터 | [NumPy](https://numpy.org/) |
| OpenCV headless 5.0.0.93 | Sparse LK optical flow / 추적점 모션 분석 | [OpenCV](https://opencv.org/) |
| Pillow 12.3.0 | Preview display / 미리보기 표시 | [Pillow](https://python-pillow.github.io/) |
| tkinterdnd2 0.7.0 + TkDND | File drag-and-drop / 파일 드롭 | [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) |
| PyInstaller 6.22.3 | Windows packaging / 실행 파일 패키징 | [PyInstaller](https://pyinstaller.org/) |
| FFmpeg / FFprobe 9.0.1 | Decode, encode and inspect / 디코딩·인코딩·검증 | [FFmpeg](https://ffmpeg.org/), [Gyan builds](https://www.gyan.dev/ffmpeg/builds/) |
| NVIDIA Video Effects Python 0.2.0.0 | Optional interpolation, separately installed / 별도 설치 보간 | [NVIDIA package](https://pypi.org/project/nvidia-vfx/), [API](https://docs.nvidia.com/maxine/vfx-python/latest/api.html) |
| CuPy CUDA12x 14.2.0 | Optional CUDA arrays, separately installed / 별도 설치 GPU 배열 | [CuPy](https://cupy.dev/) |

## FFmpeg

The Windows release includes unmodified FFmpeg/FFprobe from the Gyan 9.0.1 essentials build, configured with `--enable-gpl --enable-version3`. Those executables are GPL v3 or later, not MIT. The recorded build configuration and GPL text are in [docs/licenses](docs/licenses). FFmpeg is invoked as a separate process.

Windows 배포본에는 Gyan 9.0.1 essentials 빌드의 FFmpeg/FFprobe를 수정 없이 포함합니다. 해당 실행 파일은 `--enable-gpl --enable-version3` 설정으로 빌드됐으며 MIT가 아닌 GPL v3 이상을 따릅니다. 빌드 설정과 GPL 전문은 [docs/licenses](docs/licenses)에 있습니다. 앱은 FFmpeg를 별도 프로세스로 실행합니다.

Source and build information / 소스·빌드 정보:

- [FFmpeg 9.0.1 source archive](https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz)
- [Gyan build downloads and source links](https://www.gyan.dev/ffmpeg/builds/)
- [FFmpeg dependency/build configuration](docs/licenses/FFMPEG-LICENSE.txt)
- [GNU GPL v3 text](docs/licenses/GPL-3.0.txt)

## NVIDIA

NVIDIA VFX, CUDA, GPU drivers and model/runtime packages are not redistributed in this repository or application archive. Install them separately under NVIDIA's terms. The app is not endorsed by NVIDIA.

NVIDIA VFX·CUDA·GPU 드라이버와 모델·런타임 패키지는 이 저장소나 앱 ZIP에 포함하지 않습니다. NVIDIA의 조건에 따라 별도 설치하세요. NVIDIA 공식 제품이 아닙니다.

## Demo media / 데모 미디어

The example is an excerpt from the author's existing Higgsfield/Seedance generation, used to demonstrate analysis and correction. Account tokens, generation prompts and private source URLs are not included. Preview media is for demonstrating this project; it carries no separate open-content license.

예시는 작성자의 기존 Higgsfield/Seedance 생성 영상 일부로, 분석과 보정을 설명하기 위해 사용합니다. 계정 토큰·생성 프롬프트·비공개 원본 URL은 포함하지 않습니다. 데모 미디어에는 별도의 자유 콘텐츠 라이선스를 부여하지 않습니다.
