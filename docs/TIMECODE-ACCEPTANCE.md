# Manual timecode acceptance

All the automatic checks passed. This script is the human acceptance, separate
from them: the automation measures what the code does, and here the operator
checks what is seen and heard inside Live and Pro Tools.

It is a single session, from the beginning to the end. Each block ends with a
"Result:" line to note what happened.

## (a) Preparation

1. In Live, choose AbletonOSC as the Control Surface, and close any other
   AbletonOSC client (for example another application listening on port 11001):
   it takes the UDP port 11001, the only one where AbletonOSC answers.
   Expected result: Live accepts AbletonOSC as the Control Surface and port
   11001 is free.
2. Install loopMIDI and create a virtual MIDI port.
   Expected result: the port appears in the device list of Pro Tools and of
   Live.
3. Prepare an AAF with picture: one from the preserved corpus or the one of
   the TESTE_NOVO session.
   Expected result: the AAF opens and brings the audio clips and the video.
4. Put ffmpeg on the PATH, with the drawtext filter.
   Expected result: `ffmpeg -filters` lists drawtext.

Result:

## (b) The LTC track

1. Import the AAF with the LTC track:
   `aafton import cena12.aaf cena12.als --template templates/live12.als --ltc`
   Expected result: the report shows `LTC_TRACK_WRITTEN` and
   `MTC_CHASE_SETTINGS`.
2. Open the generated set in Live.
   Expected result: there is a track `LTC`, with the generated LTC file
   (`LTC_<timecode>_<rate>.wav`). When the AAF already has a track named LTC
   (any case or spacing), the generated track is named `LTC (aafton)`.
3. Before playing, route the LTC track to a dedicated physical output,
   different from the Main output.
   Expected result: the LTC track comes out only through that output, and does
   not reach the monitors nor the bounce of Main.
4. Play the LTC track through that output and read the signal with an LTC
   reader (phone app or `ltcdump`).
   Expected result: the TC read matches the SMPTE ruler of Live, at the rate
   of the session.

Result:

## (c) The timecode page of the Studio

1. Run `aafton ui` and open http://127.0.0.1:8765/timecode.
   Expected result: the page opens; the main page (`/`) stays the same.
2. Load the `.als` generated in step (b).
   Expected result: the page shows the clock, the TC of beat zero and the state
   of the connection with Live.
3. With Live stopped, compare the clock with the SMPTE ruler of Live.
   Expected result: the clock shows the same frame.
4. With Live playing, compare the clock again and note the delay that appears
   between the clock and the position of Live.
   Expected result: the clock follows the position of Live; the delay noted
   stays written here.
5. Use the `Go` field with three different TCs.
   Expected result: Live jumps to each TC asked for.
6. Edit the TC of beat zero and click `Apply`; then click `Reset`.
   Expected result: the clock starts to use the edited TC, `Reset` goes back to
   the TC of the sidecar and the sidecar on disk does not change.

Result:

## (d) The video with the burned-in timecode

1. Import the same AAF with the video:
   `aafton import cena12.aaf cena12_tc.als --template templates/live12.als --video picture.mov`
   Expected result: the proxy `<stem>_LiveProxy_TC.mp4` is created with the
   burned-in timecode, and the video track in Live keeps the name of the
   source.
   When the frame rate of the video is different from the frame rate of the
   session, the import skips the burn with `VIDEO_BURN_RATE_MISMATCH` and
   keeps the old proxy.
2. Open the set in Live and look at the video window.
   Expected result: the video appears with the burned-in TC, equal to the TC
   of the SMPTE ruler of Live.
3. Load the same set in the timecode page and compare the video with the clock.
   Expected result: the burned-in TC in the video of the page is the same as
   the clock.

Result:

## (e) MTC chase with Pro Tools

1. In Pro Tools, send the MTC to the virtual port of loopMIDI.
   Expected result: Pro Tools shows the MTC as sent.
2. In Live, turn on the MTC sync on the MIDI input that carries the MTC.
   Expected result: Live accepts the input as an MTC source.
3. Set the SMPTE offset equal to the `offset` of the `MTC_CHASE_SETTINGS`
   notice (the TC of the beat zero) and the rate equal to the `rate` of the
   same notice.
   Expected result: Live starts to run on the TC of the beat zero of the
   picture.
4. Adjust the Sync Delay to compensate the latency between the master and
   Live.
   Expected result: Live stays aligned with Pro Tools in the roll.
5. Note the exact name of the Live preference fields for the MTC sync, for the
   offset and for the Sync Delay.
   Expected result: the three names stay written here, for when the preference
   is read again.

Result:

## (f) Back to the AAF

1. Export the set back to AAF:
   `aafton export cena12.als cena12_volta.aaf`
   Expected result: the report shows `LTC_TRACK_SKIPPED`.
2. Open the exported AAF in Pro Tools.
   Expected result: there is no LTC track, and the clips are in the same place
   as in the source set.

Result:
