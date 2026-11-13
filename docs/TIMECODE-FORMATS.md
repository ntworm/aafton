# Where the timecode lives in the project files — Live 12 (.als) and Pro Tools (.ptx)

The tables below come from the analysis of the variants saved in `out/p00/`;
no number here comes from outside that measurement.

## Objective

The timecode work needs to know which fields of the project files carry
timecode, to read the rate and the origin of the session and to generate the
LTC track on the same reference. This document records, for Live 12 (`.als`)
and for Pro Tools (`.ptx`), which fields hold that information, how they were
measured and what was left out.

## Method

Saved variants, each one with a single difference relative to a base.

Live 12 (`.als`):

- `tc_00_base`: the base, with `LiveSet/MainTrack/DeviceChain/Mixer/Tempo/Manual`
  equal to 120.
- `tc_01_regua_smpte25`: ruler in SMPTE 25 fps.
- `tc_02_regua_smpte2997`: ruler in 29.97 drop frame.
- `tc_03_locator`: one locator.
- `tc_04_video_sem_warp`: one video clip without warp.
- `tc_05_video_com_warp`: the same video clip with warp.

Pro Tools (`.ptx`):

- base at 25 fps, starting at 01:00:00:00.
- start at 10:00:00:00.
- rate at 29.97 df.
- rate at 24.

The `.als` is XML compressed with gzip; the comparisons were made after
decompressing it. The body of the `.ptx` is obfuscated with XOR from byte 0x14
(byte 0x12 = type 0x05, byte 0x13 = seed). The body was unscrambled with the
publicly documented schema of the format and read as a tree of blocks (marker
0x5A, block type u16, size u32, content type u16, little endian); the
comparison was made block by block.

Neither Live nor Pro Tools executable was opened or decompiled.

The simple diff between the variants answered the structure of the format
directly from the project files.

## Live 12 (.als)

| Variant | Field | Type | Value |
|---|---|---|---|
| `tc_00_base` | LiveSet/SmpteFormat | int, enum of the ruler format | 0 |
| `tc_01_regua_smpte25` | LiveSet/SmpteFormat | int, enum of the ruler format | 2 |
| `tc_02_regua_smpte2997` | LiveSet/SmpteFormat | int, enum of the ruler format | 4 |
| `tc_03_locator` | LiveSet/Locators/Locators/Locator/Time | float, beats | 16 |
| `tc_03_locator` | LiveSet/Locators/Locators/Locator/Name | string | TESTE |
| `tc_03_locator` | LiveSet/Locators/Locators/Locator/IsSongStart | bool | false |
| `tc_04_video_sem_warp` | AudioClip/CurrentStart | float, beats | 0 |
| `tc_04_video_sem_warp` | AudioClip/CurrentEnd | float, beats | 19.911999979187478 |
| `tc_04_video_sem_warp` | AudioClip/Loop/LoopEnd | float, seconds without warp, beats with warp | 9.9559999999999995 |
| `tc_04_video_sem_warp` | AudioClip/IsWarped | bool | false |
| `tc_04_video_sem_warp` | AudioClip/SampleRef/FileRef/RelativePath | string, relative path | ../video_teste_25fps.mp4 |
| `tc_05_video_com_warp` | AudioClip/CurrentStart | float, beats | 0 |
| `tc_05_video_com_warp` | AudioClip/CurrentEnd | float, beats | 19.911999979187478 |
| `tc_05_video_com_warp` | AudioClip/Loop/LoopEnd | float, seconds without warp, beats with warp | 19.911999979187478 |
| `tc_05_video_com_warp` | AudioClip/IsWarped | bool | true |
| `tc_05_video_com_warp` | AudioClip/SampleRef/FileRef/RelativePath | string, relative path | ../video_teste_25fps.mp4 |
| `tc_00_base` | LiveSet/MainTrack/DeviceChain/Mixer/Tempo/Manual | float, BPM | 120 |

`LiveSet/SmpteFormat` only chooses how the time ruler is drawn. 0 (Time),
2 (25 fps) and 4 (29.97 drop frame) were measured. By the order of the menu
(Time, 24, 25, 30, 29.97 DF, 29.97 ND) the values 1 = 24, 3 = 30 and 5 = 29.97
without drop are expected, but they were not measured.

The locator holds the position in beats (`Time` 16 = bar 5 in 4/4) and the
name (`TESTE`); `IsSongStart` came `false`.

