# Exchange formats and MXF

`aafton` reads and writes three exchange formats — EDL CMX 3600, Final Cut
Pro 7 XML (xmeml) and FCPXML — and extracts the audio of MXF files to WAV.
This page describes what the code does.

The automatic checks ran on scene CENA12, whose samples were written by hand
from the specifications of each format (`tests/fixtures/formatos/`). Real
exports of Premiere, Resolve and FCP have not gone through `aafton` yet:
the script that produces that proof is in
[FORMATS-ACCEPTANCE.md](FORMATS-ACCEPTANCE.md).

## Installation

The optional `formats` extra pins OpenTimelineIO and the three reading
adapters: `opentimelineio==0.18.1`, `otio-cmx3600-adapter==1.0.0`,
`otio-fcp-adapter==1.0.0` and `otio-fcpx-xml-adapter==1.0.0`. Only the EDL and
the FCP XML use those adapters: the FCPXML is read by the code of the product.

```powershell
pip install 'aafton[formats]'
```

Without the extra, the package stays installed and the AAF keeps working:
neither the AAF import nor the AAF export depends on OpenTimelineIO. Reading an
EDL or an XML fails with `FORMAT_EXTRA_MISSING`, and exporting to FCP XML
fails with the same code; exporting EDL and FCPXML works, because both writers
belong to the product itself. The FCPXML is also read without the extra,
because the FCPXML reader belongs to the product itself.

## The four commands

The format comes from the suffix of the file; there is no format option on the
command.

### import

```powershell
aafton import ORIGEM.EDL SESSAO.als --template templates/live12.als --edl-rate 25 --audio-dir C:\Midia
```

The import of an EDL, XML or FCPXML runs the same pipeline as the AAF: the
media is looked for by name in `--audio-dir` and in the folder of the input
file, and the stereo pairing (`--stereo`), the external video (`--video`), the
`.aafton.json` sidecar and the LTC track (`--ltc`) are the same ones.

The EDL does not declare the frame rate in a way the reader can use: it needs
`--edl-rate`, with one of these values: `23.976`, `24`, `25`, `29.97`,
`29.97df`, `30`. Without the option, the reading stops with
`FORMAT_EDL_RATE_REQUIRED`.

### inspect

```powershell
aafton inspect ORIGEM.FCPXML
```

It dumps the SessionIR as JSON. It accepts AAF, ALS, EDL, XML and FCPXML; for
an EDL it accepts `--edl-rate`. The JSON goes to the standard output and the
report errors go to the error output: an exchange file with an error exits
with code 1.

### export

```powershell
aafton export SESSAO.als SAIDA.FCPXML
```

The suffix of the output chooses the format: `.aaf`, `.edl`, `.xml` or
`.fcpxml`. An unknown suffix is an error (`FORMAT_UNKNOWN_SUFFIX`), never an
empty file with the wrong name.

### verify

```powershell
aafton verify ORIGEM.EDL SESSAO.als
```

It accepts AAF, EDL, XML or FCPXML as origin and compares with the `.als`. The
EDL has no `--edl-rate` here: the rate comes from the `.aafton.json` sidecar
of the `.als` itself.

The media of the origin is looked for first in the folders of the files that
the `.aafton.json` sidecar of the `.als` records, in the order in which the
sidecar writes them, and only afterwards in the folder of the origin file.
With the origin in EDL and the media imported from another folder, the
`verify` finds the file by the path of the sidecar; and a copy with the same
name in the folder of the origin does not fail the `verify` by ambiguity. The
sidecar writes the absolute path of each file, so a relative `--audio-dir`
also serves the `verify`. When the file exists at the absolute path that the
sidecar records, the `verify` uses that file and does not look in the folders:
a copy with the same name next to the origin does not become an ambiguity.

An origin in FCPXML goes through the same channel check as the import: the
clip of the file with more than two channels stays out of the scene on both
sides and does not count as missing; the `verify` fails on the error of the
origin.

## What the reading delivers

| | EDL CMX 3600 | FCP 7 XML | FCPXML 1.9 |
|---|---|---|---|
| Audio tracks | one per audio channel, in the order of the channel number | one per audio track | one per lane: the main storyline becomes lane 0, and the lane of an item is the lane of the parent plus the lane it declares; each clip that overlaps the one of its own lane goes to an extra track |
| Frame rate | comes from `--edl-rate` | from the file | from the `frameDuration` of the sequence format |
| Session start | the first clip | the timecode of the sequence | the `tcStart` of the sequence |
| Markers | the format has none | read | read, in the spine, in the gaps, in the connected clips and in the attached items (title, generator and audio) |
| Sample rate | 48 kHz, assumed (`FORMAT_SAMPLE_RATE_ASSUMED`) | the one of the file, which carries the rate of the session | the one of the `audioRate` of the file |
| Video | skipped (`FORMAT_VIDEO_TRACK_SKIPPED`) | skipped | skipped |

