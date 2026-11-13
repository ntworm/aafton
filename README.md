# aafton

`aafton` converts post-production sound sessions between AAF, EDL (CMX 3600), Final Cut Pro 7 XML (xmeml), FCPXML, and Ableton Live 12 (`.als`). Clip positions are carried in samples; `aafton verify` compares the start and the end of every audio clip between the source and the Live set. It is designed for dialogue editing, conforming, Foley, and sound design exchange between Pro Tools, DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro, and Live 12.

The converter operates under a fail-closed design: if a session feature cannot be represented without loss, the operation stops with a non-zero exit code. Permissive mode (`--permissive`) can be enabled to generate partial output while logging all structural warnings and errors.

## Installation

There are three ways to run `aafton`.

### Desktop app for Windows

Download the release folder, unzip it and run `aafton.exe`. The first run of an unsigned app shows the SmartScreen warning: choose **More info** and then **Run anyway** to continue. `aafton-cli.exe` in the same folder is the command line version. The window needs the Microsoft Edge WebView2 Runtime, which is already present on Windows 11.

### macOS

There is no macOS build yet. Install with `pipx`, as described below.

### With Python 3.10 or newer

Once the package is on PyPI:

```bash
pipx install aafton
```

Until then, install it from a checkout of the repository:

```bash
pipx install .
```

The command `pip install .` works as well. The window of the studio needs the `desktop` extra:

```bash
pipx install ".[desktop]"
```

After the install, `aafton app` opens the studio in its own window and `aafton ui` opens the same page in the browser.

To enable EDL and Final Cut Pro 7 XML interchange via OpenTimelineIO:

```bash
pip install ".[formats]"
```

AAF and FCPXML reading and writing work without optional dependencies. `ffmpeg` and `ffprobe` are installed apart and found on the `PATH`; they are needed only for video and for audio inside MXF.

## Commands

The CLI entry point is `aafton` (or `python -m aafton.cli`).

### `import`
Converts an AAF, EDL, FCP7 XML, or FCPXML into an Ableton Live 12 `.als` set.
```bash
aafton import input.aaf output.als --audio-dir ./media --stereo pair --fades absorb
```
The Live template that ships with the package is used unless `--template` names another.
Key options:
- `--stereo pair|split`: Pair matching `_L`/`_R` channels into stereo tracks or keep separate mono tracks.
- `--fades absorb|keep`: Convert rendered fades into native Live fades or keep them as independent audio clips.
- `--media extract|link`: Extract embedded audio to the project folder or keep absolute filesystem links.
- `--groups prefix|none`: Group tracks automatically by name prefix.
- `--video <file>`: Anchor external video to session timecode.
- `--ltc`: Add a generated SMPTE ST 12-1 LTC audio track to the Live set.

### `export`
Converts a Live 12 `.als` project back to AAF, EDL, XML, or FCPXML based on the file extension.
```bash
aafton export session.als return.aaf --report export-report.json
```
The exporter re-reads the output file to confirm all source clips fit within declared asset boundaries before returning success.

### `inspect`
Dumps normalized intermediate representation (`SessionIR`) as JSON to stdout.
```bash
aafton inspect timeline.aaf
```

### `verify`
Compares the start and the end of every audio clip, in samples, between the source interchange file and the generated `.als`.
```bash
aafton verify input.aaf output.als --report verify-report.md
```
Exit code 0 means every audio clip of the source was compared and its start and end are within the tolerance the report declares: zero samples when the source counts in samples, one frame when it counts in frames. Media offset, audio content, gain, pan and envelopes are outside this check.

### `audit-audio`
Performs independent audio-level null tests between reference WAV bounces (e.g. Pro Tools) and candidate Live renders using BWF timecode and sidecar alignment.
```bash
aafton audit-audio --reference ./pt_bounces --candidate ./live_renders --sidecar set.aafton.json --map track_map.json --mode keep
```

### `turnover`
Generates a self-contained delivery bundle containing media, session files, and a SHA-256 integrity manifest.
```bash
aafton turnover session.als --media-dir ./media --output ./turnover_pkg
```

### `ui`
Launches the local web interface (Studio) for visual timeline inspection and timecode monitoring.
```bash
aafton ui --port 8765
```

### `app`
Opens the studio in its own window; it needs the `desktop` extra.
```bash
aafton app
```
`aafton app --smoke` checks the local server and exits.

### `ltc`
Generates standalone SMPTE ST 12-1 linear timecode WAV audio files.
```bash
aafton ltc --start 01:00:00:00 --rate 25 --duration 00:10:00:00 output_ltc.wav
```

## Proven Behavior and Verification

