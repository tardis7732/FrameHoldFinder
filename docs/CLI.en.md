# CLI

[한국어](CLI.md) · [English](CLI.en.md) · [Home](../README.en.md)

`FrameHoldFinder-CLI.exe` does not open the GUI. From source, replace it with `python cli.py`.

```powershell
# Analyze only; no SDK required
.\FrameHoldFinder-CLI.exe analyze "D:\clips\input.mov" --report "D:\clips\analysis.json"

# Render included candidates in the supported source format
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov"

# Inspect the plan without generating video
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov" --dry-run

# Use original 3/6 to replace 4/5, and 6/9 to replace 7/8
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\manual.mov" --manual-only --add 3:6 --add 6:9

# Exclude by a key returned by analyze
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mov" -o "D:\clips\fixed.mov" --exclude jump:3:4

# Validate and save the SDK interpreter
.\FrameHoldFinder-CLI.exe sdk-check --python "C:\vfx-env\Scripts\python.exe" --save

# Explicit format conversion
.\FrameHoldFinder-CLI.exe render "D:\clips\input.mp4" -o "D:\clips\fixed.mov" --format prores
```

## Options

| Option | Default / behavior |
| --- | --- |
| `--width` | 960, capped at source width |
| `--threshold` | 5, percentage above both neighboring speeds |
| `--hold-quiet` | 0.5 source pixels |
| `--min-move` | Defaults to actual analysis width × 0.005, measured in source pixels |
| `--support-percent` | 30 percent of eligible tracks |
| `--min-points` | 20 actual tracks |
| `--no-holds`, `--no-jumps` | Disable the respective detector |
| `--add LEFT:RIGHT` | Replace frames between original references; repeatable |
| `--exclude KEY` | Exclude a `candidates[].key`; repeatable |
| `--manual-only` | Skip tracking and use manual ranges only; requires `--add` |
| `--quality` | `LOW`, `MEDIUM`, `HIGH`; default `HIGH` |
| `--format` | `source` default; `prores` explicitly converts to MOV |
| `--report FILE` | Safely save the result JSON as well as printing it |
| `--quiet` | Suppress progress |

All frame numbers are **1-based original frame numbers**. `--add` does not insert frames: references stay intact and only their existing interior is replaced. Manual ranges may overlap only at a shared endpoint. Reference frames are protected too, so conflicting automatic candidates are disabled.

Exclusion keys use `hold:L:R`, `jump:L:R` or `replace:L:R`. Manual ranges can also be excluded. Unmatched keys are reported as warnings. Detection and conflict rules are shared with the GUI.

## Output and exit status

Results are JSON on stdout; progress is JSON Lines on stderr. Help/version output is plain text. `--report` also writes a JSON file.

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Validation or processing failure |
| 2 | Command syntax error |
| 130 | Cancellation |

Neither output nor report may overwrite the source or an alias. Existing results are replaced after validation. With no included repairable frames, `render` reports an error instead of making an unchanged copy; `--dry-run` still returns the plan.