The track name comes from the file; when the file does not name the track, the
reader uses `A1`, `A2`, `A3`... in the order in which the tracks appear. In the
EDL the name of the audio track is the channel of the file (`A1`, `A2`,
`A3`...), and the audio tracks come back in the order of the channel number,
and not in the order of the first event of each one.

Every time conversion uses the rate of the time itself, in exact fraction. In
the EDL and in the XML, the frame rate of the session comes from the first
time of the scene that is already in frames; if the scene has none, it comes
from the rate the file declares; if the file does not declare it either, the
reading stops with `FORMAT_RATE_UNSUPPORTED`. In the FCPXML the rate comes
from the `frameDuration` of the sequence format, and its `tcFormat` (`DF` or
`NDF`) decides if the timecode counts drop frame.

A file the adapter cannot read does not bring the run down: the error becomes
`FORMAT_PARSE_ERROR` in the report and the scene stays empty.

When the media of an EDL or of an XML resolves to a file with more than one
channel, the reading gives the error `FORMAT_MULTICHANNEL_UNSUPPORTED`:
those two formats carry one channel per track and do not load the whole pair.
The FCPXML also refuses the file with more than two channels, with the same
code, because the import does not extract a channel from an exchange file.

### The source timecode of the clips

The offset inside the media file comes from two sources: the exchange
document, when it declares the range of the media, and the `bext` of the WAV,
when it does not declare it.

The EDL is read through the `bext`: the file declares, for each event, the
source timecode, and the reader turns that timecode into an offset inside the
file using the `time_reference` of the `bext` chunk. A file without `bext`
gives `FORMAT_SOURCE_TC_UNRESOLVED`, a file at a rate different from the
session gives `FORMAT_SOURCE_TC_RATE_MISMATCH` and a timecode outside the
file gives `FORMAT_SOURCE_TC_OUTSIDE_MEDIA`. They are errors: in strict mode
the conversion stops at the first of them; in permissive mode the reading goes
on and the report records the problem.

In the FCP XML, the offset comes from the document itself: it is the source
time of the clip minus the start of the file that the document declares, the
`available_range` of the media. When the adapter does not deliver that range,
or when the clip starts before the file, the reader keeps the source timecode
of the clip and the `bext` of the WAV resolves the offset, with the same
errors above.

In the FCPXML the `bext` of the WAV does not come in: the reader of the
product always computes the offset with the `start` of the asset and the
`audioStart` of the clip, and the clip does not carry a source timecode for
the `origem.py` to resolve.

### The FCPXML is read by the product

The FCPXML does not go through OpenTimelineIO: the file is read by
`src/aafton/formats/fcpxml_read.py`, with the standard library and the exact
fraction arithmetic. The walk of the spine is in `fcpxml_itens.py` and the
audio rules of the clip, in `fcpxml_audio.py`. The adapter of the library
rounds every time to the whole frame, ignores the gain, the fades, the filters
and the audio of the main storyline; that is why the product reads the FCPXML
with its own code. The reader of the product refuses what it cannot read:
fade, effect, audio enhancement, gain with a curve, a pan that is not neutral,
the choice of part of the channels, the choice of channels out of order and a
file with more than two channels, each one with its own code in the report.
The clip of a file with more than two channels stays out of the scene.

What the reader of the product does:

- The times are read as an exact fraction of a second (`3600s`, `100/2500s`),
  at any rate: 23.976, 29.97 (with and without drop frame) and 59.94 fps. The
  position is not rounded to the frame; when the time does not fall on a whole
  sample, the report warns with `FORMAT_SUBSAMPLE_POSITION`.
- The scene is the sequence of the `<project>` of the document. A compound clip
  brings its own sequence in the resources, and that sequence is not the scene
  of the project. A document with more than one project is an error
  (`FORMAT_FCPXML_MULTIPLE_PROJECTS`) and the first project is the one that is
  read.
- The spine is walked in the order of the document: a `gap` and an
  `asset-clip` that are direct children of the spine are primary items; the
  children of a `gap` or of an `asset-clip` are the connected clips, the
  storylines and the markers.
- The children of a `title`, of a `<video>` generator and of an `<audio>` are
  also read: the clip, storyline and marker attached to them enter the scene,
  and the fade or the effect of an attached item is the error of the attached
  item, not of the parent.
- The text of a `title` or of a `caption` — the `<text>` and the
  `<text-style-def>` that the FCP writes — is metadata and stays out: it does
  not become an error of the clip.
- Every position is absolute time of the sequence: the position of an item of
  the spine is already sequence time, and the one of a connected item or of a
  marker inside a clip or a gap is the local time of the container plus the
  point at which the container is.
- A secondary storyline is read as a container: it stays in the lane of the
  parent plus the lane it declares, and its items count from zero.
