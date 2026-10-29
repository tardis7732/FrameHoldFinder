# 실제 영상 예시

[한국어](EXAMPLES.md) · [English](EXAMPLES.en.md) · [처음으로](../README.md)

![원본과 보정본 비교 — 0.5배속](images/comparison-half-speed.gif)

[정상 속도 비교 영상](media/comparison-original-corrected.mp4) · [예시 파일 다운로드](https://github.com/tardis7732/FrameHoldFinder/releases/latest)

## 입력

작성자의 Higgsfield 계정에서 내려받은 Seedance 영상의 **연속된 원본 265–408프레임**을 사용했습니다. 이 예시 파일은 1–144프레임으로 번호를 다시 매겼습니다. 배포 크기를 줄이기 위해 H.264로 재인코딩했으며, 문제를 만들기 위한 프레임 복제·삽입·삭제·속도 변경은 하지 않았습니다. 예시 구간에는 샷 전환이 포함되므로 모든 피크를 프레임 드랍으로 단정하지 않습니다.

- 1920×1080, 24fps, 144프레임, 6초
- 예시 발췌본과 비교 영상은 **무음**
- GIF는 차이를 보기 위한 **0.5배속**이며, 원본·보정본 MP4는 정상 속도

## 같은 조건으로 분석

분석 폭 960, 속도 +5%, 최소 이동 4.8px, 유효점 30%, 최소 20점, 홀드 허용 이동 0.5px를 사용했습니다.

| 결과 | 보정 전 | 보정 후 재분석 |
| --- | ---: | ---: |
| 점프 후보 | 20 | 0 |
| 홀드 후보 | 0 | 0 |
| 총 프레임 | 144 | 144 |
| FPS | 24 | 24 |
| 길이 | 6초 | 6초 |

검출된 기존 20프레임을 교체했습니다. 원래 후보 구간의 속도 초과율은 15.51–194.30%, 보정 뒤 같은 구간은 0–1.403%였습니다. 자세한 수치는 [분석 요약 JSON](example-results.json)에 있습니다.

**후보가 0개가 됐다는 것은 이 설정의 검출 기준을 넘지 않았다는 뜻입니다.** 실제 누락 프레임을 복원했다거나 시각적 결함이 전혀 없다는 증거가 아닙니다. 모션 수치와 함께 보간 경계·얼굴·손·가려지는 영역을 직접 검토하세요.

![실제 분석 UI](images/ui-ko.png)

## 재현

릴리스의 **FrameHoldFinder-v1.0-Examples.zip**을 풀면 `examples` 폴더에 원본 발췌·보정본·비교 영상이 있습니다.

```powershell
.\FrameHoldFinder-CLI.exe analyze "examples\higgsfield-motion-original.mp4"
.\FrameHoldFinder-CLI.exe render "examples\higgsfield-motion-original.mp4" -o "examples\my-corrected.mp4"
.\FrameHoldFinder-CLI.exe analyze "examples\my-corrected.mp4"
```

이 예시는 오디오 검증용이 아닙니다. 별도의 721프레임 원본과 짧은 오디오 포함 테스트에서 오디오 복사와 프레임 수/FPS 유지를 확인했습니다. [검증 내역](VALIDATION.md)
