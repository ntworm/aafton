from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.report import Report
from aafton.ir import SessionIR


def test_aaf_read_avisa_sobre_plugins(tmp_path):
    import aaf2

    path = tmp_path / "com_plugin.aaf"
    # Construir um AAF rapido com plugin usando a API
    with aaf2.open(str(path), "w") as f:
        comp = f.create.CompositionMob("Timeline")
        f.content.mobs.append(comp)
        slot = comp.create_timeline_slot(edit_rate=48000)
        seq = f.create.Sequence(media_kind="sound")
        slot.segment = seq

        # Um OperationGroup qualquer (desconhecido = plugin)
        from aaf2.auid import AUID

        plugin_auid = AUID("11111111-2222-3333-4444-555555555555")
        op_def = f.create.OperationDef(plugin_auid, "Um Plugin Maluco", "")
        op_def["NumberInputs"].value = 1
        op_def["IsTimeWarp"].value = False
        op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        f.dictionary["OperationDefinitions"].append(op_def)

        op = f.create.OperationGroup(op_def)
        seq.components.append(op)

    rep = Report(strict=False)
    read_aaf(path, rep)

    assert bool(rep.issues)
    assert any(i.code == "READ_PLUGIN" for i in rep.issues)


def test_aaf_write_avisa_sobre_plugins(tmp_path):
    session = SessionIR(
        name="Teste",
        sample_rate=48000,
        timecode_rate=None,
        start_timecode_samples=0,
        tracks=[],
        media=[],
        markers=[],
        source_path=None,
        source_application=None,
    )
    # Colocar algo no TrackIR que exija plugin?
    # O AAF Writer atualmente ignora os plugins e avisa, vamos verificar
    # O TrackIR tem o campo plugins=[]
    from aafton.ir import TrackIR

    session.tracks.append(
        TrackIR(
            id="t1",
            source_index=1,
            kind="audio",
            name="T1",
            channel_count=1,
            plugins=["Eq", "Comp"],
            clips=[],
        )
    )

    rep = Report(strict=False)
    write_aaf(session, tmp_path / "out.aaf", rep)

    assert any(i.code == "WRITE_PLUGIN" for i in rep.issues)


def test_aaf_read_avisa_sobre_pulldown(tmp_path):
    from aafton.aaf_read import check_pulldown
    from aafton.ir import MediaAsset

    session = SessionIR(
        name="Teste",
        sample_rate=48000,
        timecode_rate=None,
        start_timecode_samples=0,
        tracks=[],
        media=[],
        markers=[],
        source_path=None,
        source_application=None,
    )
    # Asset com sample_rate 48048 (pull-up para 24 -> 23.976)
    session.media.append(
        MediaAsset(
            id="m1", filename="audio.wav", duration_samples=1000, channels=1, sample_rate=48048
        )
    )

    rep = Report(strict=False)
    check_pulldown(session, rep)

    assert any(i.code == "PULLDOWN_MISMATCH" for i in rep.issues)


def test_aaf_read_avisa_sobre_marker_sem_posicao():
    from aafton.aaf_read import _read_markers
    from aafton.report import Report

    class CommentMarker:
        position = None
        comment = "sem posicao"

    class SlotFalso:
        name = "Markers"
        segment = type("Seq", (), {"components": [CommentMarker()]})()

    class CompFalsa:
        slots = [SlotFalso()]

    rep = Report(strict=False)
    _read_markers(CompFalsa(), 0, rep)

    assert any(i.code == "AAF_MARKER_NO_POSITION" for i in rep.issues)


def test_aaf_read_markers_converts_edit_rate_and_extracts_comment():
    from aafton.aaf_read import _read_markers
    from aafton.report import Report
    from fractions import Fraction

    class MockProp:
        def __init__(self, val):
            self.value = val

    class MockMarker:
        name = "DescriptiveMarker"

        def __init__(self, pos, comment):
            self.props = {"Position": MockProp(pos), "Comment": MockProp(comment)}

        def __getitem__(self, key):
            return self.props[key]

        def get(self, key):
            return self.props.get(key)

    class MockSlot:
        name = "Markers"
        edit_rate = Fraction(24, 1)
        segment = type("Seq", (), {"components": [MockMarker(777, "Cena 1")]})()

    class MockComp:
        slots = [MockSlot()]

    rep = Report(strict=False)
    markers = _read_markers(MockComp(), 0, rep, sample_rate=48000)
    assert len(markers) == 1
    # 777 frames at 24fps -> 777 * 48000 / 24 = 1554000 samples
    assert markers[0].position_samples == 1554000
    assert markers[0].name == "Cena 1"
