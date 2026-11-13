# Ableton Live XML Map

## 1. Live version
Creator: Ableton Live 12.4.5b11
MinorVersion: 12.0_12402
SchemaChangeCount: 5

## 2. Prototypes found
  AudioTrack        3  OK
  GroupTrack        2  OK
  ReturnTrack       1  OK
  MidiTrack         0  MISSING
  AudioClip         6  OK
  Locator           1  OK

## 3. AudioClip fields
  AudioClip/CurrentStart
  AudioClip/CurrentEnd
  AudioClip/Loop/LoopStart
  AudioClip/Loop/LoopEnd
  AudioClip/Loop/LoopOn
  AudioClip/Loop/OutMarker
  AudioClip/Loop/HiddenLoopStart
  AudioClip/Loop/HiddenLoopEnd
  AudioClip/Loop/StartRelative
  AudioClip/IsWarped
  AudioClip/Name
  AudioClip/SampleRef/FileRef/Path
  AudioClip/SampleRef/FileRef/RelativePath
  AudioClip/Fades/FadeInLength
  AudioClip/Fades/FadeOutLength
  AudioClip/SampleVolume

## 4. Missing fields / Other
AudioClip `Time`: does not exist. The AudioClip uses `CurrentStart` and `CurrentEnd` for the position in time in the Arrangement.
Locator `Time`: `Locator/Time`.
