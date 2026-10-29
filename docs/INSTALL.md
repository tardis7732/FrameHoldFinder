# 설치와 환경

[한국어](INSTALL.md) · [English](INSTALL.en.md) · [처음으로](../README.md)

## Windows 패키지

릴리스 ZIP 전체를 압축 해제하고 `FrameHoldFinder.exe`를 실행하세요. `_internal` 폴더를 EXE와 함께 보관해야 합니다. 분석·재생·그래프·수동 구간 지정에는 별도 Python이나 NVIDIA GPU가 필요하지 않습니다. Windows x64용이며, 다른 운영체제용 실행 파일은 제공하지 않습니다.

보정 저장만 NVIDIA GPU와 별도 SDK Python 환경을 사용합니다. SDK가 없으면 먼저 분석 결과를 검토할 수 있습니다.

## SDK 설치

이 앱은 **NVIDIA Video Effects Python 패키지의 VideoFrameGeneration**을 사용합니다. Nuke의 RIFE나 게임용 DLSS 프레임 생성과는 별개의 경로입니다.

### 준비

- Windows 10/11 64비트, Python 3.11 x64 권장.
- Windows VFG 지원 GPU: **Ada 또는 Blackwell** 계열. 예: GeForce RTX 40/50 시리즈. 다른 VFX 효과의 지원 GPU 목록과 혼동하지 마세요. [NVIDIA VFG 요구사항](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoFrameGeneration.html#hardware-requirements)
- 호환되는 최신 NVIDIA 드라이버. 테스트 환경은 RTX 5070 Ti / 드라이버 610.88입니다.
- 테스트 환경의 CUDA Toolkit은 **12.8**입니다. 아래 예시는 시스템에 CUDA 12.x Toolkit이 설치되어 있다고 가정합니다. [CUDA 다운로드](https://developer.nvidia.com/cuda-downloads)

### 별도 Python 환경 만들기

PowerShell에서 실행합니다. 기존 Nuke나 다른 앱의 Python 환경에 설치하지 않아도 됩니다.

```powershell
py -3.11 -m venv C:\vfx-env
& C:\vfx-env\Scripts\python.exe -m pip install --upgrade pip setuptools
& C:\vfx-env\Scripts\python.exe -m pip install numpy "cupy-cuda12x==14.2.0"
& C:\vfx-env\Scripts\python.exe -m pip install "nvidia-vfx==0.2.0.0" --extra-index-url https://pypi.nvidia.com
& C:\vfx-env\Scripts\python.exe -m pip check
```

설치 패키지 이름은 `nvidia-vfx`, Python에서 import하는 이름은 `nvvfx`입니다. 이 버전의 NVIDIA wheel은 실행에 쓰는 VFX 라이브러리를 포함합니다. 앱 ZIP에는 SDK·모델·CUDA를 재배포하지 않습니다. [NVIDIA 패키지 설명](https://pypi.org/project/nvidia-vfx/)

시스템 CUDA Toolkit 없이 구성하려면 CuPy의 `[ctk]` 설치 방식도 있습니다. 위 CuPy 설치 대신 `"cupy-cuda12x[ctk]==14.2.0"`을 사용하고, 최신 드라이버와 CUDA 런타임 호환성을 확인하세요. 이 대안은 이 프로젝트의 실제 검증 구성과 다릅니다. 서로 다른 CuPy 패키지를 한 환경에 중복 설치하지 마세요. [CuPy 공식 설치 안내](https://docs.cupy.dev/en/stable/install.html)

### 앱에 연결하기

1. **더보기 → 설정 → SDK 설정**을 엽니다.
2. **경로 변경**에서 `C:\vfx-env\Scripts\python.exe`를 선택합니다. 폴더·DLL·모델 파일이 아닙니다.
3. **확인 후 적용**을 누릅니다. GPU 이름과 연결 상태가 표시됩니다.
4. 실제 짧은 구간을 보정해 GPU 메모리와 해당 입력 형식도 확인합니다.

CLI로 동일한 설정을 저장할 수 있습니다.

```powershell
.\FrameHoldFinder-CLI.exe sdk-check --python "C:\vfx-env\Scripts\python.exe" --save
```

언어와 SDK 경로는 `%LOCALAPPDATA%\FrameHoldFinder` 아래에 사용자별로 저장됩니다. 앱 폴더를 바꿔도 유지됩니다.

## 지원 형식과 제한

| 항목 | 지원 및 동작 |
| --- | --- |
| 입력 | SDR, 정방향·고정 FPS, 한 개의 영상 스트림 |
| 기본 출력 | 원본과 같은 확장자 / MOV, MP4, M4V, MKV의 지원 조합 |
| ProRes | MOV/MKV, Proxy·LT·Standard·HQ·4444·XQ의 지원 픽셀 형식 |
| H.264 | 지원되는 Baseline/Main/High 계열, YUV 4:2:0/4:2:2/4:4:4 8·10비트 조합 |
| HEVC | Main / Main10, YUV 4:2:0 8·10비트 |
| 오디오 | 원본 오디오 스트림 복사. 합성하거나 속도를 바꾸지 않음 |
| 지원하지 않는 경우 | 알파, HDR PQ/HLG·HDR 부가 메타데이터, 인터레이스, 회전 메타데이터, 가변 FPS, 영상 시작 타임스탬프가 0이 아닌 입력, 복수 영상, 자막/데이터 스트림 포함 입력 |

코덱과 픽셀 형식의 모든 조합을 보장하지 않습니다. 사용 가능한 FFmpeg 인코더가 요구 속성을 보존하지 못하면 저장을 중단합니다. SDK 생성은 RGB 10비트 경로이고, 전체 영상은 다시 인코딩됩니다. 보정 대상 밖의 프레임도 압축 결과까지 동일하지는 않습니다.

기본 출력이 지원되지 않는 코덱이라면 별도 검토본을 만들거나, CLI의 명시적 `--format prores` 옵션으로 MOV 변환을 선택할 수 있습니다. 이 옵션도 알파·HDR 등 생성 경로 자체의 제한을 해제하지 않습니다.

## 소스에서 실행

Python 3.11 x64와 FFmpeg/FFprobe를 준비합니다. 시스템 PATH의 FFmpeg는 libx264, libx265, prores_ks 인코더가 있는 빌드를 사용하세요.

```powershell
git clone https://github.com/tardis7732/FrameHoldFinder.git
cd FrameHoldFinder
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe app.py
& .\.venv\Scripts\python.exe cli.py --help
```

FFmpeg가 없다면 [공식 다운로드 안내](https://ffmpeg.org/download.html)를 따라 설치하거나 `winget install --id Gyan.FFmpeg.Essentials -e`로 설치한 뒤 새 터미널을 여세요.

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
& .\.venv\Scripts\python.exe -m pip install pyinstaller
.\build.ps1 -PythonExe .\.venv\Scripts\python.exe
```

## 문제 해결

- `No module named nvvfx` / `cupy`: 앱에 선택한 **SDK Python** 환경에 패키지가 설치됐는지 확인합니다.
- CUDA DLL / NVRTC 오류: CUDA 12.x Toolkit과 CuPy 패키지, GPU 드라이버 조합을 확인합니다.
- SDK 확인은 되지만 렌더가 실패: 입력 형식 또는 GPU 메모리 부족일 수 있습니다. 오류 메시지와 작은 해상도의 검토본으로 확인하세요.
- 점프가 너무 많이 잡힘: 의도적인 가속·카메라 움직임도 후보가 됩니다. 속도 기준·추적점 비율을 올리고 컷을 제외해 검토하세요.
- 정지 장면이 홀드로 잡힘: 의도적인 정지를 제외하세요. 움직임만으로 오류와 연출 의도를 확정할 수 없습니다.
