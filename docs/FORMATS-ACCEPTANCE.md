# Manual acceptance of the exchange formats and of MXF

The automatic checks passed: on scene CENA12, the cycle of importing,
verifying and exporting in the three formats returns the same positions,
durations, offsets, files, session start and markers. They measure what the
code does with files written by hand.

This script is the human acceptance, separate from them: here the operator
checks what is seen and heard inside Premiere, Resolve and Pro Tools. It is a
single session, from the beginning to the end. Each block ends with a "Result:"
line to note what happened, and each step brings an "Expected result:".

Execution: manual check in the applications.

## (a) Preparation

1. Install the formats extra in `aafton` environment:
   `pip install 'aafton[formats]'`
   Expected result: `pip` installs `opentimelineio==0.18.1`,
   `otio-cmx3600-adapter==1.0.0`, `otio-fcp-adapter==1.0.0` and
   `otio-fcpx-xml-adapter==1.0.0`, without an error.
2. Put `ffmpeg` and `ffprobe` on the PATH.
   Expected result: `ffmpeg -version` and `ffprobe -version` answer on the
   same machine.
3. Prepare a short sequence in Premiere, with audio in two tracks and media in
   WAV with file timecode (BWF, `bext` chunk): for example ten seconds per
   track, with file timecode 10:00:00:00 in one of the files.
   Expected result: the sequence has two audio clips, each one with its own
   WAV file, and the Premiere Probe shows the file timecode of each one.
4. Prepare a short timeline in Resolve, with the same two audio tracks and the
   same WAV files.
   Expected result: the timeline has two audio clips, with the WAV files and
   the same record time of the Premiere sequence.
5. Open a working folder (for example `C:\aceite\formatos`), copy the media
   WAVs into it, in the `midia` subfolder, and keep the exports and the
   reports in it. From the root of the repository, also set the two PowerShell
   variables: `$aaftonPython = ".\.venv\Scripts\python.exe"` and
   `$trabalho = "C:\aceite\formatos"`.
   Expected result: the folder exists, with the media WAVs inside, and the two
   PowerShell variables exist in the session.

Result:

## (b) Premiere: XML and EDL

1. In Premiere, export the sequence of step (a) 3 in "Final Cut Pro XML"
   (xmeml) and, then, in EDL (CMX 3600), with the frame rate of the sequence.
   Expected result: both files exist in the working folder; the XML has two
   audio tracks and the EDL has events on the channels A and A2.
2. Import the XML of Premiere:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_premiere.xml $trabalho\premiere.als --template templates/live12.als --audio-dir $trabalho\midia --report $trabalho\premiere_xml_import.json`
   Expected result: the `.als` is created, the report comes out without an
   error and Live shows two audio tracks.
3. Compare the positions: open the `.als` in Live and note the start and the
   end of each clip; take the same values in the Premiere timeline.
   Expected result: the clips fall in the same places, frame by frame; if any
   of them does not fall, the report brings `FORMAT_POSITION_ROUNDED_TO_FRAME`
   with the number of rounded positions and the largest error.
4. Import the EDL of Premiere, with the frame rate of the sequence:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_premiere.edl $trabalho\premiere_edl.als --template templates/live12.als --audio-dir $trabalho\midia --edl-rate 25 --report $trabalho\premiere_edl_import.json`
   Expected result: the `.als` is created without an error and the clips fall
   in the same places of the Premiere timeline.
5. Export the set back to XML:
   `& $aaftonPython -m aafton.cli export $trabalho\premiere.als $trabalho\premiere_volta.xml --report $trabalho\premiere_xml_export.json`
   Expected result: the `.xml` is created, with two audio tracks and the
   markers that the session had.
6. Import the returned `.xml` in Premiere.
   Expected result: Premiere opens the file, shows the two audio tracks with
   the same clips and the same positions of the original sequence.
7. Put a stereo music clip (two-channel WAV) in one of the tracks of the
   sequence and repeat the export and the import of steps 5 and 2, in strict
   mode.
   Expected result: the export and the import stop with
   `FORMAT_MULTICHANNEL_UNSUPPORTED` and no file is written; in the FCPXML
   (block (c)) the same clip passes, because the FCPXML carries the stereo
   pair of the file.
8. Put a rubber band (volume curve) on the volume of one of the clips of the
   sequence and export the sequence in "Final Cut Pro XML"; then import the
   XML with `aafton`.
   Expected result: the XML is created; in the import, the report brings
   `FORMAT_GAIN_UNSUPPORTED` for the clip with the curve, because the
   `Audio Levels` filter with a curve does not become the gain of the clip.

Result:

## (c) Resolve: FCPXML and EDL

1. In Resolve, export the timeline of step (a) 4 in FCPXML and, then, in EDL
   (CMX 3600).
   Expected result: both files exist in the working folder; the FCPXML has a
   sequence with `tcStart` and the EDL has events on the channels A and A2.