- An `<audio>` item is read as the audio of an `asset-clip`, and the items
  attached to it are also read. The `<audio>` item goes through the same
  effect check as an `asset-clip`: a fade, a connected `filter-audio`, a
  `conform-rate` with scale, a `timeMap` or one of the six audio enhancements
  connected to it is the error of the `<audio>` item, with its name, and not
  of the clip that carries it.
- The `asset-clip` of the main storyline that has audio becomes a clip in lane
  0, and the connected clips are read by the lane of the parent plus the lane
  they declare.
- An `asset-clip` of a stereo file is a single clip, in a track of two
  channels.
- An `audio-channel-source` with a single channel, in a file of one channel,
  chooses that channel; in a stereo file, choosing part of the channels is
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`, and the clip keeps the whole file.
- A file with more than two channels is `FORMAT_MULTICHANNEL_UNSUPPORTED`, in
  the reader and in the media check: the import does not extract a channel
  from an exchange file. The clip stays out of the scene: in permissive mode
  the `.als` is written without it. It holds both when the document declares
  the channels of the file and when it is only the media check that finds the
  file with more than two channels: in both cases the clip leaves the scene.
  The track that loses the clip and is left with no clip at all also leaves
  the scene, and the one that keeps a clip comes to have the number of
  channels of the clips that stayed. The `verify` runs the same check on the
  origin: it fails on the error of the origin, and the clip that left the
  scene does not count as missing.
- The six audio enhancements of the FCP (`adjust-EQ`, `adjust-matchEQ`,
  `adjust-loudness`, `adjust-noiseReduction`, `adjust-humReduction` and
  `adjust-voiceIsolation`) are `FORMAT_EFFECT_UNSUPPORTED`: the reader does
  not know how to apply them. The error comes out in the clip, in the
  `<audio>` item or in the audio component that carries them, and the clip
  stays in the scene. A disabled enhancement (`enabled="0"`) does not change
  the sound and passes without an error, and the same holds for the
  enhancement that is inside a disabled component (`enabled="0"` or
  `active="0"`) and for the `adjust-EQ` with `mode="flat"` and without a
  parameter. The video adjustments that the reader knows (`adjust-crop`,
  `adjust-corners`, `adjust-conform`, `adjust-transform`, `adjust-blend`,
  `adjust-stabilization`, `adjust-rollingShutter`, `adjust-360-transform`,
  `adjust-reorient`, `adjust-orientation`, `adjust-cinematic`,
  `adjust-colorConform` and `adjust-stereo-3D`) are ignored, like every
  video adjustment.
- An `adjust-` that is not a known video adjustment, nor the `adjust-volume`,
  nor the `adjust-panner`, nor one of the six audio enhancements, is
  `FORMAT_EFFECT_UNKNOWN`: the reader does not know what it does. The warning
  comes out in the clip, in the `<audio>` item or in the audio component that
  carries it, with the name of the clip and of the element, and the import
  goes on in strict mode and in permissive mode, with the clip in the scene. A
  disabled adjustment (`enabled="0"` or `active="0"`) does not change the
  sound and does not give a warning, and the clip without audio does not give
  a warning either: it has no adjustment that the reader does not know.
- A constant `adjust-volume` becomes the gain of the clip: the level comes
  from the `amount` attribute or from the `<param name="amount" value="...">`
  parameter; without either of them, it is 0 dB. With a keyframe curve, it is
  `FORMAT_GAIN_UNSUPPORTED`.
- The `audioStart` and the `audioDuration` of a clip — the audio cut of a
  split edit — mark the start and the duration of the audio: the audio starts
  at the sequence time corrected by the cut and lasts the `audioDuration`.
- A J or L cut extends the audio of a clip over the neighbour: the two clips
  stay in the same lane and overlap. Since a track of Live plays one clip at a
  time, the overlapping clip goes to an extra track of the lane, with the
  note `FORMAT_OVERLAP_MOVED_TO_TRACK`. No track is left with overlapping
  clips.
- A `conform-rate` with `scaleEnabled="0"` is accepted: it does not change
  the speed of the clip.
- A `chapter-marker` becomes a marker.
- `keyword`, `rating`, `note`, `metadata`, `analysis-marker` and
  `hidden-clip-marker` are ignored: they are metadata and do not take time.
  The `object-tracker` (video tracking) is also ignored. An
  `audio-role-source` that only has a role is also ignored; one that changes
  the sound (disabled, volume, mute or keyframe) is
  `FORMAT_EFFECT_UNSUPPORTED`.
- `title`, `video`, `caption` and `live-drawing` are video items: the reader
  skips them and warns once with `FORMAT_VIDEO_TRACK_SKIPPED`, but the items
  attached to them are read. The `<text>` and the `<text-style-def>` of the
  title are ignored as metadata, and not as a story item.

The audio rules of the clip:

- An audio component that is disabled (`enabled="0"` or `active="0"`) does
  not sound. A clip with components and all of them disabled stays out of the
  scene (`FORMAT_CLIP_DISABLED`).
- A clip with `enabled="0"` stays out of the scene, and the clips attached to
  it stay in the scene: they play in the FCP even with the clip disabled.
- Exactly one connected component, in an asset of one channel, chooses the
  channel of the clip; in a stereo asset, the clip keeps all the channels of
  the asset. More than one connected component is
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`.
- Choosing part of the channels of a multichannel asset — a `srcCh` of a
  single channel, a `srcCh` above the channels of the file, a `srcID`
  different from 1, a component cut in another range or routed to an output
  that is not the centre (C) or the pair L, R — is
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`: the product does not extract a
  channel from an exchange file, and the clip would play the whole file.
- An asset that declares fewer channels than the file has is
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED` when the clip chooses channels: the
  document says one channel and the clip chooses channel 1, but the file is
  stereo and Live would play both. The report says the clip, the file and the
  two numbers. In strict mode the conversion stops; in permissive mode the
  clip stays in the scene, playing the whole file. Without a channel choice
  there is no error.
