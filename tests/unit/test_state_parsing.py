from aafton.protools_text import ProToolsTextExport


def test_empty_state_parsing():
    content = """SESSION NAME:	SPOT_AAF_TESTE
SAMPLE RATE:	48000.000000
BIT DEPTH:	24-bit
SESSION START TIMECODE:	00:00:00:00
TIMECODE FORMAT:	25 Frame
# OF AUDIO TRACKS:	1
# OF AUDIO CLIPS:	0
# OF AUDIO FILES:	0

T R A C K  L I S T I N G
TRACK NAME:	Audio 3
COMMENTS:	
USER DELAY:	0 Samples
STATE: 
PLUG-INS: 	Channel Strip (stereo)

M A R K E R S  L I S T I N G
#   LOCATION     TIME REFERENCE    UNITS    NAME    COMMENTS
"""
    parsed = ProToolsTextExport.parse(content)
    track = parsed.tracks["Audio 3"]
    assert track.state == ""
    assert "Channel Strip (stereo)" in track.plugins
