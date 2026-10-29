# Validation / 검증

Public release: **1.0**, 2026-10-29. Local development iteration numbers are not public release versions.

공개 버전은 **1.0**입니다. 이전 로컬 개발 번호는 공개 릴리스 번호와 별개입니다.

## Automated tests / 자동 테스트

133 tests: **131 passed, 2 skipped** on Windows. The skipped cases require symbolic-link privileges unavailable in the test environment. Coverage includes motion criteria, manual ranges and reference protection, exclusion handling, graph viewport navigation, language translations, CLI validation, safe report paths and source-format planning.

Windows에서 133개 중 **131개 통과, 2개 생략**했습니다. 생략한 항목은 환경의 심볼릭 링크 권한이 필요한 경우입니다. 모션 기준·수동 구간/참조 보호·제외·그래프 탐색·번역·CLI·보고서 경로·원본 포맷 계획을 검증했습니다.

## Actual GPU renders / 실제 GPU 렌더

| Source / 원본 | Result / 결과 |
| --- | --- |
| ProRes HQ MOV / 4:2:2 10-bit | Codec/profile/pixel format, frame count/FPS and audio retained / 형식·프레임·오디오 유지 |
| ProRes4444 MOV / decoded4:4:4 12-bit | Matching encoded properties; interpolation remains RGB10A2 / 출력 속성 유지, 보간은 RGB10A2 |
| H.264 High MP4 | Matching source properties / 원본 형식 유지 |
| HEVC Main10 MP4 | Matching source properties / 원본 형식 유지 |
| Full Higgsfield source,721frames,1920×1080,24fps | HEVC Main10/color tags/audio retained; decoded AAC hash matched / HEVC·색정보·오디오 유지, AAC 디코딩 해시 일치 |
| CLI manual render,12frames,30000/1001fps | References3/6 replaced4/5; count/FPS/audio retained / 3·6 참조로4·5 교체, 프레임·FPS·오디오 유지 |
| Cropped jump example,34frames,24fps | Cut original111–144 first; +37.1% detection,11 replacements,11→0 candidates,34frames retained / 원본111–144를 먼저 잘라 분석,11장 교체,후보11→0,34프레임 유지 |
| Hold example,66frames,24fps | First1–66,hold2 + correction-aware jump2→0,frames15/17/63/65 replaced; HEVC Main10/BT.709/AAC payload retained / 첫66프레임,홀드2·보정반영점프2→0,4장 교체,형식·오디오 유지 |

Short audio-bearing fixtures also retained their decoded audio hashes. Source files remained unchanged. These tests establish the supported export path, not universal format compatibility or perfect reconstruction.

짧은 오디오 포함 테스트에서도 디코딩 오디오 해시가 유지됐고 원본 파일은 변경되지 않았습니다. 검증된 저장 경로에 대한 결과이며, 모든 형식이나 완벽한 보간을 보장하지는 않습니다.

## Desktop review / 데스크톱 확인

- Korean/English live switch retains analysis, candidates, exclusions, frame position and zoom.
- Graph zoom, panning, point seeking, threshold handle, full-range reset and small-window layout checked.
- 82 UI checks cover the frame1-to-last display, vertical maximum/Auto/Ctrl+wheel, focus handling and both-axis Fit reset.
- English main UI and all help tabs checked for clipping and untranslated labels.
- Frozen GUI startup and console analysis checked separately from source execution.

- 한국어/영어 전환 후 분석·목록·제외·재생 위치·확대 상태 유지 확인.
- 그래프 확대·이동·클릭·기준 손잡이·전체 보기·작은 창 배치 확인.
- UI82항목으로 1프레임~마지막 표시, 세로 최대·자동·Ctrl+휠, 입력 포커스와 양축 전체 초기화 확인.
- 영문 메인 UI와 도움말 탭의 잘림·미번역 확인.
- 소스 실행과 별도로 패키지 GUI 시작·CLI 분석 확인.

Test environment / 검증 환경: Windows 11 x64, Python 3.11, RTX 5070 Ti, NVIDIA 610.88, CUDA 12.8, nvidia-vfx 0.2.0.0, CuPy CUDA12x 14.2.0, FFmpeg 9.0.1.

For visual example results and their limits, see [English](EXAMPLES.en.md) / [한국어](EXAMPLES.md).
