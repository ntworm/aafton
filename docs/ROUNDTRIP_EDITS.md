# Edit round trip

What this document guarantees today, and what it does not guarantee. Every
"works" claim here has a test that proves it; every "does not work" has the
cause written down.

## What the round trip does

**Volume and Pan automation enters the SessionIR.** The Live `.als` stores the
automation in `AutomationEnvelopes/Envelopes/AutomationEnvelope`, with
`EnvelopeTarget/PointeeId` pointing to the `AutomationTarget` of the mixer
inside the `DeviceChain`. The reader resolves this link and converts each
`FloatEvent` into `(absolute sample, value)` in `TrackIR.gain_points` and
`TrackIR.pan_points`.

The `.als` writer emits a sentinel `FloatEvent` before the first point, with
`Time = SENTINEL_TIME`. It carries the value of the first point and **is not**
a position in the timeline. The reader drops this point so the timeline is not
polluted.

An envelope that points to neither Volume nor Pan keeps no representation in
the AAF and keeps being reported as `ALS_AUTOMATION_UNSUPPORTED`.

**Volume automation goes to the AAF with VaryingValue (LinearInterp).** The
gain automation curve of each track is serialized in an `OperationGroup`
("Audio Gain") with the `Amplitude` parameter of type `VaryingValue` and linear
interpolation `LinearInterp`. The keyframes are normalized between `[0.0, 1.0]`
along the extent of the clip, preserving continuous ramps without degradation
into steps. The report emits `GAIN_AUTOMATION_WRITTEN`.

**Pan automation goes to the AAF with VaryingValue.** In mono tracks, when
`track.pan_points` is present, the slot is wrapped in an `OperationGroup`
("Mono Audio Pan") with the `Pan value` parameter of type `VaryingValue` and
the `LinearInterp` algorithm. The pan range of Live `[-1.0, 1.0]` is mapped
linearly to the `[0.0, 1.0]` range of the AAF. The report emits
`PAN_AUTOMATION_EXPORTED`.

**Independent fades become a `Transition` beside a `Filler`.** A one-sided
fade (`clip.fade_in` or `clip.fade_out`) with no adjacent clip to form the
crossfade is written as a `Transition` (Avid Audio Dissolve) beside a `Filler`:
after the `Filler` is the fade in from silence, before it is the fade out to
silence. The positions of the clips do not change, and the reader returns the
same fades. The report emits `FADE_WRITTEN_AS_TRANSITION`, once per track.
When the two fades do not fit in the clip, or when there is a gain automation
point inside the fade, the fade stays as a ramp in the gain envelope of the
clip (`VaryingValue`), with `FADE_CONVERTED_TO_ENVELOPE` and the error
`WRITE_FADE_UNSUPPORTED`. Fades with a compatible adjacent pair keep becoming
the `Transition` of the crossfade.

**Strict validation of the time map and of MainTrack.** When the `.als` holds
BPM automation in the `MainTrack` with varying values, the reader refuses it in
strict mode with `ALS_TEMPO_AUTOMATION_UNSUPPORTED`, preventing a silent
temporal misalignment on export. Clips with `Warp` or `Loop` active keep
naming the causes and require a previous render
(`ALS_CLIP_MODE_UNSUPPORTED`).

**Preservation and export of Session Markers.** Locators read from Live are
converted into `session.markers` (`MarkerIR`) and serialized in the exported
AAF in a metadata track ("Markers") using `DescriptiveMarker` components with
the `Position` and `Comment` properties. On the read back through `read_aaf`,
the markers are fully recovered. The report emits `MARKERS_WRITTEN`.

**Single rounding for different clocks.** For clips whose media has a sample
rate distinct from the session, the absolute offset of the span
(`clip.source_offset_samples + offset`) is computed in exact rational fractions
and rounded once, when converting to the rate of the source, reporting the
residues through `WRITE_SOURCE_OFFSET_ROUNDING` with no accumulation of double
rounding.

Proof: `tests/unit/test_roundtrip_edits.py` and
`tests/golden/test_roundtrip_golden.py`.

## What changes in the report

| Situation | Before | Now |
|---|---|---|
| volume automation in Live | refused on the read or in steps | exported with `VaryingValue` (`GAIN_AUTOMATION_WRITTEN`) |
| pan automation in Live | refused on the read or `WRITE_PAN_...` | exported with `VaryingValue` (`PAN_AUTOMATION_EXPORTED`) |
| independent fade | `WRITE_FADE_UNSUPPORTED` | `Transition` beside a `Filler` (`FADE_WRITTEN_AS_TRANSITION`); envelope and error when it does not fit |
| variable BPM automation | silent or unvalidated map | `ALS_TEMPO_AUTOMATION_UNSUPPORTED` in strict mode |
| sync markers | not exported | serialized in `DescriptiveMarker` (`MARKERS_WRITTEN`) |

## Known limits

| Limit | Where |
|---|---|
| adjacent transition with cuts in the overlap refused | `WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED`, preserves the geometry |
| fade with a gain automation point inside it, or pair of fades larger than the clip | stays as a gain envelope, `WRITE_FADE_UNSUPPORTED` |
| Warp, loop and non-linear time require a render before the AAF | `ALS_CLIP_MODE_UNSUPPORTED`, by design |
| `MXF`/`RF64`, interleaved multichannel and fallback without sidecar | the technical report of 2026-10-15 |
| a universal round trip without losses is not promised | the technical report of 2026-10-15 |
