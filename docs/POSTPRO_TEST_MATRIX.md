# Qualification and Test Matrix - Ableton AAF Post-Production

The general matrix defines the qualification of the following stages.

---

## 1. Methodological principles

- **No dependence on a single test:** a monolithic session does not cover all the permutations of dialects, rates and layouts. The qualification uses reduced fixtures per edge case, combined with real studio sessions.
- **Two distinct directions of proof:**
  1. *Import cycle:* check of the generated `.als` against the original AAF of Pro Tools.
  2. *Return cycle:* check of the exported AAF against the intentional state of the Live Set edited by the operator.
- **External oracles:** integrity tests do not use the own reader/writer in a closed circuit when there is a risk of a symmetric error masking a defect.

---

## 2. Test families and acceptance criteria

| Family | Mandatory cases | Oracle and acceptance criterion |
|---|---|---|
| **Coverage** | Missing clips, extra clips, empty clips, video only, tracks with the same name, duplicates in the map | Multiset of events; strict count of indexed audio; `VerifyResult.ok` false if a clip is missing or is left over |
| **Time and limits** | Displaced start and end, nominal tolerances, subframe, gaps, valid transitions and arbitrary overlaps | Start and end deviations (`delta_samples` and `delta_end_samples`) inside the explicit tolerance; `WRITE_OVERLAP` with an error and a block of the write |
| **Sample rates** | 48 kHz, mixed sources with distinct rates, 44.1 kHz | Rejection of a rate mismatch without silent resample; exact match of samples |
| **Timecode and frames** | 24, 25, 30 fps; 23.976 (24000/1001), 29.97 NDF and DF (30000/1001); hour transitions and drop compensation | Timecode labels and sample counts aligned; zero accumulated drift over long sessions |
| **Media and encodings** | WAV PCM 16/24-bit, IEEE Float 32-bit, RIFF vs. RF64 (>4 GiB), files with extra chunks after `data` | Correct decoding of samples; prohibition of interleaving float as integer; clean stop before the format limits are exceeded |
| **Multichannel layouts** | Mono, Stereo (interleaved and split), Quad (4.0), 5.1 and conventional 7.1; Film, SMPTE and BWF orders | Exact assignment of the channels to the correct slots; absence of inversion between LFE and surround; zero bleeding between the channels |
| **Gains and levels** | Static clip gain (0 dB, -6 dB, amplification), track fader, mute, envelopes with steps | Level comparison against the reference render of the host without distortion or spurious dither |
| **Fades and transitions** | Supported crossfades with coinciding fades, absorbed vs. kept fades | Millimetric temporal preservation; absence of gaps or duplicated attenuation |
| **Editing in Live** | *Move, split, trim, duplicate, rename, reorder, delete*, insertion of new audio, *Save As* | Correct detection in the return; conservation of the sibling channels synchronized |
| **Processing in the host** | Plugins, virtual instruments, *Warp*, sends, returns with time effects | *Render* in the source host before the export; report declaring the rendered operations |

---

## 3. Ladder of technical evidence

1. **Level 1 (Unitary / Local):** fast unit tests without open DAWs, exercising parsers, intermediate representation and calculation of deltas (`pytest`).
2. **Level 2 (CLI in an isolated environment):** calls to the terminal commands (`import`, `export`, `verify`, `inspect`) in temporary directories, inspecting exit codes and reports.
3. **Level 3 (Round trip with a golden reference):** real test sessions confronted against independent session reports.
4. **Level 4 (Manual Live / Save As):** real opening in Ableton Live 12, visual/auditory check and re-recording via *Save Live Set As*.
5. **Level 5 (Pro Tools acceptance):** re-import of the generated AAF in real Pro Tools workstations, validating media relink and sync.
6. **Level 6 (Real production):** execution in film/audiovisual projects with dozens of tracks and hundreds of takes.
7. **Level 7 (Independent review gate):** audit of diffs, git sanity and proof of the evidence produced.
