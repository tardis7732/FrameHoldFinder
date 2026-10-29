"""Local UI translations. Inputs, model data and paths are never localized."""
import json
import os
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
import weakref

_language = 'ko'
_variables = weakref.WeakSet()
_originals = {}

_CATALOG = r'''
Frame Hold Finder · 모션 분석 1.0||Frame Hold Finder · Motion Analysis 1.0
영상 선택||Open Video
분석 시작||Analyze
재분석||Reanalyze
취소||Cancel
더보기||More
설정…||Settings…
설정||Settings
언어||Language
변경 즉시 적용됩니다.||Changes apply immediately.
SDK 설정…||SDK Setup…
SDK 설정||SDK Setup
최근 결과 열기||Open Last Result
결과 폴더 열기||Open Output Folder
파일 경로 복사||Copy File Path
제외 초기화||Reset Exclusions
기준 초기화||Reset Thresholds
감지 도움말||Detection Help
전체 설정||General
홀드 설정||Hold Detection
점프 설정||Jump Detection
분석 폭||Analysis Width
감지||Enable
허용 이동||Tolerance
원본 px 이하||source px ≤
최소 기준 이동||Min. Motion
속도 기준||Speed
추적점 비율||Point Ratio
최소 추적점||Min. Points
% 초과||% above
% 이상||% or more
개 이상|| points or more
 px 이하|| px or less
보정 영상 저장||Save Corrected Video
품질||Quality
NVIDIA SDK 확인 중…||Checking NVIDIA SDK…
검출 구간||Detected Ranges
추가||Add
전체||Fit
선택 제외||Exclude Selected
선택 포함||Include Selected
삭제||Delete
포함||Use
원본 구간||Source Range
유형||Type
참조 프레임||References
생성 프레임||Generated
분석 후 검출 구간이 표시됩니다.||Detected ranges appear after analysis.
현재 기준에서 검출된 구간이 없습니다.||No ranges detected at the current thresholds.
분석 후 표시됩니다.||Analyze a video to view the graph.
영상||Video
모든 파일||All Files
영상은 한 번에 하나씩 드롭해 주세요.||Drop one video at a time.
영상 파일을 드롭해 주세요.||Drop a video file.
영상 경로를 입력하거나 파일을 선택하세요.||Enter a video path or choose a file.
파일을 찾을 수 없습니다. 입력한 경로를 확인하세요.||File not found. Check the entered path.
파일 경로를 복사했습니다.||File path copied.
저장한 결과 파일을 찾을 수 없습니다.||The saved output file could not be found.
열기 실패 · ||Could not open · 
분석 시작을 눌러 검출 구간을 확인하세요.||Select Analyze to detect ranges.
교체할 프레임 없음||No frames to replace
 · SDK 확인 필요|| · SDK setup required
홀드는 0 이상 px, 최소 이동은 0 초과 px, 추적점 비율은 1~100%, 최소 추적점은 1~10000 사이의 정수를 입력하세요.||Enter hold tolerance ≥ 0 px, minimum motion > 0 px, point ratio 1–100%, and 1–10000 minimum points.
기준이 자동 적용됐습니다.||Thresholds applied.
분석 기준을 변경했습니다.||Analysis settings changed.
분홍 홀드 · 주황 점프 · 보라 보정 반영||Pink: hold · Orange: jump · Purple: correction-aware
점프 기준에는 0 이상의 유한한 숫자를 입력하세요.||Enter a finite speed threshold of zero or greater.
속도 기준에는 0 이상의 숫자를 입력하세요.||Enter a speed threshold of zero or greater.
보정 반영 점프||Correction-aware Jump
보정 반영||Correction-aware
홀드+점프||Hold + Jump
홀드||Hold
점프||Jump
비활성 · 수동 추가 구간과 겹침||Disabled · overlaps a manual range
수동 추가 구간과 겹침||Overlaps a manual range
제외 · 원본 유지||Excluded · keep original
참조 프레임 확인 필요||Reference frames need review
보류 · 상세 확인||Deferred · review details
보류 · ||Deferred · 
 · 묶음|| · grouped
추가 구간을 삭제했습니다.||Manual range deleted.
영상을 선택하세요.||Choose a video.
모션벡터 추적 중…||Tracking motion vectors…
보간 결과 저장||Save Interpolated Video
원본 형식||Source Format
저장 경로||Output Path
작업 실패||Operation Failed
취소 중…||Canceling…
취소됐습니다.||Canceled.
영상 정보를 읽지 못했습니다: ||Could not read video information: 
분석 완료||Analysis complete
SDK 환경 확인 필요||SDK setup required
파일||File
테스트 영상 선택||Choose Test Video
분석 설정의 입력값을 확인하세요.||Check the analysis settings.
진행 중인 작업이 끝난 뒤 추가하세요.||Wait for the current operation to finish before adding a range.
먼저 영상을 선택하세요.||Open a video first.
영상이 변경됐습니다. 추가 창을 다시 열어주세요.||The video has changed. Reopen Add Range.
검토할 영상을 선택하세요.||Choose a video to review.
영상 선택 · Ctrl+O||Open video · Ctrl+O
모션 분석 시작 · Ctrl+Enter||Analyze motion · Ctrl+Enter
같은 점의 앞뒤 이동량보다 모두 이 비율 이상 빨라야 점프 후보입니다.||A point must exceed both adjacent motion intervals by this percentage to count toward a jump.
포함된 교체 대상을 저장합니다. 원본 프레임 수·FPS·길이를 유지하고 오디오를 복사합니다. NVIDIA SDK가 필요합니다.||Render included ranges. Preserves the source frame count and FPS, and copies audio. Requires NVIDIA SDK.
Ctrl/Shift로 여러 항목을 선택해 함께 제외하거나 포함할 수 있습니다.||Use Ctrl or Shift to select multiple ranges, then include or exclude them together.
이전 검출 구간||Previous detected range
다음 검출 구간||Next detected range
홀드로 판단할 정지 허용 이동량입니다. 원본 픽셀 기준이며 기본값은 0.5px입니다.||Maximum motion treated as a hold, in source pixels. Default: 0.5 px.
유효 추적점 중 속도 기준을 넘는 점의 비율입니다. 최소 추적점 조건도 함께 만족해야 합니다.||Required share of valid tracked points exceeding the speed threshold. Minimum point count must also be met.
속도 기준을 넘어야 하는 실제 추적점의 최소 개수입니다.||Minimum number of actual tracked points that must exceed the speed threshold.
점프 검출과 그래프 점수에 적용합니다.\n홀드 보정 후 남는 점프 평가에도 같은 기준을 사용합니다.||Used for jump detection and graph scores.\nThe same criteria evaluate jumps that remain after hold correction.
▶ 재생||▶ Play
❚❚ 정지||❚❚ Pause
영상을 선택하면 여기서 재생할 수 있습니다.||Open a video to play it here.
영상 검토||Video Review
첫 프레임을 불러오는 중…||Loading the first frame…
반복||Loop
벡터||Vectors
검토 재생 오류: ||Playback error: 
이전 프레임으로 이동합니다.||Go to the previous frame.
다음 프레임으로 이동합니다.||Go to the next frame.
재생 / 일시정지\n미리보기는 소리 없이 재생됩니다.||Play / pause\nThe preview is silent.
재생 속도||Playback speed
화살표 길이만 3배로 표시합니다.\n실제 이동량과 검출 기준은 변하지 않습니다.||Arrows are drawn at 3× length.\nActual motion and detection criteria are unchanged.
프레임 번호를 입력하고 Enter를 누르세요.||Enter a frame number and press Enter.
분홍: 반복 프레임 · 연보라: 보정 후 해소 예상||Pink: repeated frames · Lavender: expected to resolve
프레임 번호에 정수를 입력하세요.||Enter an integer frame number.
유효한 영상 크기, 프레임 수, fps가 필요합니다.||Valid dimensions, frame count and FPS are required.
영상 디코딩이 실패했습니다.||Video decoding failed.
불완전한 검토 프레임을 받았습니다.||Received an incomplete preview frame.
요청한 프레임을 읽지 못했습니다. 원본과 분석 프레임 수를 확인하세요.||Could not read the requested frame. Check source and analysis frame counts.
프레임 표시 연결 오류: ||Frame display callback error: 
설정 기준||Detection Settings
그래프 읽기||Reading the Graph
교체 방식||Replacement Rules
교체||Replaced
현재값||Current Value
의미||Meaning
미입력||Not entered
확인 필요||Needs review
추적에 사용할 영상의 가로 크기||Video width used for tracking
홀드 허용 이동||Hold Tolerance
같은 이미지로 볼 이동량. 원본 픽셀 기준이며 기본값은 0.5px||Maximum motion treated as the same image, in source pixels. Default: 0.5 px.
점프 비교에 사용할 추적점의 최소 움직임. 원본 픽셀 기준||Minimum tracked motion used for jump comparisons, in source pixels.
같은 점이 바로 앞·뒤 구간보다 모두 얼마나 빨라야 하는지||Required speed increase over both neighboring intervals of the same point.
비교 가능한 추적점 중 속도 기준을 넘는 점의 최소 비율||Minimum share of comparable points exceeding the speed threshold.
비율과 함께 충족할 실제 점 개수. 점수가 아니며 1~10000개 조절||Actual point count required alongside the ratio. Adjustable from 1 to 10000.
직접 입력한 px 값은 같은 영상 재분석에서 유지합니다. 새 영상·분석 폭 선택 시 기본값으로 돌아갑니다.||Custom pixel values persist when reanalyzing the same video. A new video or analysis width restores the default.
홀드 기본값은 바뀌지 않습니다. 비율·최소 점 개수 변경은 다시 추적하지 않고 평가만 갱신합니다.||Hold tolerance stays unchanged. Changing the point ratio or minimum count reevaluates without retracking.
설명용 예시 · 속도 기준 5%||Illustration · 5% speed threshold
같은 추적점의 현재 움직임이 앞·뒤 구간보다 모두 기준을 초과해야 합니다.||The same tracked point must exceed the threshold against both adjacent intervals.
위 두 점 개수 조건을 만족하는 속도 초과율입니다. 기준선 위면 점프 후보입니다.||The speed increase supported by both point-count criteria. Values above the threshold are jump candidates.
그래프 높이||Graph Height
점 개수||Point Support
보라 빈 원도 교체 대상입니다. 홀드 보간을 가정한 값이며 실제 생성 결과의 측정값은 아닙니다.\n점프 후보는 누락 확정이 아닙니다. 위 그림은 설명용 예시입니다.||Purple hollow circles are also replacement candidates. Their values assume hold interpolation; they are not measurements of rendered frames.\nJump candidates do not prove missing frames. The graph above is an illustration.
보정 시 해소||Expected to resolve
홀드 2–3 · 같은 이미지||Hold 2–3 · same image
연속 점프 3→4, 4→5||Adjacent jumps 3→4, 4→5
전체 프레임 수·FPS·영상 길이를 유지하며 오디오는 그대로 복사합니다.\n추가: 양끝 번호를 선택하면 내부 프레임만 생성합니다. 3·6 선택 → 4·5 생성.\n추가 구간과 겹치는 자동 항목은 비활성화됩니다. 추가 항목을 제외·삭제하면 다시 평가합니다.||Frame count, FPS and duration are preserved; audio is copied unchanged.\nAdd: choose two endpoints to regenerate only the frames between them. Choose 3 and 6 → generate 4 and 5.\nAutomatic ranges that conflict with manual ranges are disabled. Excluding or deleting the manual range reevaluates them.
구간 추가||Add Range
앞뒤 기준 프레임 선택||Choose Two Reference Frames
양 끝은 유지하고, 사이 프레임을 같은 간격으로 생성해 기존 위치에 적용합니다.||Keep both endpoints and regenerate the intervening frames at evenly spaced times.
앞 기준 프레임||First Reference Frame
뒤 기준 프레임||Last Reference Frame
현재 프레임||Current Frame
이동||Go
닫기||Close
3프레임 이상인 영상에서 사용할 수 있습니다.||Requires a video with at least 3 frames.
뒤 기준은 앞 기준보다 최소 2프레임 뒤여야 합니다.||The last reference must be at least 2 frames after the first.
NVIDIA VFX SDK 연결||NVIDIA VFX SDK Connection
경로 변경||Change Path
연결 확인||Check Connection
확인 후 적용||Verify and Apply
현재 등록 경로||Current SDK Path
등록된 SDK 경로가 없습니다.||No SDK path has been configured.
선택한 경로 · 아직 적용하지 않음||Selected Path · Not Yet Applied
NVIDIA VFX SDK가 설치된 Python 환경의 …\Scripts\python.exe를 선택하세요.\n폴더나 모델 파일이 아닌 Python 실행 파일을 선택합니다.\n분석·재생은 SDK 없이 가능하며, 보정 영상 저장에는 SDK 연결이 필요합니다.||Select …\Scripts\python.exe from the Python environment containing NVIDIA VFX SDK.\nChoose the Python executable, not a folder or model file.\nAnalysis and playback work without the SDK; rendering requires it.
등록 경로를 읽는 중 오류가 있었습니다. 경로를 다시 선택할 수 있습니다.||Could not read the configured path. You can select it again.
저장된 실행 파일을 찾을 수 없습니다. 경로를 변경해 주세요.||The configured executable was not found. Choose a new path.
등록된 경로입니다. 연결 확인으로 SDK와 GPU 연결을 검사할 수 있습니다.||This is the configured path. Check Connection verifies the SDK and GPU.
경로 변경에서 SDK Python 실행 파일을 선택해 주세요.||Use Change Path to select the SDK Python executable.
NVIDIA VFX SDK Python 선택||Select NVIDIA VFX SDK Python
Python 실행 파일||Python Executable
실행 파일||Executable
현재 등록된 경로를 선택했습니다.||Selected the currently configured path.
선택한 경로는 아직 저장하지 않았습니다. 확인 후 적용을 누르면 검증한 뒤 저장합니다.||The selected path is not saved yet. Verify and Apply checks it before saving.
먼저 SDK Python 실행 파일을 선택해 주세요.||Choose the SDK Python executable first.
SDK와 GPU 연결을 확인하고 있습니다…||Checking the SDK and GPU connection…
성공하면 선택한 경로를 적용합니다.||The selected path will be applied if verification succeeds.
등록 경로는 변경하지 않았습니다.||The configured path is unchanged.
연결 실패 · 기존 등록 경로는 유지됩니다.||Connection failed · The existing SDK path is unchanged.
경로를 적용하지 못했습니다.||Could not apply the path.
경로는 저장했지만 앱 상태 갱신 중 오류가 발생했습니다.||The path was saved, but the application status could not be refreshed.
SDK 연결 정상||SDK Connected
연결 정상 · ||Connected · 
적용 완료 · ||Applied · 
기준 초기화 · 최소 기준 이동 ||Thresholds reset · Minimum motion 
px · 감지 모드·분석 폭·제외 항목 유지|| px · Detection modes, analysis width and exclusions retained
점프 평가에 사용할 최소 이동량(원본 px). 새 영상·분석 폭 선택 시 기본값 ||Minimum motion for jump evaluation (source px). New videos/widths default to 
px. 직접 입력값은 그대로 사용합니다.|| px. Custom values are used unchanged.
최소 이동 기본값: 분석 가로 × 0.005 = ||Default minimum motion: analysis width × 0.005 = 
유효점 ||Valid points: 
 이상과 최소 || or more, and at least 
과 최소 || and at least 
를 동시에 충족해야 합니다.|| are both required.
 기준 프레임 번호를 입력하세요.|| reference frame number is required.
 기준 프레임은 정수로 입력하세요.|| reference frame must be an integer.
 기준 프레임은 1–|| reference frame must be between 1 and 
 사이로 입력하세요.||.
현재||Current
앞||First
뒤||Last
속도||Speed
선택: ||Selected: 
선택 ||Selected 
NVIDIA 교체 · ||NVIDIA replacement · 
처리 중 · ||Processing · 
저장 완료 · 원본 ||Saved · Source 
 · 분석 내역 저장 실패: || · Could not save analysis report: 
프레임·FPS 유지 · 오디오 그대로 복사|| frames and FPS preserved · Audio copied unchanged
프레임·FPS 유지 · || frames and FPS preserved · 
프레임 교체|| frames to replace
프레임 생성|| frames generated
프레임 유지|| frames retained
프레임 · 원본 || frames · Source 
 교체|| replaced
 참조|| reference
 생성 · || generated · 
장 · 길이 유지|| frames · Duration preserved
프레임|| frames
비활성||Disabled
제외||Excluded
원본||Original
개|| points
NVIDIA SDK 환경을 선택해 주세요.||Choose an NVIDIA SDK environment.
SDK Python 실행 파일이 없습니다.||The SDK Python executable was not found.
SDK Python 실행 파일을 찾을 수 없습니다. 경로를 다시 선택하세요.||The SDK Python executable was not found. Select the path again.
SDK 확인이 실패했습니다.||SDK verification failed.
싱크 유지 검증 실패: 출력 프레임 수가 원본과 다릅니다.||Sync validation failed: output frame count differs from the source.
NVIDIA 보간이 실패했습니다.||NVIDIA interpolation failed.
보고서 경로가 폴더입니다. JSON 파일 경로를 선택하세요.||The report path is a folder. Choose a JSON filename.
보고서를 원본 또는 출력 영상 경로에 저장할 수 없습니다.||The report cannot overwrite the source or output video.
영상 스트림이 없습니다.||No video stream was found.
프레임 속도를 읽지 못했습니다.||Could not read the frame rate.
현재 버전은 SDR 영상용입니다. HDR 영상은 SDR 검토본으로 변환해 주세요.||This version supports SDR video. Convert HDR footage to an SDR review copy first.
현재 분석은 SDR 영상용입니다.||Analysis currently supports SDR video.
영상 디코딩 중 불완전한 프레임을 받았습니다.||An incomplete frame was received while decoding.
디코딩된 프레임이 없습니다.||No frames could be decoded.
분석 도중 영상 파일이 변경됐습니다. 다시 분석하세요.||The video changed during analysis. Analyze it again.
분석 중 원본이 변경됐습니다.||The source changed during analysis.
분석 이후 입력 파일이 변경됐습니다. 다시 분석하세요.||The input changed after analysis. Analyze it again.
불완전한 영상 프레임입니다.||The video frame is incomplete.
프레임 미리보기를 불러오지 못했습니다.||Could not load the frame preview.
임계값은 0 이상의 유한한 숫자여야 합니다.||The threshold must be a finite number of zero or greater.
유효하지 않은 차이값입니다.||Invalid difference value.
교체할 원본 프레임 수가 올바르지 않습니다.||Invalid source frame count for replacement.
교체 구간과 장수는 정수여야 합니다.||Replacement ranges and frame counts must be integers.
교체 구간은 겹칠 수 없으며 양끝 원본 프레임이 필요합니다.||Replacement ranges cannot overlap and require original frames at both endpoints.
싱크 유지 모드에서는 교체할 프레임 수와 생성 장수가 같아야 합니다.||Generated and replaced frame counts must match to preserve sync.
양끝 원본 프레임이 있는 교체 구간을 선택하세요.||Select a replacement range with original frames at both endpoints.
프레임 보간 결과가 참조 프레임과 같습니다. 반복 프레임 저장을 중단했습니다.||: interpolation returned a reference frame. Saving was stopped to avoid introducing a hold.
입력 영상이 없습니다.||Input video not found.
출력 경로가 폴더입니다. 영상 파일 경로를 선택하세요.||The output path is a folder. Choose a video filename.
원본 영상에는 저장할 수 없습니다.||The source video cannot be overwritten.
보간이 취소됐습니다.||Interpolation canceled.
현재 보간 저장은 알파 없는 RGB 영상용입니다. 알파 채널이 있는 영상은 별도 처리해 주세요.||Rendering currently supports video without alpha. Process alpha-channel video separately.
현재 보간 저장은 고정 프레임률(CFR) 영상용입니다. VFR 영상은 CFR 검토본으로 변환해 주세요.||Rendering requires constant frame rate (CFR). Convert variable-frame-rate footage to a CFR review copy.
입력 프레임 디코딩이 불완전합니다.||Input frame decoding is incomplete.
보간 구간 안에서 영상이 끝났습니다.||The video ended within an interpolation range.
뒤쪽 정상 프레임을 읽지 못했습니다.||Could not read the trailing reference frame.
출력 프레임 수가 처리 계획과 다릅니다. 다시 분석하세요.||Output frame count differs from the plan. Analyze the source again.
Video Frame Generation을 지원하는 NVIDIA VFX SDK가 필요합니다.||NVIDIA VFX SDK with Video Frame Generation support is required.
추적점 비율은 1~100 사이의 숫자여야 합니다.||Point ratio must be between 1 and 100.
최소 추적점 수는 1~10000 사이의 정수여야 합니다.||Minimum points must be an integer between 1 and 10000.
수동 추가 프레임은 정수여야 합니다.||Manual range frames must be integers.
영상의 전체 프레임 수가 올바르지 않습니다.||Invalid total video frame count.
수동 추가 구간은 목록이어야 합니다.||Manual ranges must be a list.
수동 추가 구간의 양끝 프레임이 필요합니다.||Manual ranges require both reference frames.
영상 범위 안에서 교체할 프레임이 포함되도록 양끝을 선택하세요.||Choose two endpoints within the video, with at least one frame between them.
수동 추가 구간의 키와 프레임이 일치하지 않습니다.||Manual range identifier and frames do not match.
수동 추가 구간은 겹칠 수 없습니다. 양끝 프레임만 공유할 수 있습니다.||Manual ranges cannot overlap. Only their endpoints may be shared.
앞뒤 기준 프레임이 없어 교체할 수 없습니다.||Cannot replace without a reference frame on each side.
제외한 프레임과 겹쳐 홀드 교체를 보류합니다.||Hold replacement deferred because it overlaps an excluded frame.
제외되거나 보류된 홀드가 기준 프레임에 있어 교체를 보류합니다.||Replacement deferred because a reference frame belongs to an excluded or deferred hold.
홀드 이동량은 0 이상, 최소 이동량은 0보다 커야 합니다.||Hold tolerance must be at least zero and minimum motion must be greater than zero.
홀드 문맥 최소 이동량은 0보다 큰 유한한 숫자여야 합니다.||Hold context minimum motion must be a finite number greater than zero.
점프 기준은 0 이상의 숫자입니다.||Jump threshold must be a number of zero or greater.
'''

