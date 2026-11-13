# Ableton AAF - Post-Production Specification

Base: `84b54dd3798f2c950de78b446b005cbbe859323a`.
Status: the implementation phases are documented in the planning and verification scripts.

---

## 1. Product decision and scope

Build a specialized sound editing bridge **Pro Tools -> Live 12 -> Pro Tools**. The flow aims to let an editor receive cinema/audiovisual sessions with separated clips, do assembly, synchronization, editing and creation in Ableton Live 12, and hand the work back with positioning and structural integrity auditable for finishing in Pro Tools.

### Formats supported in the planned scope
- **Conventional audio:** mono, stereo, quad (4.0), 5.1 and conventional 7.1 (standard SMPTE/Film cinema layouts).
- **Non-goals (out of scope):** Dolby Atmos, object-based audio, higher-order ambisonics, 7.1.2 or simulation of integrated surround mixer processing that does not exist in Live.

The proposal is a tool focused on preparation, channel integrity and auditable return. It does not aim to be a universal plugin translator nor an emulator of external DAWs.

---

## 2. Selective commit vs. full commit

The "commit everything" hypothesis is discarded as a universal rule:

1. **Preserve editability whenever possible:** media, cuts, offsets, handles, names, positions and channels must be preserved without destructive consolidation.
2. **Separation of gain and volume entities:** clip gain, track fader, mute and volume/pan automation are distinct structures. Static gain and fades can be preserved in certified metadata.
3. **Render in the host for operations that cannot be translated:** third-party effect plugins, virtual instruments, *Warp*, time-stretch, processing of busses/subgroups with sidechain compression and complex automation curves must be rendered/bounced in the source DAW before the transport.
4. **Declared metadata:** the converter and the report must record explicitly what was transported in metadata and what requires a render in the host, avoiding that processing is applied twice or silenced.
5. **Surround pan and multichannel dynamics:** Live handles channels internally as stereo. Moving a visual group or six mono tracks in Live is not a surround mixer with linked dynamics. Therefore, linked multichannel effects require a prior multichannel render in the proper host.

---

## 3. Delivery profiles

| Profile | Content | Success condition |
|---|---|---|
| **Editable** | Audio clips with limits, certified fades and translatable static gain | Identical structural and temporal comparison in the verifier |
| **Prepared in the host** | Effects bounced in the host, stems consolidated per channel, residual metadata declared | Sample-exact alignment of limits, without duplicated processing |
| **Not supported** | Unknown operations, ambiguous layouts or data with no normative interpretation | Stop with an error code, naming the track and the clip; never a silent discard |

---

## 4. Multichannel representation in Live

A logical 5.1 track is broken into an organizational group (`GroupTrack`) with six discrete mono tracks: L, R, C, LFE, Ls, Rs. In the case of 7.1, the canonical side and rear channels are added (Lss, Rss, Lrs, Rrs).

- **Limits of the organizational group:** the `GroupTrack` serves for visual organization and navigation. The audio outputs of the individual tracks must be routed directly to discrete outputs or independent multichannel busses, avoiding the stereo sum in the group master.
- **Linked track editing:** cuts and fades are kept coordinated between the sibling tracks by the native feature of Live, but intentional point divergences by the operator must be detected in the return.
- **Return:** the AAF exporter must rebuild or preserve the channels without swapping roles (for example: LFE cannot be swapped with a surround channel).

---

## 5. Incremental architecture

1. **IR (*Intermediate Representation*):** times and durations expressed in integer samples; offsets in the rate of the source; exact rational timecode rates.
2. **ALS boundary:** reading focused exclusively on the active Arrangement of the Live Set; detection of inactive clips, take lanes and devices that are not supported.
3. **AAF boundary:** strict verification before the write; closed rejection of overlaps without a corresponding transition.
4. **Verification (`verify`):** clear distinction between the structural check of the import and an independent sound audit.

---

## 6. Reference documents

- Test and qualification matrix: [`POSTPRO_TEST_MATRIX.md`](POSTPRO_TEST_MATRIX.md).
