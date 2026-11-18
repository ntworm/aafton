"""As pecas do apply: o JSON das edicoes e a troca dos clips numa sessao IR.

Nenhum AAF aqui: as regras de fade, de crossfade e as recusas sao testadas
sobre um SessionIR sintetico, que e o que o apply faz entre a leitura do AAF
e a escrita do novo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.aaf_apply_pieces import ApplyRefused, parse_edits, swap_clips
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "phaseslip"
EDICOES = FIXTURES / "edicoes-exemplo.json"

SEGUNDOS = 48000


def _clip(
    clip_id: str,
    start: int,
    end: int,
    offset: int = 0,
    *,
    index: int = 0,
    **extra,
) -> ClipIR:
    """Um clip do IR sintetico, com a midia m0."""
    return ClipIR(
        id=clip_id,
        track_id="t0",
        channel_index=0,
        event_index=index,
        name=clip_id,
        media_id="m0",
        timeline_start_samples=start,
        timeline_end_samples=end,
        source_offset_samples=offset,
        **extra,
    )


def _sessao(clips, *, canais: int = 1, duracao: int = SEGUNDOS) -> SessionIR:
    """Uma sessao de uma track, com a midia resolvida."""
    asset = MediaAsset(
        id="m0",
        filename="fonte.wav",
        channels=1,
        sample_rate=SEGUNDOS,
        duration_samples=duracao,
        status="resolved",
        declared_location="fonte.wav",
        resolved_path="fonte.wav",
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="Track 0",
        kind="audio",
        channel_count=canais,
        clips=list(clips),
    )
    return SessionIR(
        name="Sintetico",
        sample_rate=SEGUNDOS,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )


def _codigos(report: Report) -> list[str]:
    """Os codigos das issues do relatorio, na ordem em que entraram."""
    return [issue.code for issue in report.issues]


def test_parse_edits_le_o_json_do_exemplo() -> None:
    """O JSON de edicoes do fixture vira as pecas por id de clip."""
    dados = json.loads(EDICOES.read_text(encoding="utf-8"))
    assert parse_edits(dados) == {
        "t4c0_ch0": [(0, 1401363, 37)],
        "t3c0_ch0": [(0, 700700, 10), (700700, 1401375, 700725)],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "clips": {}},
        {"clips": {}},
        {"version": 1},
        {"version": 1, "clips": {"c0": []}},
        {"version": 1, "clips": {"c0": [{"start": 0, "end": 10}]}},
        {"version": 1, "clips": {"c0": [{"start": 0, "end": 10, "offset": "0"}]}},
        {"version": 1, "clips": {"c0": [{"start": 0, "end": 10, "offset": True}]}},
        {"version": 1, "clips": {"c0": [{"start": 10, "end": 10, "offset": 0}]}},
        {"version": 1, "clips": {"c0": [{"start": 0, "end": 10, "offset": -1}]}},
        {
            "version": 1,
            "clips": {
                "c0": [
                    {"start": 0, "end": 100, "offset": 0},
                    {"start": 50, "end": 200, "offset": 50},
                ]
            },
        },
    ],
)
def test_parse_edits_recusa_o_json_quebrado(payload: dict) -> None:
    """Toda edicao malformada e recusada com o codigo APPLY_BAD_EDITS."""
    with pytest.raises(ApplyRefused) as erro:
        parse_edits(payload)
    assert erro.value.code == "APPLY_BAD_EDITS"


def test_swap_clips_parte_o_clip_em_pecas() -> None:
    """Um clip virado em duas pecas vira dois clips, com id e evento novos."""
    sessao = _sessao([_clip("c0", 0, SEGUNDOS)])
    report = Report(strict=False)
    tracks = swap_clips(sessao, {"c0": [(0, 100, 0), (100, 200, 100)]}, report)
    assert len(tracks) == 1
    clips = tracks[0].clips
    assert [clip.id for clip in clips] == ["c0/1", "c0/2"]
    assert [
        (clip.timeline_start_samples, clip.timeline_end_samples, clip.source_offset_samples)
        for clip in clips
    ] == [(0, 100, 0), (100, 200, 100)]
    assert [clip.event_index for clip in clips] == [0, 1]
    assert report.issues == []


def test_swap_clips_encurta_o_fade_que_nao_cabe_na_peca() -> None:
    """O fade que passa da peca e encurtado, e o relatorio diz."""
    fades = FadeIR(kind="in", duration_samples=1000)
    sessao = _sessao(
        [
            _clip(
                "c0", 0, SEGUNDOS, fade_in=fades, fade_out=FadeIR(kind="out", duration_samples=1000)
            )
        ]
    )
    report = Report(strict=False)
    tracks = swap_clips(sessao, {"c0": [(0, 400, 0)]}, report)
    peca = tracks[0].clips[0]
    assert peca.fade_in is not None
    assert peca.fade_in.duration_samples == 400
    assert peca.fade_out is None
    assert _codigos(report).count("APPLY_FADE_SHORTENED") == 2


def test_swap_clips_remove_o_crossfade_quando_a_sobreposicao_some() -> None:
    """O crossfade de dois clips que deixa de se cruzar e removido."""
    sessao = _sessao(
        [
            _clip("a", 0, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 2000, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    report = Report(strict=False)
    tracks = swap_clips(sessao, {"a": [(0, 800, 0)]}, report)
    assert "APPLY_CROSSFADE_REMOVED" in _codigos(report)
    primeiro, segundo = tracks[0].clips
    assert primeiro.fade_out is None
    assert segundo.fade_in is None


def test_swap_clips_redimensiona_o_crossfade_que_mudou() -> None:
    """O crossfade que mudou de tamanho vira o tamanho novo da sobreposicao."""
    sessao = _sessao(
        [
            _clip("a", 0, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 2000, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    report = Report(strict=False)
    tracks = swap_clips(sessao, {"b": [(800, 2000, 0)]}, report)
    assert "APPLY_CROSSFADE_RESIZED" in _codigos(report)
    primeiro, segundo = tracks[0].clips
    assert primeiro.fade_out is not None
    assert primeiro.fade_out.duration_samples == 200
    assert segundo.fade_in is not None
    assert segundo.fade_in.duration_samples == 200


def test_swap_clips_recusa_o_crossfade_mais_longo_que_o_clip() -> None:
    """Um crossfade que passaria do clip e recusado com APPLY_CROSSFADE_TOO_LONG."""
    sessao = _sessao(
        [
            _clip("a", 500, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 1200, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    report = Report(strict=False)
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"b": [(0, 1200, 0)]}, report)
    assert erro.value.code == "APPLY_CROSSFADE_TOO_LONG"


def test_swap_clips_recusa_a_sobreposicao_criada() -> None:
    """A edicao que faz dois clips da mesma track se cruzarem e recusada."""
    sessao = _sessao([_clip("a", 0, 1000), _clip("b", 2000, 3000, index=1)])
    report = Report(strict=False)
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"b": [(500, 3000, 0)]}, report)
    assert erro.value.code == "APPLY_OVERLAP_CREATED"


def test_swap_clips_recusa_o_clip_desconhecido() -> None:
    """Um id de clip que nao esta no AAF e recusado com APPLY_UNKNOWN_CLIP."""
    sessao = _sessao([_clip("a", 0, SEGUNDOS)])
    report = Report(strict=False)
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"naoexiste": [(0, 100, 0)]}, report)
    assert erro.value.code == "APPLY_UNKNOWN_CLIP"


def test_swap_clips_recusa_a_peca_alem_da_midia() -> None:
    """A peca que passa do fim do arquivo e recusada com APPLY_OUTSIDE_MEDIA."""
    sessao = _sessao([_clip("a", 0, SEGUNDOS)], duracao=1000)
    report = Report(strict=False)
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"a": [(0, 2000, 0)]}, report)
    assert erro.value.code == "APPLY_OUTSIDE_MEDIA"


def test_swap_clips_recusa_a_track_que_nao_e_mono() -> None:
    """O clip de uma track com mais de um canal e recusado."""
    sessao = _sessao([_clip("a", 0, SEGUNDOS)], canais=2)
    report = Report(strict=False)
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"a": [(0, 100, 0)]}, report)
    assert erro.value.code == "APPLY_TRACK_NOT_MONO"


def test_swap_clips_mantem_o_crossfade_no_pedaco_que_ainda_cruza() -> None:
    """Dividir o clip anterior deixa o crossfade no pedaco que ainda cruza o vizinho."""
    sessao = _sessao(
        [
            _clip("a", 0, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 2000, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    report = Report(strict=False)
    tracks = swap_clips(sessao, {"a": [(0, 950, 0), (2500, 2600, 2500)]}, report)
    por_id = {c.id: c for c in tracks[0].clips}
    assert por_id["a/1"].fade_out is not None
    assert por_id["a/1"].fade_out.duration_samples == 50
    assert por_id["b"].fade_in is not None
    assert por_id["b"].fade_in.duration_samples == 50
    assert por_id["a/2"].fade_out is None


def test_swap_clips_recusa_o_pedaco_que_fica_por_baixo_do_vizinho() -> None:
    """Um pedaco que fica inteiro sob o clip vizinho nao vira crossfade: recusa."""
    sessao = _sessao(
        [
            _clip("a", 0, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 2000, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"a": [(0, 950, 0), (950, 1000, 950)]}, Report(strict=False))
    assert erro.value.code == "APPLY_CROSSFADE_TOO_LONG"


def test_swap_clips_nao_avisa_crossfade_que_nao_mudou() -> None:
    """Editar o clip longe do crossfade nao gera aviso de crossfade redimensionado."""
    sessao = _sessao(
        [
            _clip("a", 0, 1000, fade_out=FadeIR(kind="out", duration_samples=100)),
            _clip("b", 900, 2000, index=1, fade_in=FadeIR(kind="in", duration_samples=100)),
        ]
    )
    report = Report(strict=False)
    swap_clips(sessao, {"a": [(0, 400, 0), (400, 1000, 410)]}, report)
    assert "APPLY_CROSSFADE_RESIZED" not in _codigos(report)


def test_swap_clips_recusa_a_sobreposicao_nova_entre_clips_nao_vizinhos() -> None:
    """Numa cadeia de crossfades, o clip que passa a cruzar o de dois adiante e recusado."""
    sessao = _sessao(
        [
            _clip("x", 0, 150, fade_out=FadeIR(kind="out", duration_samples=50)),
            _clip(
                "w",
                100,
                200,
                index=1,
                fade_in=FadeIR(kind="in", duration_samples=50),
                fade_out=FadeIR(kind="out", duration_samples=50),
            ),
            _clip("z", 150, 400, index=2, fade_in=FadeIR(kind="in", duration_samples=50)),
        ]
    )
    with pytest.raises(ApplyRefused) as erro:
        swap_clips(sessao, {"x": [(0, 200, 0)]}, Report(strict=False))
    assert erro.value.code == "APPLY_OVERLAP_CREATED"
