# Timecode in Live: LTC, burned-in video and MTC chase

This document brings together what `aafton` does with timecode and what the
operator needs to configure in Live 12 for the session to run along with the
picture.

## What Live does not offer for film work

Live 12 is not a timecode machine:

- it generates neither MTC nor LTC: no timecode leaves Live for the picture;
- it has no session start time: the zero of time is the beat zero of the set;
- it does not follow a tempo change while it is under MTC;
- the time ruler can show SMPTE (`LiveSet/SmpteFormat`), but that is display
  only: the number on the ruler synchronizes nothing with the picture;
- the film session uses a fixed tempo.

What Live does is follow an MTC that comes in. The MTC chase is the way for the
set to run along with Pro Tools.

## The LTC track

`aafton import --ltc` creates a generated LTC track in the set. The signal
starts on the first frame at or after the Live origin plus 2 samples and runs
until 10 s after the last clip, at -18 dBFS, mono PCM 24 bits, with the
`time_reference` of the BWF marking the start of the signal. The sidecar marks
the track as `generated`, so the export and the verify leave it out
(`LTC_TRACK_SKIPPED`).

The LTC never starts before 00:00:00:00: a Live origin before zero is moved
forward to frame zero, and the report warns with `LTC_START_CLAMPED`. The
report labels and the BWF description then show 00:00:00:00, the real start of
the signal, and the WAV name comes out with that same start.

With the origin before zero, the MTC chase does not come out with the
`MTC_CHASE_SETTINGS`: Live does not accept a negative SMPTE offset, so the
report warns `MTC_CHASE_ORIGIN_NEGATIVE`. The suggestion of the warning is an
origin between frame zero and the first clip: the upper bound follows the rule
of the import itself, the largest frame it accepts, that is, the frame whose
start, truncated to an integer sample (rounded down), still does not pass the
start of the first clip; the lower bound is frame zero — in drop frame, both
use `;`. An origin after the first clip is refused by the import with
`CLIP_BEFORE_ORIGIN`. An origin of less than half a frame before zero counts
as zero for Live: the chase comes out aligned, with the usual
`MTC_CHASE_SETTINGS` and the offset `00:00:00:00`.

An LTC that would pass the end of the line, 24:00:00:00, is not written: the
report records the error `LTC_TRACK_SKIPPED_24H`, with the start and the end
the signal would have. In strict mode the import stops at the error; in
permissive mode the `.als` is written without the LTC track and without the
WAV.

The generated track starts on the Main output of Live and stays without mute.
Before playing, route that track to a dedicated physical output: as it comes,
the LTC plays on the monitors and goes into the bounce of Main. The name of
the generated track has to be free: the comparison with the tracks of the set
uses the normalized name (case, spaces and punctuation removed, and the `.wav`
suffix left out), so `ltc`, `L-T-C` and `LTC.wav` take the same name as `LTC`.
When the name is taken, the generated track becomes `LTC (aafton)` and, when
that one is taken too, `LTC (aafton) 2`, `LTC (aafton) 3`…

```bash
aafton import cena12.aaf cena12.als --template templates/live12.als --ltc
```

The `aafton ltc` command writes a standalone LTC WAV, without a Live set, to
take to a recorder, to a camera or to a monitor:

```bash
aafton ltc --start 01:00:00:00 --rate 25 --duration 00:10:00:00 ltc.wav
```

The accepted rates are 23.976, 24, 25, 29.97, 29.97df and 30. At a rate with
no LTC (50, 59.94, 60) the `--ltc` answers `LTC_RATE_UNSUPPORTED` and the
import goes on without the track. The end can be given as `--duration` or as
`--end`; the `--duration` is read as a TC label at the chosen rate, with `;`
in drop frame.

The `aafton ltc` refuses a level above 0 dBFS, a sample rate outside the
8000-384000 Hz range, a WAV above the 4 GiB RIFF limit, a start before
00:00:00:00 and an end after 24:00:00:00. In those cases the command does not
write the file, writes the reason on the error output and exits with code 2.

## The video proxy with the burned-in timecode