The video clip is an `AudioClip`. Without warp, the end of the loop is in
seconds (9.956); with warp, the same field is in beats (19.912 at 120 BPM).
The `CurrentEnd` is 19.911999979187478 in both cases.

The diff also shows other time fields. None of them is session timecode:
`Transport/CurrentTime` and the fields of `TimeSelection` are edit state, and
the `WarpMarkers` are the map from seconds to beats of the clip.

| Variant | Field | Type | Value |
|---|---|---|---|
| `tc_01_regua_smpte25` | LiveSet/Transport/CurrentTime | float, beats | 112 |
| `tc_01_regua_smpte25` | LiveSet/TimeSelection/AnchorTime | float, beats | 112 |
| `tc_01_regua_smpte25` | LiveSet/TimeSelection/OtherTime | float, beats | 112 |
| `tc_04_video_sem_warp` | AudioClip/WarpMarkers/WarpMarker (SecTime, BeatTime) | float, s and beats | 0 s -> 0 beats; 0.015625 s -> 0.03125 beats |
| `tc_05_video_com_warp` | AudioClip/WarpMarkers/WarpMarker (SecTime, BeatTime) | float, s and beats | 0 s -> 0 beats; 0.015625 s -> 0.03125 beats |

No variant showed a session start field in the `.als`.

## Pro Tools (.ptx)

| Variant | xor type/seed | 0x204d +0 (rate code) | 0x204d +9 (u32 LE, start in frames) | 0x204d +40 (rate code, copy) | 0x204d +44 (u32 LE, start, copy) | 0x204b +161 (u8 or u32 LE, rate code) | 0x204d +29..+39 (ASCII, duration limit) | 0x204d +56..+66 (ASCII, limit, copy) |
|---|---|---|---|---|---|---|---|---|
| base (25 fps, 01:00:00:00) | 0x05/0xf9 | 2 | 90000 | 2 | 90000 | 2 | 24:00:00:00 | 24:00:00:00 |
| start 10:00:00:00 | 0x05/0x39 | 2 | 900000 | 2 | 900000 | 2 | 24:00:00:00 | 24:00:00:00 |
| 29.97 df | 0x05/0xfd | 5 | 107892 | 5 | 107892 | 5 | 24:00:00;00 | 24:00:00;00 |
| 24 fps | 0x05/0x07 | 1 | 86400 | 1 | 86400 | 1 | 24:00:00:00 | 24:00:00:00 |

The `xor type/seed` column shows the type 0x05 and the seed of each variant.

The block with content type 0x204d holds the timecode record twice: the rate
code at offset +0 and again at +40 (1 = 24, 2 = 25, 5 = 29.97 df measured),
the start of the session as u32 little endian at +9 and again at +44, counting
frames of the session rate (90000 = 01:00:00:00 at 25 fps, 900000 =
10:00:00:00, 107892 = 01:00:00;00 at 29.97 df, 86400 = 01:00:00:00 at 24),
and the text of the duration limit at +29..+39 and again at +56..+66.

The bytes +1..+3 and +41..+43 of the 0x204d block are zero in the four
variants: the rate code at +0 and at +40 can be u8 or u32 LE.

The text of the limit comes after a pair of u32 little endian: the value 5 at
+21..+24 and the length of the text, 11, at +25..+28. The copy repeats the
pair at +48..+51 and +52..+55 before the text at +56..+66.

The text at +29 and at +56 is the duration limit of the session: 24:00:00:00,
with ';' before the frames in drop frame mode.

The 0x204b block repeats the rate code at +161, and the bytes +162..+164 are
zero in the four variants: the code at +161 can also be u8 or u32 LE. The
blocks 0x2067, 0x2016, 0x2587 and 0x0002 change on every save and were not
interpreted.

Anyone writing a `.ptx` needs both copies of the record: in the 0x204d the
rate is at +0 and +40, the start at +9 and +44 and the limit, preceded by the
5 and 11 pair, at +29..+39 and +56..+66.

## Not found or not measured

- Live rate codes 1, 3 and 5.
- An Arrangement start offset (no variant).
- The MTC sync offset of Live (it stays in the preferences, it was not
  tested).
- Pro Tools rate codes for 23.976, 29.97 without drop, 30 and 30 drop.
- The Feet+Frames and Timecode 2 rate fields.
- Reading the `.ptx` inside the product (outside the scope of this work).

## Use

The importer can write `LiveSet/SmpteFormat` from the rate of the session
only in the measured codes. The clock that follows the LTC track uses the
origin of the sidecar, because the `.als` has no start time.
