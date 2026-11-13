# AAF Dialect Matrix and Exchange with Ableton Live 12

Technical documentation of the ingest, geometric normalization and export capabilities of the `ableton-aaf` pipeline for the main post-production and audiovisual editing systems.

---

## 1. Overview of the Dialect Matrix

The Advanced Authoring Format (AAF / SMPTE 377M / Edit Protocol) standard is implemented in substantially different ways across the DAWs and NLEs on the market:

- **Avid Pro Tools**: treats audio sessions as pure sample-accurate units, wrapping stereo and surround channels through *Audio Channel Combiner*, and serializing transitions as clips rendered on disk (*rendered fades*).
- **Blackmagic DaVinci Resolve**: runs a frame-oriented timeline (*frame edit rates* such as 24 or 25 fps even for sound tracks), serializes native crossfades through *Avid Audio Dissolve* and includes embedded video tracks.
- **Adobe Premiere Pro**: projects the session as a rich tree with several `CompositionMob` (nested sequences, auxiliary sequences and drafts that precede the main timeline), isolates media paths through *NetworkLocators* with the URI scheme `file:///`, and wraps audio tracks inside *Mono Audio Pan* nodes.
- **Consolidated sessions (Embedded Essence)**: store the PCM audio streams directly in the structured AAF container, sometimes preceded by complete RIFF WAVE headers, sometimes as a raw PCM stream (*raw bytes*), at the rates of 44.1 kHz, 48 kHz and 96 kHz.

The `ableton-aaf` project implements a neutral layer of geometric abstraction (`SessionIR`) that converts every variant into absolute samples counted from `00:00:00:00`, mapping them with mathematical exactness to the native format of Ableton Live 12 (`.als`).

---

## 2. Summary of the Support Matrix

| Feature / Dialect | Avid Pro Tools | DaVinci Resolve | Adobe Premiere Pro | Embedded essence |
|---|---|---|---|---|
| **Composition selection** | `Usage_TopLevel` | `Usage_TopLevel` | Tie-break by 3 ranks | Default composition |
| **Slot edit rate** | Sample rate (48k) | Frame rate (24, 25 fps) | Sample rate / frames | Sample rate |
| **Stereo / multichannel** | *Audio Channel Combiner* | Parallel mono/stereo tracks | Parallel mono/stereo tracks | Mono / multichannel mobs |
| **Fades / transitions** | Rendered mobs `Fade \d+` | `Avid Audio Dissolve` | Dissolve / clip gain | N/A |
| **Fade options** | `--fades absorb` or `keep` | Native in Live | Native in Live | N/A |
| **Media location** | Names / BEXT / Paths | Relative / A-prefix | *NetworkLocator* (`file:///`) | Direct binary extraction |
| **Video tracks** | Ignored / guide | Dropped with a warning | Dropped with a warning | N/A |
| **Static pan** | Optional | Optional | *Mono Audio Pan* unwrap | N/A |
| **Sample rates** | 44.1, 48, 96, 192 kHz | 44.1, 48, 96 kHz | 44.1, 48, 96 kHz | 44.1, 48, 96 kHz |

---

## 3. Specification per Ecosystem

### 3.1. Avid Pro Tools

1. **Audio Channel Combiner (`6b46dd7a-132d-4856-ab21-8b751d8462ec`)**:
   - Stereo tracks consolidated by Pro Tools use an `OperationGroup` with two input channels pointing at distinct mono sources.
   - The reader detects the Combiner, identifies `track.channel_count = 2` and sets `_TRACK_FORMAT = 2`.
   - On the export to Live, the sources are paired without bleed between the channels.

2. **Rendered fades (`Fade \d+`)**:
   - Pro Tools writes the fades between takes as individual WAV files on disk or as dedicated fade mobs.
   - `--fades absorb` mode: replaces the rendered files with native Live 12 fades and crossfades, checking media availability at the head and tail of the adjacent clips (`FADE_CLAMPED_START`, `FADE_CLAMPED_END`).
   - `--fades keep` mode: keeps the rendered file as an independent clip on the Live timeline when the source media has no extra material to cover the crossfade (`FADE_KEPT_SOURCE_TOO_SHORT`).

### 3.2. DaVinci Resolve

1. **Frame Edit Rates**:
   - Audio slots in DaVinci Resolve commonly have a `slot.edit_rate` of 24 or 25 fps, while the clips declare `length` in the unit of that slot (frames).
   - The reader converts the durations and positions through `_edit_units_to_samples(length, edit_rate, sample_rate) = Fraction(length * sample_rate, edit_rate)`.
   - A clip of 48 frames at 24 fps converts with exact sample precision to 96,000 samples @ 48 kHz, without loss and without accumulated error.

2. **Discard of picture tracks**:
   - Slots with `media_kind == "Picture"` present in the Resolve export are filtered out and dropped with the informative record `VIDEO_TRACK_SKIPPED_ON_IMPORT`.

3. **Avid Audio Dissolve transitions (`0c3bea41-fc05-11d2-8a29-0050040ef7d2`)**:
   - Audio transitions shorten the time cursor of the sequence by their own length and generate `FadeIR(kind="out")` on the previous clip and `FadeIR(kind="in")` on the next one.

### 3.3. Adobe Premiere Pro