TRANSLATIONS = {}
for _line in _CATALOG.strip().splitlines():
    if '||' in _line:
        _source, _target = _line.split('||', 1)
        TRANSLATIONS[_source.replace('\\n', '\n')] = _target.replace('\\n', '\n')
_pattern = re.compile('|'.join(re.escape(key) for key in sorted(TRANSLATIONS, key=len, reverse=True)))
_path_pattern = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|(?:[\w.-]+[\\/])+(?=\S))[^\n]*')


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'FrameHoldFinder' / 'settings.json'


def load_language():
    global _language
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8')).get('language', 'ko')
        _language = value if value in ('ko', 'en') else 'ko'
    except (OSError, ValueError, TypeError, AttributeError):
        _language = 'ko'
    return _language


def language():
    return _language


def tr(value):
    if isinstance(value, tuple):
        return tuple(tr(item) for item in value)
    if isinstance(value, list):
        return [tr(item) for item in value]
    if not isinstance(value, str):
        return value
    source = _originals.get(value, value)
    # Do not reinterpret user-entered relative media paths or bare filenames.
    if re.fullmatch(r'[^\n]*\.(?:mov|mp4|mxf|mkv|avi|webm|exe)', source, re.IGNORECASE) and not any(mark in source for mark in (' · ', ': ')):
        return source
    if _language == 'ko':
        return source
    if source in TRANSLATIONS:
        result = TRANSLATIONS[source]
    else:
        # A status message can contain a Windows path with Korean words.
        # Mask that suffix before translating the surrounding UI sentence.
        paths = []
        def protect(match):
            paths.append(match.group(0))
            return f'\x00{len(paths)-1}\x00'
        safe = _path_pattern.sub(protect, source)
        result = _pattern.sub(lambda match: TRANSLATIONS[match.group(0)], safe)
        for index, path in enumerate(paths):
            result = result.replace(f'\x00{index}\x00', path)
    if result != source:
        _originals[result] = source
    return result