- An asset that does not declare the number of channels (`audioChannels`)
  does not serve to choose channels: the reader assumed a single channel and
  does not know that the choice is partial, so the choice is also
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`, and the report says that the
  document does not declare the number of channels.
- The `outCh` of a component is the centre (C) or the pair L, R in that order:
  in a file of one channel, `C`, `L, R` and `1, 2` pass; in a stereo file,
  only `L, R` and `1, 2` pass. Without `outCh`, the whole file is played. Any
  other output is `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`.
- The choice of channels that is not in increasing order — a `srcCh="2, 1"`
  of a stereo file — is `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`: Live plays the
  file in its own order, and the product does not swap the sides.
- A component that repeats the range of the audio of the clip — or, in a
  split edit, the range of the video — cuts nothing and passes; a component
  cut in another range is `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`.
- The constant volume of the connected component is the gain of the clip,
  together with the constant volume of the clip itself; the level comes from
  the `amount` attribute or from the `<param name="amount" value="...">`
  parameter, and an `adjust-volume` without either of them is 0 dB.
- A `mute` without start nor duration mutes the whole clip
  (`FORMAT_CLIP_DISABLED`); a `mute` with a range is
  `FORMAT_GAIN_UNSUPPORTED`.
- A `srcEnable="video"` leaves the clip without audio: no audio clip is read
  for it.
- An `adjust-panner` that is not neutral is `FORMAT_PAN_UNSUPPORTED`: the IR
  of the product has no pan. The level comes from the `amount` attribute or
  from the `<param name="amount" value="...">` parameter, like the one of the
  volume, and a panner with keyframe is also an error.

What the reader of the product refuses, and the warning it gives:

| What | Code |
|---|---|
| clip with `enabled="0"`, which stays out of the scene | `FORMAT_CLIP_DISABLED` |
| `adjust-volume` with keyframe | `FORMAT_GAIN_UNSUPPORTED` |
| `mute` with range | `FORMAT_GAIN_UNSUPPORTED` |
| fade (`fade-in`, `fade-out`) in the clip or in the `<audio>` item | `FORMAT_FADE_UNSUPPORTED` |
| connected `filter-audio`, `conform-rate` with `scaleEnabled="1"`, `timeMap` in the clip or in the `<audio>` item | `FORMAT_EFFECT_UNSUPPORTED` |
| `adjust-EQ`, `adjust-matchEQ`, `adjust-loudness`, `adjust-noiseReduction`, `adjust-humReduction` or `adjust-voiceIsolation` in the clip, in the `<audio>` item or in the audio component | `FORMAT_EFFECT_UNSUPPORTED` |
| `adjust-panner` that is not neutral, by the `amount` attribute or by the `amount` parameter | `FORMAT_PAN_UNSUPPORTED` |
| `adjust-` that the reader does not know, in the clip, in the `<audio>` item or in the audio component (warning: the clip stays in the scene) | `FORMAT_EFFECT_UNKNOWN` |
| `audio-role-source` that changes the sound (disabled, volume, mute or keyframe) | `FORMAT_EFFECT_UNSUPPORTED` |
| the choice of part of the channels of a multichannel file, a `srcCh` above the channels of the file, a `srcCh` out of increasing order, a `srcID` different from 1, a component cut in another range or routed to an output that is not the centre (C) or the pair L, R, two connected audio components or a channel choice in an asset that does not declare the number of channels | `FORMAT_CHANNEL_SELECTION_UNSUPPORTED` |
| file with more than two channels, whose clip stays out of the scene | `FORMAT_MULTICHANNEL_UNSUPPORTED` |
| `ref-clip`, `sync-clip`, `mc-clip`, `clip` (compound) and `audition` | `FORMAT_NESTED_UNSUPPORTED` |
| any other story item, with the name of the element in the report | `FORMAT_NESTED_UNSUPPORTED` |

### The drop frame of the EDL

The CMX 3600 adapter of OpenTimelineIO only counts drop frame when the label
of the timecode uses `;`. The product rewrites the text of the file before
handing it to the adapter (`src/aafton/formats/edl_fix.py`): when the session
counts drop frame — by the `--edl-rate 29.97df` or by the
`FCM: DROP FRAME` line of the file itself — the timecodes of the event lines
are counted in drop frame, with `;` in place of the `:` separator.

A file that already marks some label with `;` says what it is: it is reliable
as it is and is not rewritten. That way a source timecode that does not count
drop frame keeps no drop frame, even in the session that counts; rewriting all
the labels would turn that source into a drop frame source, and the record of
the clip would stop matching the origin.

The event lines are recognized by the event number and by the rest of the
line, in any number of digits: `1 AX` and `0001 AX` are events.

A timecode with `;` in a session that does not count drop frame is an error
(`FORMAT_EDL_DROP_FRAME_MISMATCH`), and the same holds for a
`FCM: DROP FRAME` at a rate that does not count drop frame. When the `FCM` of
the file declares drop frame and the session is at 29.97 without drop frame,
the file rules: the reading is done in drop frame and the report brings the
note `FORMAT_EDL_DROP_FRAME_FROM_FCM`.

The opposite way has a name: `FCM: NON-DROP FRAME` in a session that counts
drop frame is the warning `FORMAT_EDL_FCM_MISMATCH`, and the reading is done
in drop frame, because it is the rate of the session that rules the count.

When the `FCM` lines of the file disagree with each other — one of drop
frame and another of non-drop frame — the file is read as the first line
rules and as the rate of the session, and the report brings the warning
`FORMAT_EDL_FCM_MIXED`.

### The XML of FCP 7

The XML of the FCP is read by the `fcp_xml` adapter of OpenTimelineIO, with
two adjustments of the product: the `<enabled>FALSE</enabled>` of a clipitem
and the audio filters that Premiere writes in every audio clipitem.

- A disabled clipitem stays out of the scene and becomes the error
  `FORMAT_CLIP_DISABLED`.
- A disabled track (`<enabled>FALSE</enabled>`) is left with no clip at all
  and becomes the error `FORMAT_TRACK_DISABLED`.
- A disabled clipitem without `id` is found by the track it is in and by its
  order inside the track.
- The `Audio Levels` filter of Premiere, with a constant level, becomes the
  gain of the clip; the level 1 is the neutral one. With a curve, it is
  `FORMAT_GAIN_UNSUPPORTED`.
- The `Audio Pan` filter with pan 0 is the neutral one and is not an error;
  another pan is `FORMAT_EFFECT_UNSUPPORTED`.
- Any other filter is `FORMAT_EFFECT_UNSUPPORTED`, and the clip stays in the
  scene.

An XML of an FCP 7 project can have more than one sequence outside a
`clipitem`: the one of the scene and the ones in the bin. The file does not
say which of them is the scene, and that is the error
`FORMAT_XML_MULTIPLE_SEQUENCES`, which brings the names of the sequences. The
state of the tracks comes from the sequence that was read, and not from the
others.

## What the writing carries and what it loses

| | EDL | FCP 7 XML | FCPXML 1.9 |
|---|---|---|---|
| Precision of the positions | frame, with rounding | frame, with rounding | exact fraction of a second, without rounding |
| Markers | lost, with a warning | carried | carried |
| Fades, clip gain and muted clip | error | error | error |
| Automation of gain or of pan of the track | error | error | error |
| Muted track, track volume and track pan | error | error | error |
| File with more than one channel | error | error | carried, with the channels of the file |
| Audio rate | — | the rate of the session | 44.1 kHz, 48 kHz or 96 kHz |
| Writer | own code | adapter `fcp_xml` | own code |

### EDL

The EDL writer belongs to the product: the `cmx_3600` writer of OpenTimelineIO
refuses a scene without a video track and, with the track empty, writes only
the `TITLE` and loses every audio event. The written EDL brings the `TITLE:`
header with the name of the session, the `FCM:` line with the drop frame of
the session, one event per clip — with the channel (`A`, `A2`, `A3`...), the
reel and the source and record timecodes — and the `* FROM CLIP NAME:` and
`* SOURCE FILE:` comments of each event.

The `source out` is the `source in` plus the length of the record. That stays
valid in CMX 3600 even when the lengths are not multiples of frame: the event
has the same duration in the origin and in the record.

The reel of each file is unique inside the EDL: it is the base of the name of
the file, in upper case and with at most 8 characters. Two files with the same
beginning of name cannot use the same reel, and the second one receives the
short name, with up to 6 characters, and a two-digit counter.

The EDL has no marker: the report warns with `FORMAT_EDL_MARKERS_DROPPED`. The
EDL accepts at most 999 events; above that the writing stops with
`FORMAT_EDL_TOO_MANY_EVENTS` and no file is written.

### FCP 7 XML

The XML is written by the `fcp_xml` adapter of OpenTimelineIO, with one audio
track per channel, gaps between the clips and the markers of the session. The
timecode of the sequence is the start of the session, and the media file
appears as an external reference with its range.

The adapter writes the timecode of the sequence as non drop frame and does not
write the sample rate of the session; the writer fixes the XML after writing
it (`src/aafton/formats/otio_write.py`). In a drop frame session, the
timecode of the sequence comes out with the semicolon and with `displayformat`
DF, and the XML carries the sample rate of the session: a 96 kHz session comes
back from the XML at 96 kHz, and a drop frame session comes back from the XML
as drop frame, at the same start.

The timecode of each media file also comes out in drop frame: the
`displayformat` of the `<file>` is DF and the label comes from the frame
counter of the file, with the semicolon. A file that starts at 10:00:00;00
comes out with that label, and not with the non drop frame label of the same
frame counter. In a session without drop frame nothing changes.

The offset in the source also goes through the frame: when it does not fall
whole, the rounding enters the count of `FORMAT_POSITION_ROUNDED_TO_FRAME`.

The FCP XML does not have two clipitems in the same place of the track: a
clip that starts before the end of the previous one stays out, with
`FORMAT_XML_OVERLAPPING_CLIPS`.

### FCPXML 1.9

The FCPXML is also written by the product: the FCPXML writer of OpenTimelineIO
refuses a scene without a video track and writes version 1.8, without `tcStart`
and without the markers that sit inside the gap of the spine. The written file
brings the `sequence` with `tcStart`, `tcFormat` (`DF` or `NDF`),
`audioLayout`, `audioRate` and the duration of the session; the `media-rep` of
each asset, pointing to the media file; an `asset-clip` per clip, in negative
lanes (`-1`, `-2`, ...), with the exact times in fraction of a second
(`3600s`, `100/2500s`); and the markers inside the gap of the spine.

Since each clip becomes an `asset-clip` and each track becomes a lane, a
stereo file goes through the FCPXML: the asset carries `audioChannels` and the
clip comes back as a single clip, in a track of two channels.

The times of the FCPXML are not rounded to the frame: the position of a clip
becomes an exact fraction of a second. The audio rate must be one of the three
that the format names (44.1 kHz, 48 kHz or 96 kHz); another rate stops the
writing with `FORMAT_FCPXML_SAMPLE_RATE_UNSUPPORTED`.

### What the three formats refuse

Fade, constant clip gain, muted clip and automation of gain or of pan of the
track are not carried by any of the three formats: each one refuses with its
own code (`FORMAT_EDL_FEATURE_UNSUPPORTED`, `FORMAT_XML_FEATURE_UNSUPPORTED`,
`FORMAT_FCPXML_FEATURE_UNSUPPORTED`) and, in strict mode, the writing stops;
with `--permissive`, the file comes out without those features.

Muted track, track volume and track pan are refused the same way, each one
with the code of its format. A clip of a file with more than one channel is an
error in the EDL and in the XML (`FORMAT_MULTICHANNEL_UNSUPPORTED`), because
those two formats carry one channel per track; in the writing, the FCPXML
carries the channels of the file and does not refuse. In the reading, the
FCPXML refuses the file with more than two channels, as the reader describes
above.

The source timecode comes from the `bext` of the media file; without `bext`,
it starts at zero, with the warning `FORMAT_SOURCE_TC_FROM_ZERO`.

## Report codes

An error stops the run in strict mode; a warning and a note go on.

| Code | Severity | Where |
|---|---|---|
| `FORMAT_EXTRA_MISSING` | error | the `formats` extra is missing in the reading of the EDL or of the XML and in the writing of XML |
| `FORMAT_UNKNOWN_SUFFIX` | error | suffix outside AAF, EDL, XML and FCPXML |
| `FORMAT_SUFIXO_DESCONHECIDO` | error | format reader with a suffix without an adapter |
| `FORMAT_EDL_RATE_REQUIRED` | error | EDL without `--edl-rate` |
| `FORMAT_EDL_DROP_FRAME_MISMATCH` | error | drop frame label in a session that does not count drop frame |
| `FORMAT_EDL_DROP_FRAME_FROM_FCM` | note | `FCM: DROP FRAME` in a session read as drop frame |
| `FORMAT_EDL_FCM_MISMATCH` | warning | `FCM: NON-DROP FRAME` in a session that counts drop frame |
| `FORMAT_EDL_FCM_MIXED` | warning | `FCM` lines of the EDL that disagree with each other |
| `FORMAT_PARSE_ERROR` | error | origin file that the adapter could not read |
| `FORMAT_RATE_UNSUPPORTED` | error | frame rate that the reader does not know |
| `FORMAT_SAMPLE_RATE_ASSUMED` | note | sample rate assumed in the reading of the EDL or of the FCPXML |
| `FORMAT_TRANSITION_UNSUPPORTED` | error | transition in the origin file |
| `FORMAT_EFFECT_UNSUPPORTED` | error | effect in the clip of the origin file |
| `FORMAT_EFFECT_UNKNOWN` | warning | adjustment that the reader of the FCPXML does not know |
| `FORMAT_FADE_UNSUPPORTED` | error | fade in the clip of the FCPXML |
| `FORMAT_GAIN_UNSUPPORTED` | error | gain with a curve in the clip of the origin file |
| `FORMAT_PAN_UNSUPPORTED` | error | pan that is not neutral in the clip of the FCPXML |
| `FORMAT_NESTED_UNSUPPORTED` | error | stack or nested track in the origin file |
| `FORMAT_CLIP_DISABLED` | error | disabled clipitem of the XML or clip of the FCPXML |
| `FORMAT_TRACK_DISABLED` | error | disabled audio track in the XML of the FCP |
| `FORMAT_CHANNEL_SELECTION_UNSUPPORTED` | error | subset of channels that the reader cannot read |
| `FORMAT_POSICAO_INDEFINIDA` | error | position that the origin file does not declare |
| `FORMAT_SUBSAMPLE_POSITION` | warning | time that does not fall on a whole sample |
| `FORMAT_VIDEO_TRACK_SKIPPED` | note | video track of the origin file |
| `FORMAT_OVERLAP_MOVED_TO_TRACK` | note | overlapping clip that went to an extra track of the lane |
| `FORMAT_SOURCE_TC_UNRESOLVED` | error | media file without `bext` to resolve the offset |
| `FORMAT_SOURCE_TC_OUTSIDE_MEDIA` | error | source timecode outside the file |
| `FORMAT_SOURCE_TC_RATE_MISMATCH` | error | rate of the file different from the rate of the session |
| `FORMAT_MULTICHANNEL_UNSUPPORTED` | error | file with more than one channel in the EDL or in the XML, and file with more than two channels in the FCPXML |
| `FORMAT_EDL_FEATURE_UNSUPPORTED` | error | feature that the EDL does not carry |
| `FORMAT_EDL_MARKERS_DROPPED` | warning | markers that the EDL does not carry |
| `FORMAT_EDL_TOO_MANY_EVENTS` | error | more than 999 events in the EDL |
| `FORMAT_EDL_MEDIA_MISSING` | error | clip of the EDL without media file in the session |
| `FORMAT_XML_FEATURE_UNSUPPORTED` | error | feature that the XML does not carry |
| `FORMAT_XML_OVERLAPPING_CLIPS` | error | overlapping clips in the same track |
| `FORMAT_XML_MULTIPLE_SEQUENCES` | error | FCP XML with more than one sequence outside a clipitem |
| `FORMAT_XML_MEDIA_MISSING` | error | clip of the XML without media file in the session |
| `FORMAT_FCPXML_FEATURE_UNSUPPORTED` | error | feature that the FCPXML does not carry |
| `FORMAT_FCPXML_SAMPLE_RATE_UNSUPPORTED` | error | audio rate that the FCPXML does not name |
| `FORMAT_FCPXML_INVALID` | error | file that is not a valid FCPXML |
| `FORMAT_FCPXML_MULTIPLE_PROJECTS` | error | FCPXML with more than one project |
| `FORMAT_FCPXML_MEDIA_MISSING` | error | clip of the FCPXML without the asset it references |
| `FORMAT_POSITION_ROUNDED_TO_FRAME` | warning | positions rounded to the frame |
| `FORMAT_SOURCE_TC_FROM_ZERO` | warning | media file without `bext` in the writing |
| `WRITE_SESSION_FEATURE_UNSUPPORTED` | error | feature of the session without representation in the format |
| `MEDIA_MXF_EXTRACTED` | note | extraction of the MXF to WAV |
| `MEDIA_MXF_UNSUPPORTED` | error | MXF that could not be extracted |
| `MEDIA_MXF_RATE_MISMATCH` | warning | rate of the video of the MXF different from the one of the session, or drop frame timecode in a session that does not count drop frame |
| `MEDIA_MXF_TIMECODE_MISSING` | warning | MXF without timecode |

## MXF

The MXF is a container: the audio is inside it and ffmpeg knows how to read
it. With `ffmpeg` and `ffprobe` on the PATH, a `.mxf` file (OP-Atom or OP1a)
found during the import becomes **one** 24-bit PCM WAV, with all the audio
channels together: several mono streams are merged into a single file.

The WAV comes out in the `Samples/Imported` folder of the Live project, next
to the files that the import already copied, and the `.aafton.json` sidecar
records its sha256 as it records the one of any resolved media. The name of
the WAV is the name of the MXF, plus eight hexadecimal characters of the
origin path and the `_mxf.wav` suffix (`gravacao_a1b2c3d4_mxf.wav`): the
digest comes from the resolved path and it is what keeps two MXF with the same
name, in different folders, from becoming the same file.

The timecode of the MXF goes to the `bext` chunk of the WAV, in the
`time_reference` field. It is counted at the rate of the video when the file
has video and, without video, at the rate of the session. When the two rates
are different, the report records `MEDIA_MXF_RATE_MISMATCH` and the timecode
is counted in the video. It is the label that decides the count: a timecode
with `;` is counted in drop frame, at the rates that count drop frame (29.97
and 59.94), and a label without `;` is counted as non drop frame, even in a
session that counts drop frame. Without video, a drop frame timecode (`;`) in
a session that does not count drop frame is the warning
`MEDIA_MXF_RATE_MISMATCH`, and the timecode is counted as non drop frame.
Without timecode in the MXF, the audio starts at 00:00:00:00, with the
warning `MEDIA_MXF_TIMECODE_MISSING`. Each extraction appears in the report as
`MEDIA_MXF_EXTRACTED`, with the name of the WAV, the channels, the timecode
and the sha256.

An audio-only MXF is not the video of the session: the `ffprobe` says if the
file has a video stream, and an MXF without video stays out of the choice of
the video.

The `ffprobe` is called in bytes and the output is read as utf-8, so a folder
with an accent in the name does not break the reading of the report of the
`ffprobe`.

Without `ffmpeg` or without `ffprobe` on the PATH, without the media folder
or with an invalid file, the MXF stays marked as media not supported and the
conversion ends with the error `MEDIA_MXF_UNSUPPORTED`.

## Known limits

- **Frame precision in the EDL and in the XML.** The positions are rounded to
  the nearest frame; the report warns once per file, with the number of
  rounded positions and the largest error, in
  `FORMAT_POSITION_ROUNDED_TO_FRAME`. The FCPXML does not round.
- **FCPXML inside the FCP and Resolve.** The FCPXML 1.9 that the product
  writes, with `tcStart`, with `media-rep` and with the times in fraction of
  a second, has not been opened yet inside Final Cut Pro nor inside Resolve.
  It is the block (c) of the script.
- **Real exports not tested yet.** The samples were written by hand, from the
  published specifications of each format. No real export of Premiere, Resolve
  or FCP has been read so far.
- **Markers of the FCPXML.** The markers of the spine, of the gaps, of the
  connected clips and of the attached items (title, generator and audio) are
  read, with the absolute position of the sequence; the `chapter-marker`
  becomes a marker, and `analysis-marker` and `hidden-clip-marker` are
  ignored.
- **Channel choice inside a multichannel file.** The import does not extract
  a channel from an exchange file: the `.als` points to the whole file. A
  clip that chooses part of the channels of a multichannel file is refused
  with `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`; in permissive mode the clip
  plays the whole file. An asset that does not declare the number of channels
  (`audioChannels`) also does not serve to choose channels: the reader
  assumed a single channel and does not know that the choice is partial. The
  same holds when the asset declares fewer channels than the file has: the
  clip that chooses channels is refused with
  `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`, and the report says the two
  numbers. This holds until the import extracts channels.
- **File with more than two channels in the FCPXML.** The FCPXML refuses the
  file with more than two channels — a polyphonic WAV of a field recorder or
  a 5.1 stem — with `FORMAT_MULTICHANNEL_UNSUPPORTED`, in the reader and in
  the media check: the import does not extract a channel from an exchange
  file, and Live plays the stereo pair. The clip stays out of the scene: in
  permissive mode the `.als` is written without it, even when it is only the
  media check that finds the file with more than two channels. The track that
  loses the clip and is left with no clip at all leaves the scene, and the one
  that keeps a clip keeps the number of channels of the clips that stayed. The
  `verify` runs the same check on the origin: it fails on the error of the
  origin, and the clip that left the scene does not count as missing. This
  holds until the import extracts channels.
- **Order of the tracks of an EDL read back.** An EDL that the product writes
  and reads back brings the audio tracks in the order of the channel number
  (`A1`, `A2`, `A3`...), which is the order in which they were in the source
  set. The positions of the events are right.
- **Frame rates read.** The EDL and XML reader knows 24, 25, 30, 50 and 60
  fps, and the variants 23.976, 29.97 and 59.94 fps; another rate gives
  `FORMAT_RATE_UNSUPPORTED`. The FCPXML reader accepts any rate whose
  denominator is 1 or 1001.
- **Channels of the EDL.** The channel `A` and the channel `A2` are the ones
  of CMX 3600; `A3` and after is an extension: the reader of OpenTimelineIO
  reads those channels, but the file is not strict CMX 3600.
- **Video, transitions, effects and nesting.** The video tracks of the three
  formats are skipped; the video comes in through the `--video`, as in the
  AAF. Transition, effect (including the speed one) and nested stack in the
  origin file are an error.

## Out of scope

OMF and DAWproject are neither read nor written. The Studio, the web page,
does not import the new formats: it keeps reading AAF and ALS. The `verify`
does not read MXF: it compares the origin (AAF, EDL, XML or FCPXML) with the
`.als`, and the MXF enters the conversation in the import, as media.
