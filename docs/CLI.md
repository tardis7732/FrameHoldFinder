# CLI

[한국어](CLI.md) · [English](CLI.en.md) · [처음으로](../README.md)

Windows 패키지의 `FrameHoldFinder-CLI.exe`는 GUI를 열지 않습니다. 소스에서는 저장소 최상위 폴더에서 `python src/cli.py`로 바꿔 실행합니다.

```powershell
# 분석만: SDK 불필요
.\FrameHoldFinder-CLI.exe analyze "D:\clips\input.mov" --report "D:\clips\analysis.json"

# 포함된 자동 후보를 보정: 기본값은 원본 포맷
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov"

# 생성 계획 확인만
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov" --dry-run

# 원본 3·6을 참조해 기존 4·5 교체, 6·9로 7·8 교체
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\manual.mov" --manual-only --add 3:6 --add 6:9

# analyze 결과의 키로 특정 항목 제외
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov" --exclude jump:3:4

# SDK 경로 확인과 저장
.\FrameHoldFinder-CLI.exe sdk-check --python "C:\vfx-env\Scripts\python.exe" --save

# 명시적인 형식 변환
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mp4" -o "D:\clips\fixed.mov" --format prores
```

## 옵션

| 옵션 | 기본값 / 동작 |
| --- | --- |
| `--width` | 960, 원본 폭 이하로 제한 |
| `--threshold` | 5, 앞·뒤 구간 대비 속도 초과 % |
| `--hold-quiet` | 0.5 원본 px |
| `--min-move` | 생략 시 실제 분석 폭 × 0.005, 원본 px 단위 |
| `--support-percent` | 30, 유효 추적점 중 필요한 비율 |
| `--min-points` | 20, 필요한 실제 점 개수 |
| `--no-holds`, `--no-jumps` | 각 자동 감지 끄기 |
| `--add LEFT:RIGHT` | 원본 참조 프레임 사이를 교체, 반복 지정 가능 |
| `--exclude KEY` | `candidates[].key` 제외, 반복 지정 가능 |
| `--manual-only` | 모션 추적을 생략하고 수동 구간만 사용; `--add` 필요 |
| `--quality` | `LOW` / `MEDIUM` / `HIGH`, 기본 `HIGH` |
| `--format` | `source` 기본 / `prores` 명시적 MOV 변환 |
| `--report FILE` | 표준 출력과 같은 JSON을 안전하게 파일로 저장 |
| `--quiet` | 진행 로그 끄기 |

프레임 번호는 모두 **1부터 시작하는 원본 프레임 번호**입니다. `--add`는 프레임 삽입이 아니며, 양끝 참조는 그대로 두고 사이만 교체합니다. 여러 수동 구간은 양끝 참조 하나만 공유할 수 있습니다. 참조 프레임도 보호하므로 충돌하는 자동 항목은 비활성화됩니다.

`--exclude` 키는 `hold:L:R`, `jump:L:R`, `replace:L:R` 형식입니다. 수동 항목도 제외할 수 있고, 일치하지 않는 키는 경고에 표시합니다. 자동 후보와 수동 항목의 규칙은 GUI와 같습니다.

## 출력과 종료 코드

결과는 stdout의 JSON, 진행 상황은 stderr의 JSON Lines입니다. 도움말과 버전은 일반 텍스트입니다. `--report`는 별도 JSON 파일도 저장합니다.

| 코드 | 의미 |
| --- | --- |
| 0 | 성공 |
| 1 | 입력 검증 / 처리 실패 |
| 2 | 명령 문법 오류 |
| 130 | 취소 |

원본 경로와 그 별칭에는 결과나 보고서를 저장할 수 없습니다. 기존 결과는 새 렌더 검증 후 교체됩니다. 포함된 생성 대상이 없으면 `render`는 복사본을 만들지 않고 오류를 반환합니다. `--dry-run`은 이 경우도 계획을 확인할 수 있습니다.