1. **Deterministic CompositionMob selection (3-rank algorithm)**:
   - Premiere projects often hold several sequences in the same AAF (auxiliary sequences, multicam or drafts).
   - The `aaf_read.py` sorts the `CompositionMob` found deterministically, applying the following strict hierarchy:
     - **Rank 1**: `UsageCode == 'Usage_TopLevel'`. Root sequences marked by the editor have the highest priority.
     - **Rank 2**: Count of audio slots (`TimelineMobSlot` with a sound `DataDef`). Full sequences beat drafts or single-clip sequences.
     - **Rank 3**: Total accumulated duration of the audio slots in samples. Long editing sequences beat short stingers/effects sequences.
   - Ties keep the original order of the file (stable sort in Python).

2. **NetworkLocators and URI normalization**:
   - Media descriptors in Premiere point at the physical location through `NetworkLocator` instances with a `URLString` in the `file:///` pattern.
   - The `_url_para_caminho()` function decodes percent-encoded characters and removes redundant slashes before the drive letter on Windows (`file:///C:/...` -> `C:\...`).

3. **Mono Audio Pan wrapper**:
   - Premiere wraps the whole `Sequence` of each track in an `OperationGroup` with `Mono Audio Pan` (`9d2ea893-0968-11d3-8a38-0050040ef7d2`).
   - The `_read_static_track_pan()` extractor walks down the tree to recover the continuous static pan value, mapping from $[0, 1]$ to the $[-1, 1]$ of Live.

### 3.4. Embedded audio (Embedded Essence)

- For consolidated AAFs (where the media travels inside the AAF file and not as external WAVs), the `media_extract.py` module extracts the essence directly to `Samples/Imported`:
  - **RIFF WAVE**: container with the full header written by the source host; kept intact with the `bext` chunk injected.
  - **Raw PCM**: sample stream without a header; packed on the fly with a RIFF WAVE header and a `bext` synchronized with the start timecode of the session.
  - **Multi-rate support**: rates of 44.1 kHz, 48 kHz and 96 kHz read faithfully from the `SampleRate` property of the `WAVEDescriptor`.

### 3.5. Fractional rates and SMPTE format in Live 12

The project serializes `TimecodeRate` as exact rationals:
- **23.976 fps**: $24000 / 1001 \approx 23.976023976$
- **29.97 fps NDF**: $30000 / 1001 \approx 29.97002997$ (`drop_frame=False`)
- **29.97 fps DF**: $30000 / 1001$ (`drop_frame=True`) with the SMPTE formula that compensates the skipped frames.
- **59.94 fps**: $60000 / 1001 \approx 59.94005994$

In the Live 12 Set XML (`.als`), the `LiveSet/SmpteFormat` tag is filled according to the value of the session:
- `1`: 24 fps and 23.976 fps
- `2`: 25 fps
- `3`: 30 fps NDF
- `4`: 29.97 fps DF
- `5`: 29.97 fps NDF
- `0`: Bars / ms (fallback for rates with no direct match in Live, such as 59.94).

### 3.6. Long sessions (2h+) and negative timecode / pre-roll

1. **Feature-length scale (2h+ / 7200s+)**:
   - The time calculations use 64-bit integers for absolute samples (7200s @ 48 kHz = 345,600,000 samples, well below the 53-bit floating point precision limit).
   - There is no overflow and no sub-sample degradation in the Live 12 tick grid (`LIVE_CLIP_TICKS_PER_BEAT = 3843840`).

2. **Negative timecode and pre-roll (`live_origin_samples=None`)**:
   - In Ableton Live, the arrangement timeline does not allow negative beats ($Time \ge 0.0$).
   - Film sessions often start at `00:59:50:00` (10 seconds of pre-roll before `01:00:00:00`) or have negative alignment timecodes (`tc.start < 0`).
   - In `WriteOptions`, setting `live_origin_samples=None` triggers the automatic resolution of the origin:
     ```python
     origin = session.start_timecode_samples if options.live_origin_samples is None else options.live_origin_samples
     if options.live_origin_samples is None:
         all_starts = [c.timeline_start_samples for c in session.all_clips()] + [m.position_samples for m in session.markers]
         if all_starts and min(all_starts) < origin:
             origin = min(all_starts)
     ```
   - This formally guarantees that for every clip and marker of the session:
     $$\text{live\_samples} = \text{abs\_samples} - \text{origin} \ge 0$$
   - Every clip opens on the Live timeline at beat 0 or later, preventing `AnchorError` failures.

---

## 4. Test Suite and Validation Oracles

The validation of the whole matrix is covered by the automated suite:

1. `tests/golden/test_dialect_matrix.py`:
   - `test_protools_synthetic_combiner_and_rendered_fades` (absorb and keep modes)
   - `test_resolve_synthetic_frame_edit_rates_and_video_skip` (24 and 25 fps)
   - `test_premiere_synthetic_multiple_comps_deterministic_ranking`
   - `test_rank_composition_mob_hierarchy` (isolated formal validation)
   - `test_embedded_audio_matrix` (44.1k, 48k, 96k in RIFF and PCM)
   - `test_fractional_frame_rates_matrix` (23.976, 29.97 NDF/DF, 59.94)
   - `test_long_session_2h_plus`
   - `test_negative_timecode_and_automated_origin_alignment`
   - `test_preroll_timecode_clips_before_nominal_start`

2. `tests/golden/test_dialects.py`:
   - Validation against real empirical fixtures provided in the `tests/fixtures/aaf/` folder.

3. `tests/aaf_factory.py`:
   - Controlled synthetic generators that do not use the writer of the product itself, keeping the oracles orthogonal and auditable.