2. Import the FCPXML of Resolve:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_resolve.fcpxml $trabalho\resolve.als --template templates/live12.als --audio-dir $trabalho\midia --report $trabalho\resolve_fcpxml_import.json`
   Expected result: the `.als` is created, the report comes out without an
   error and Live shows two audio tracks.
3. Compare the positions with the Resolve timeline, as in block (b) 3.
   Expected result: the clips fall in the same places, frame by frame.
4. Import the EDL of Resolve, with the frame rate of the timeline:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_resolve.edl $trabalho\resolve_edl.als --template templates/live12.als --audio-dir $trabalho\midia --edl-rate 25 --report $trabalho\resolve_edl_import.json`
   Expected result: the `.als` is created without an error and the clips fall
   in the same places of the Resolve timeline.
5. Export the set back to FCPXML:
   `& $aaftonPython -m aafton.cli export $trabalho\resolve.als $trabalho\resolve_volta.fcpxml --report $trabalho\resolve_fcpxml_export.json`
   Expected result: the `.fcpxml` is created, with `tcStart` in the sequence,
   with the `media-rep` of each file and with the times in fraction of a
   second.
6. Import the returned `.fcpxml` in Resolve and see if it accepts the exact
   fractional times, without rounding to the frame.
   Expected result: Resolve opens the file and the clips fall in the same
   places; note here what Resolve did with the fractional times.

Result:

## (d) Pro Tools and MXF

1. Prepare an AAF of Pro Tools (or of Media Composer) whose media is MXF
   OP-Atom: two MXF files, for example, and the AAF that references them.
   Expected result: the AAF opens in Pro Tools and the clips point to the MXF
   files.
2. Import the AAF:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_pt.aaf $trabalho\pt.als --template templates/live12.als --audio-dir $trabalho\midia --report $trabalho\pt_import.json`
   Expected result: the report brings `MEDIA_MXF_EXTRACTED`, once per MXF
   file, with the name of the WAV, the channels, the timecode and the sha256;
   and it does not bring `MEDIA_MXF_UNSUPPORTED`.
3. Look at the `Samples/Imported` folder of the generated Live project.
   Expected result: there is one WAV per MXF file, with the name of the MXF,
   plus eight hexadecimal characters of the origin path and the `_mxf.wav`
   suffix (for example `gravacao_a1b2c3d4_mxf.wav`), in 24-bit PCM.
4. Open the `.als` in Live and play the clips, listening to the extracted
   WAVs, and check their timecode: the `time_reference` of the `bext` chunk of
   each WAV has to match the timecode of the MXF, and Live shows the same
   start timecode.
   Expected result: each WAV plays in the place of the clip, without a time
   deviation, with the same audio of the original MXF, and the timecodes
   match; if the MXF has no timecode, the report brings
   `MEDIA_MXF_TIMECODE_MISSING` and the WAV starts at 00:00:00:00.
5. Export the set back to AAF:
   `& $aaftonPython -m aafton.cli export $trabalho\pt.als $trabalho\pt_volta.aaf --report $trabalho\pt_export.json`
   Expected result: the `.aaf` is created and the report comes out without an
   error.
6. Open the returned `.aaf` in Pro Tools.
   Expected result: Pro Tools opens the AAF and the clips stay in the same
   places of the original AAF; note here any refusal of Pro Tools.

Result:

## (e) Verification of each origin

1. Verify the origin of Premiere:
   `& $aaftonPython -m aafton.cli verify $trabalho\cena_premiere.xml $trabalho\premiere.als --report $trabalho\verify_premiere.md`
   Expected result: the command exits with code 0 and the report shows the
   coverage line.
2. Verify the EDL origin of Premiere:
   `& $aaftonPython -m aafton.cli verify $trabalho\cena_premiere.edl $trabalho\premiere_edl.als --report $trabalho\verify_premiere_edl.md`
   Expected result: the command exits with code 0; the frame rate comes from
   the sidecar of the `.als`, with no option on the command line.
3. Verify the FCPXML origin of Resolve and the EDL origin of Resolve:
   `& $aaftonPython -m aafton.cli verify $trabalho\cena_resolve.fcpxml $trabalho\resolve.als --report $trabalho\verify_resolve.md`
   `& $aaftonPython -m aafton.cli verify $trabalho\cena_resolve.edl $trabalho\resolve_edl.als --report $trabalho\verify_resolve_edl.md`
   Expected result: both commands exit with code 0.
4. Verify the AAF origin of Pro Tools:
   `& $aaftonPython -m aafton.cli verify $trabalho\cena_pt.aaf $trabalho\pt.als --report $trabalho\verify_pt.md`
   Expected result: the command exits with code 0.
5. Check the coverage of each verification, not only the result: the
   "Coverage:" line says how many clips of the origin were measured.
   Expected result: the coverage of each verification is the same on both
   sides, and no clip was left out.

Result:

## (f) 29.97 drop frame

1. Prepare a timeline in Resolve (or in Premiere) with the rate of 29.97 fps
   in drop frame, with an audio clip that crosses the minute mark.
   Expected result: the timeline is in 29.97 drop frame and the clip crosses
   the minute mark.
2. Export the timeline in EDL (CMX 3600) and in FCPXML.
   Expected result: both files exist in the working folder; the EDL brings
   the line `FCM: DROP FRAME` and the timecodes with `;`, and the FCPXML
   brings `tcFormat="DF"`.
3. Import the EDL, with the drop frame rate:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_df.edl $trabalho\cena_df.als --template templates/live12.als --audio-dir $trabalho\midia --edl-rate 29.97df --report $trabalho\cena_df_import.json`
   Expected result: the `.als` is created without an error and the clip
   starts in the same frame as in the timeline.