The converter has been verified against real-world production sessions and controlled benchmarks:
- **Sample Accuracy**: Four real Pro Tools sessions (`TESTE_AFF_PROTOOLS`, `SPOT_AAF_TESTE`, `TESTE_DF_2997` and `TESTE_DF_2997_correto`) and one real Premiere Pro session, each converted to Live and back in both stereo pairing modes (ten combinations), every compared clip at zero samples of deviation by `aafton verify`; Resolve appears only as a three-clip dialect fixture for `edit_rate` handling, not as a real-session proof.
- **29.97 Drop-Frame Alignment**: Verified on real sessions exported in drop-frame and non-drop-frame variants. Exact drop-frame counting avoids the 3.6036-second drift per hour that occurs when treating 29.97 fps as 30 fps.
- **Native Live 12 Acceptance**: Tested on Ableton Live 12.4.15b7. All 11 test cases covering off-grid clip boundaries, drop frame, gain automation, crossfades, asymmetric fades, mono pan, and channel combiners were opened and re-saved via Save As, achieving a geometric index of 1.0000 across all 303 audio events.
- **Acoustic Audit**: Pro Tools and Live renders generated from identical AAF sources achieved lag 0 alignment and worst residual peak below −144 dBFS in keep mode across 4 tracks and 8 channels.

## Known Limitations

- **Warp and Loop**: Clips with Warp enabled, loop active, or non-zero start offsets are rejected (`ALS_CLIP_MODE_UNSUPPORTED`). Clips must be rendered to flat audio before export.
- **Tempo Changes**: Main track tempo automation or deviations from sidecar BPM are rejected in strict mode (`ALS_TEMPO_AUTOMATION_UNSUPPORTED` for tempo automation, `ALS_TEMPO_CHANGED` for a tempo that differs from the sidecar).
- **DAW Processing**: Plugins, virtual instruments, and return/master effects chains are not reconstructed in AAF.
- **Fades and Transitions**: A clip fade that has no matching fade on a neighbouring clip is written as an AAF `Transition` beside a `Filler` (fade in from silence, fade out to silence), the form Resolve and Premiere write; the report notes it as `FADE_WRITTEN_AS_TRANSITION`. Two fades that do not fit in their clip, or a fade with a gain automation point inside it, cannot be written that way: strict mode rejects them (`WRITE_FADE_UNSUPPORTED`) and permissive mode converts them to a gain automation envelope (`FADE_CONVERTED_TO_ENVELOPE`). The curve of a Live fade is not carried; the AAF holds a plain audio dissolve.
- **Pan Automation**: Imported pan is static; continuous pan automation from AAF is not imported. Exporting pan automation to AAF is supported only on mono tracks.
- **File Formats**: RF64 containers and individual RIFF essences larger than 4 GiB are unsupported.
- **Video Tracks**: Embedded video tracks in AAF are discarded on import; video is not written to export AAFs.

## Running Tests

Run the full automated test suite with `pytest`:

```bash
pytest
```

To run structural corpus tests, the test silence corpus is needed. On the first run, `pytest` generates it in `.local/corpus/teste_aaf` from the pinned manifest, so a fresh clone needs no manual step.

Configure the corpus path using the environment variable; point `AAFTON_CORPUS_DIR` at another folder to use a corpus kept elsewhere:

```bash
export AAFTON_CORPUS_DIR=.local/corpus/teste_aaf
```

To generate the same corpus by hand:

```bash
python scripts/make_silence_corpus.py tests/corpus/teste_aaf.json .local/corpus/teste_aaf
```

Tests for media not distributed in the repository skip with reasons starting with `not distributed:`. Optional local test assets can be pointed to with `AAFTON_LOCAL_MEDIA_DIR` and `AAFTON_REEXPORT_DIR`.

## Documentation

- [Interchange Formats and MXF](docs/FORMATS.md)
- [Dialect Matrix](docs/DIALECT_MATRIX.md)
- [Live XML Map](docs/ALS_XML_MAP.md)
- [Live Schema Contract](docs/ALS_SCHEMA.md)
- [Timecode and Synchronization](docs/TIMECODE.md)
- [Timecode in Project Files](docs/TIMECODE-FORMATS.md)
- [Audio Audit Specification](docs/AUDIO_AUDIT.md)
- [Post-Production Specification](docs/POSTPRO_SPEC.md)
- [Test Matrix](docs/POSTPRO_TEST_MATRIX.md)
- [Roundtrip Edits](docs/ROUNDTRIP_EDITS.md)
- [Format Acceptance Script](docs/FORMATS-ACCEPTANCE.md)
- [Timecode Acceptance Script](docs/TIMECODE-ACCEPTANCE.md)

## License

MIT License. See [LICENSE](LICENSE) for details.