`aafton import --video VIDEO` burns the timecode into the video proxy of Live,
which comes out as `<stem>_LiveProxy_TC.mp4` (ffmpeg with drawtext). The proxy
is reused when the source, the start and the rate match those of the previous
burn; with `--video-no-burn` the old proxy is kept. Without ffmpeg with
drawtext the old behavior of the tool still applies
(`VIDEO_BURN_UNAVAILABLE`).

When the frame rate of the video is different from the rate of the session,
the burn is skipped with `VIDEO_BURN_RATE_MISMATCH`: the burned-in TC would
come from the clock of the session. The burned proxy comes out with
`+faststart`.

## The timecode page of the Studio

The Studio gets a timecode page. It opens with `aafton ui` and stays at
http://127.0.0.1:8765/timecode; the main page (`/`) did not change.

To use the page:

1. Load into the Studio an `.als` generated by `aafton`.
2. Choose AbletonOSC as the Control Surface in Live. The page talks to Live
   through AbletonOSC (UDP 11000, replies on 11001) and polls the state every
   50 ms.

The state of Live is asked for in a single batch: the tempo, `is_playing` and
`current_song_time`, with the position last. Each AbletonOSC reply arrives
once per update cycle of Live, about 100 ms by its own code; the real cadence
still has to be checked in the test session. The 50 ms is the polling interval
of the page, and each reply is limited by that cycle.

The server of the Studio is multithreaded and the bridge to Live has a single
lock. The video range replies have at most 4 MiB each.

What the page shows and what it does:

- large clock with the frame that holds the Live position;
- the TC of beat zero, read from the sidecar; it can be edited in memory with
  `Apply` and returned to the sidecar value with `Reset`, and the sidecar on
  disk is not written;
- the `Go` field, to send Live to a TC;
- the state of the connection with Live;
- tempo warning when the tempo of Live differs from the BPM of the sidecar;
- the burned-in video, which follows Live.

The clock tolerates the float32 rounding of AbletonOSC: the tolerance is
2 * |beats| * 2**-23 converted into frames (two to four float32 ulps of the
beats value), with a floor of 1e-9 frame. The `Go` field sends the smallest
float32 that stays on the target or after it.

With Live stopped, the video pauses and is looked for again above a one-frame
error; with Live playing, it follows Live and is only looked for again above
four frames. This was not checked inside Live.

Changing the TC of beat zero renames only the clock: the video keeps the
origin of the sidecar.

The page works at any session rate. When the rate has no LTC, it shows in the
`NUM/DEN` format.

The import through the Studio (web page) does not burn the TC and has no LTC
option: for now both things are command line only.

AbletonOSC only answers on the UDP port 11001, so only one program at a time
can listen to Live: with another AbletonOSC client open (for example another
application listening on port 11001), the page warns that the port is in use
and connects on its own when it closes.

The clock and the conversion between beats and TC live in `live_clock.py`,
which uses fixed time and the tolerance of 2 * |beats| * 2**-23 converted into
frames, with a floor of 1e-9 frame. The OSC 1.0 client lives in `live_osc.py`.

## MTC chase on Windows

The signal path is: Pro Tools as MTC master → virtual port of loopMIDI → sync
input of Live.

1. Install loopMIDI and create a virtual port.
2. In Pro Tools, send the MTC to that port.
3. In Live, turn on the MTC sync on the MIDI input that carries the MTC.
4. Set the SMPTE offset equal to the TC of beat zero, the same value that the
   import printed in the MTC_CHASE_SETTINGS notice (the `offset` of the
   context).
5. Use the rate of the session, the same one that the import printed (the
   `rate` of the context).
6. Adjust the Sync Delay to compensate the latency between the master and
   Live.

The exact name of the Live preference fields for the MTC sync and for the
offset was not checked on this machine: the fields need to be checked in the
test session.

## Limits

- Live generates neither MTC nor LTC: the chase is input only.
- The film session uses a fixed tempo.
- The SMPTE ruler of Live is display: it shows the number, it does not
  synchronize.