4. Import the FCPXML:
   `& $aaftonPython -m aafton.cli import $trabalho\cena_df.fcpxml $trabalho\cena_df_fcpxml.als --template templates/live12.als --audio-dir $trabalho\midia --report $trabalho\cena_df_fcpxml_import.json`
   Expected result: the `.als` is created without an error, the clip falls in
   the same place and the offset in the source is the same.
5. Compare the three at the minute mark: the last frame before the mark and
   the first frame after it.
   Expected result: Live, the NLE and `aafton` show the same frame at the
   minute mark; if any of them diverges, the report brings
   `FORMAT_EDL_DROP_FRAME_MISMATCH` or `FORMAT_POSITION_ROUNDED_TO_FRAME`.
6. Prepare an MXF with 29.97 drop frame timecode and import the AAF that
   references it, as in block (d).
   Expected result: the report brings `MEDIA_MXF_EXTRACTED` with the timecode
   of the MXF, and the `time_reference` of the `bext` chunk of the extracted
   WAV matches it.

Result:

## (g) FCP: secondary storyline, compound clip, dual mono, J/L cut and title

1. In Final Cut Pro, edit a project with a secondary storyline, a compound
   clip and a dual mono clip, and export the project in FCPXML.
   Expected result: the file exists in the working folder, with a single
   `<project>`, with the secondary storyline in the spine and with the
   compound clip in the resources.
2. Import the FCPXML:
   `& $aaftonPython -m aafton.cli import $trabalho\projeto_fcp.fcpxml $trabalho\fcp.als --template templates/live12.als --audio-dir $trabalho\midia --report $trabalho\fcp_import.json`
   Expected result: in strict mode, the import stops at the error
   `FORMAT_NESTED_UNSUPPORTED` of the compound clip and no file is written;
   with `--permissive`, the `.als` is created, the clips of the secondary
   storyline fall in the same places of the FCP timeline, and the report
   brings `FORMAT_NESTED_UNSUPPORTED` for the compound clip and
   `FORMAT_CHANNEL_SELECTION_UNSUPPORTED` for the dual mono clip, which has
   two connected audio components.
3. Do a J/L cut in the dialogue: in the FCP, extend the audio of one clip
   into the next clip, so that the two clips overlap in the same lane, and
   export the project in FCPXML.
   Expected result: in the import, the overlapping clip goes to an extra
   track of the lane, the report brings the note
   `FORMAT_OVERLAP_MOVED_TO_TRACK` with the name of the clip and of the
   track, and no track is left with two overlapping clips.
   Listen in Live, against the FCP: the extended audio plays as in the FCP.
4. Attach a music and a marker to a title or to a video generator, and export
   the project in FCPXML.
   Expected result: in the import, the music and the marker enter the scene,
   in the position that they declare. The text that the FCP writes in the
   title is ignored, as metadata, and the import does not stop because of it.
   Check in Live, against the FCP.
5. Edit a dual mono clip, with one of the channels of the file disconnected,
   and export the project in FCPXML.
   Expected result: the strict import stops with
   `FORMAT_CHANNEL_SELECTION_UNSUPPORTED`, because the import does not
   extract a channel from an exchange file; in permissive mode, the `.als` is
   created, the two channels of the file play in Live, and the command exits
   with code 1, because the error stays in the report.
6. Put in the FCP project a clip of a polyphonic WAV of a field recorder,
   with four channels or more, and export the project in FCPXML.
   Expected result: the strict import stops with
   `FORMAT_MULTICHANNEL_UNSUPPORTED`, because the import does not extract a
   channel from an exchange file and Live plays the stereo pair; in permissive
   mode, the `.als` is created without the clip, which stays out of the
   scene, and the command exits with code 1, because the error stays in the
   report.

Result:

## (h) Closing

1. Gather here the deviations: what Premiere, Resolve or Pro Tools accepted
   differently from the expected, with the step in which they appeared.
   Expected result: the list is written here, even empty.
2. Note what Resolve did with the fractional times of the FCPXML of step
   (c) 6.
   Expected result: the observation is written here.
3. Note what is not in scope: OMF, DAWproject, the import of the new formats
   through the Studio page and the reading of MXF inside the `verify`.
   Expected result: the list is written here, as it is in
   [FORMATS.md](FORMATS.md).

Result:
