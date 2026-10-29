# Real footage example — correcting a jump segment

[한국어](EXAMPLES.md) · [English](EXAMPLES.en.md) · [Home](../README.en.md)

![34-frame jump comparison at 0.25x](images/jump-comparison-quarter-speed.gif)

[Full normal-speed comparison](media/comparison-original-corrected.mp4) · [Download sample media](https://github.com/tardis7732/FrameHoldFinder/releases/latest)

In the comparison, the corrected view on the right shows an **orange border only on replaced frames**, alongside the frame number and cumulative replacement count (**11 total**). The downloadable `jump-corrected.mp4` has no overlays.

## Trim first, then analyze

The footage was generated with Seedance and downloaded from the author's Higgsfield account. **Frames 111–144 of the previous 144-frame example are extracted first** as `jump-original.mp4`, then this **34-frame file is analyzed afresh**. The analysis is not a cropped view of the full video's results.

- Source and corrected files: **1920×1080, 24fps, 34 frames, about 1.417 seconds**, silent
- Add **110** to the new frame numbers **1–34** to recover **111–144** in the previous example.
- Stored as H.264 High / yuv420p. No frame duplication, insertion, deletion within the selected range or retiming was introduced to manufacture a defect.
- The GIF above compares all 34 frames at **0.25x**. Source, corrected and full comparison MP4 files play at normal speed.

## Detection and replacement

A **+37.1% speed threshold** detects 11 jump candidates. **The application default remains +5%.** Other settings are width 960, minimum motion 4.8px, support 30%, minimum 20 points and hold tolerance 0.5px.

Replaced frames on the new timeline: **3, 5, 7, 11, 13, 15, 19, 23, 27, 29, 31**. Each is generated at the midpoint of its original neighbors. For example, the **2→3 jump uses references 2 and 4 to generate frame 3**; in the previous example's numbering, that is **112 and 114 generating 113**.

![Analysis UI for the trimmed 34-frame video](images/ui-en-zoom.png)

| Result | Before | Reanalyzed with the same settings |
| --- | ---: | ---: |
| Jump candidates (+37.1% threshold) | 11 | 0 |
| Hold candidates | 0 | 0 |
| Speed excess at the first jump, 2→3 | 194.561% | 0.631% |
| Total frames | 34 | 34 |
| Frame rate | 24fps | 24fps |
| Duration | About 1.417 seconds | About 1.417 seconds |

After correction, speed excess at the 11 original candidate intervals ranges from **0 to 0.876%**. These results use the +37.1% settings above. Changing the threshold, including returning to the +5% default, can change the candidate results. Zero candidates alone does not establish recovery of missing original frames or removal of every visual artifact. [Analysis summary JSON](example-results.json)

## Reproduce

Extract **FrameHoldFinder-v1.0-Examples.zip** from the release. Its `examples` folder contains `jump-original.mp4`, `jump-corrected.mp4` and the comparison. The source file is already trimmed to the selected 111–144 range.

```powershell
.\FrameHoldFinder-CLI.exe analyze "examples\jump-original.mp4" --threshold 37.1
.\FrameHoldFinder-CLI.exe render "examples\jump-original.mp4" --threshold 37.1 -o "examples\my-corrected.mp4"
.\FrameHoldFinder-CLI.exe analyze "examples\my-corrected.mp4" --threshold 37.1
```

This silent example does not demonstrate audio preservation. Copying audio and preserving frame count/FPS were checked on separate audio-bearing footage. [Validation](VALIDATION.md)
