from pathlib import Path

import pytest
from aafton.als_paths import AUDIO_CLIP_FIELDS, find_prototype
from aafton.als_write import WriteOptions, build_audio_clip
from aafton.ir import ClipIR, MediaAsset, FadeIR
from aafton.report import Report
from aafton.als_xml import IdAllocator, get_value, load_als, classify_id_tags

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture
def contexto():
    root = load_als(TEMPLATE)
    posicionais, unicos = classify_id_tags(root)
    return root, IdAllocator.from_root(root), find_prototype(root, "AudioClip"), posicionais, unicos


def _clip_do_spec():
    return ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="cena12_tk3.wav",
        media_id="m1",
        timeline_start_samples=48000,
        timeline_end_samples=144000,
        source_offset_samples=24000,
    )


def _asset():
    return MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path="C:/audio/cena12_tk3.wav",
    )


def test_valores_numericos_exatos_do_spec(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    node = build_audio_clip(
        _clip_do_spec(),
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["current_start"]) == "1"
    assert get_value(node, AUDIO_CLIP_FIELDS["current_end"]) == "3"
    assert get_value(node, AUDIO_CLIP_FIELDS["loop_start"]) == "0.5"
    assert get_value(node, AUDIO_CLIP_FIELDS["loop_end"]) == "2.5"
    assert get_value(node, AUDIO_CLIP_FIELDS["out_marker"]) == "2.5"
    assert get_value(node, AUDIO_CLIP_FIELDS["loop_on"]) == "false"
    assert get_value(node, AUDIO_CLIP_FIELDS["start_relative"]) == "0"
    assert get_value(node, AUDIO_CLIP_FIELDS["is_warped"]) == "false"


def test_offset_da_fonte_nunca_vai_para_start_relative(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    node = build_audio_clip(
        _clip_do_spec(),
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert float(get_value(node, AUDIO_CLIP_FIELDS["start_relative"])) == 0.0
    assert float(get_value(node, AUDIO_CLIP_FIELDS["loop_start"])) == 0.5


def test_caminho_do_arquivo_usa_barra_normal(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    asset = _asset()
    asset.resolved_path = "C:\\audio\\cena12_tk3.wav"
    node = build_audio_clip(
        _clip_do_spec(),
        asset,
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert "\\" not in get_value(node, AUDIO_CLIP_FIELDS["sample_path"])


def test_fades_do_ir_chegam_no_xml(contexto):
    from aafton.ir import FadeIR

    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    clip.fade_in = FadeIR(kind="in", duration_samples=24000)
    clip.fade_out = FadeIR(kind="out", duration_samples=48000)
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_in_length"]) == "0.5"
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_out_length"]) == "1"


def test_clip_sem_offset_conhecido_usa_zero_e_nao_explode(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    clip.source_offset_samples = None
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["loop_start"]) == "0"


def test_clip_com_midia_nao_resolvida_e_recusado(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    asset = _asset()
    asset.status = "missing"
    asset.resolved_path = None
    with pytest.raises(ValueError, match="unresolved media"):
        build_audio_clip(
            _clip_do_spec(),
            asset,
            prototipo,
            allocator,
            posicionais,
            unicos,
            WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
            Report(),
        )


def test_clip_sem_fade_zera_fades_ignorando_template(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    clip.fade_in = None
    clip.fade_out = None
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_in_length"]) == "0"
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_out_length"]) == "0"

    # Check that prototype was different so we are sure we overwrote it
    assert get_value(prototipo, AUDIO_CLIP_FIELDS["fade_in_length"]) != "0"
    assert get_value(prototipo, AUDIO_CLIP_FIELDS["fade_out_length"]) != "0"


def test_propriedades_de_curva_zeradas_sem_fade(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    clip.fade_in = None
    clip.fade_out = None
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    for curve in (
        "fade_in_curve_skew",
        "fade_in_curve_slope",
        "fade_out_curve_skew",
        "fade_out_curve_slope",
    ):
        if curve in AUDIO_CLIP_FIELDS:
            assert get_value(node, AUDIO_CLIP_FIELDS[curve]) == "0"


def test_fade_maior_que_clip_e_cortado_e_gera_warning(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    # duration is timeline_end - timeline_start = 144000 - 48000 = 96000 samples

    clip.fade_in = FadeIR(kind="in", duration_samples=80000)
    clip.fade_out = FadeIR(kind="out", duration_samples=80000)  # sum = 160000 > 96000
    report = Report(strict=False)
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        report,
    )

    # 80000 in, 80000 out => 1:1 ratio => 48000 in, 48000 out
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_in_length"]) == "1"  # 48000 samples = 1 beat
    assert get_value(node, AUDIO_CLIP_FIELDS["fade_out_length"]) == "1"

    encontrado = any(msg.code == "FADES_EXCEED_CLIP" for msg in report.issues)
    assert encontrado, "Warning FADES_EXCEED_CLIP nao emitido"


def test_crossfade_in_state_is_written(contexto):
    root, allocator, prototipo, posicionais, unicos = contexto
    clip = _clip_do_spec()
    clip.crossfade_in_state = True
    node = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "1"

    clip.crossfade_in_state = False
    node2 = build_audio_clip(
        clip,
        _asset(),
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(),
    )
    assert get_value(node2, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "0"