class DisplayVar(tk.StringVar):
    """A display-only variable retaining its untranslated source for switching."""
    def __init__(self, master=None, value='', name=None):
        self.source = value
        super().__init__(master, tr(value), name)
        _variables.add(self)

    def set(self, value):
        self.source = _originals.get(value, value) if isinstance(value, str) else value
        super().set(tr(self.source))

    __hash__ = object.__hash__


def set_language(value, root=None, persist=True):
    """Translate existing display surfaces without rebuilding analysis/player state."""
    global _language
    if value not in ('ko', 'en'):
        raise ValueError('Unsupported language')
    if persist:
        path = settings_path()
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(settings, dict):settings = {}
        except (OSError, ValueError):
            settings = {}
        settings['language'] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    _language = value
    for variable in list(_variables):
        try:variable.set(variable.source)
        except tk.TclError:pass
    if root is not None:
        refresh_widgets(root)


def refresh_widgets(widget):
    """Refresh static labels, open dialogs, menus and plots after a locale change."""
    try:
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.title(tr(widget.title()))
        if 'text' in widget.keys() and not ('textvariable' in widget.keys() and widget.cget('textvariable')):
            widget.configure(text=tr(widget.cget('text')))
        if isinstance(widget, tk.Menu):
            for index in range((widget.index('end') or 0) + 1):
                if widget.type(index) not in ('separator', 'tearoff'):
                    widget.entryconfigure(index, label=tr(widget.entrycget(index, 'label')))
        if isinstance(widget, ttk.Notebook):
            for tab in widget.tabs():widget.tab(tab, text=tr(widget.tab(tab, 'text')))
        if isinstance(widget, ttk.Treeview):
            for column in widget['columns']:widget.heading(column, text=tr(widget.heading(column, 'text')))
            for row in widget.get_children():widget.item(row, values=tr(widget.item(row, 'values')))
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                if widget.type(item) == 'text':widget.itemconfigure(item, text=tr(widget.itemcget(item, 'text')))
        if isinstance(widget, tk.Text) and not hasattr(widget, '_variable'):
            source = widget.get('1.0', 'end-1c');translated = tr(source)
            if source != translated:
                state = widget['state'];widget.configure(state='normal')
                widget.delete('1.0', 'end');widget.insert('1.0', translated);widget.configure(state=state)
        for child in widget.winfo_children():refresh_widgets(child)
    except tk.TclError:
        pass

