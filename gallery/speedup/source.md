# How MP4 export got faster
> From 3.6x to 16x real time

The first version rendered at 3.6 times real time. Three changes took it to 16, and we'll look at the numbers for each.
## Four steps, 3.6x to 16x {#speeds .build}

```chart bar
first version: 3.6
skip unchanged frames: 8.3
faster x264 preset: 10.2
JPEG frames: 16
note x: higher is faster
```

The first version rendered at [three point six times](#first-version) real time. A ten minute deck took almost three minutes to export.

[Skipping unchanged frames](#skip-unchanged-frames) more than doubled that, to eight point three.

[A faster x264 preset](#faster-x264-preset) brought it to ten point two.

[JPEG frames](#jpeg-frames) finished the job at sixteen.
## What each change did {#changes}

| Change | Speed | Gain | What it removed |
|---|---|---|---|
| **Baseline** | 3.6x | none | nothing, every frame screenshotted and encoded |
| **Skip unchanged frames** | 8.3x | 2.3x | repeat work on static slides |
| **Faster x264 preset** | 10.2x | 1.2x | encoder CPU time |
| **JPEG frames** | 16x | 1.6x | PNG compression and decoding |

Slides spend most of their time still, so [skipping unchanged frames](#row-2) gave the biggest jump. A frame identical to the last is reused instead of captured again.

[The x264 preset](#row-3) trades a little compression for encoder speed. It gave the smallest gain, about twenty percent.

[JPEG frames](#row-4) are cheaper to produce and to decode than PNG, which gave another sixty percent. Together the changes make export about four and a half times faster.

## Questions {.questions}

- How does it decide a frame is unchanged?
- Does JPEG lower the video quality?
- Which x264 preset is used now?

<!-- revised speeds -->
## Four steps, 3.6x to 16x {#speeds .build}

```chart bar
first version: 3.6
skip unchanged frames: 8.3
faster x264 preset: 10.2
JPEG frames: 16
```

The first version rendered at [three point six times](#first-version) real time. Ten minutes of slides took almost three minutes to export.

[Skipping unchanged frames](#skip-unchanged-frames) more than doubled that, to eight point three.

[A faster x264 preset](#faster-x264-preset) brought it to ten point two.

[JPEG frames](#jpeg-frames) finished the job at sixteen.
