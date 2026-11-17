"""Track combinada (Audio Channel Combiner) com dois slots na organizacao.

O leitor AAF guarda numa track estereo do Pro Tools uma projecao por canal,
cada uma de um slot (original_source_indices [s0, s1]). A chave de origem de
cada projecao e o slot do seu canal, com a ordem contada no canal, como no
verify._index; na escrita o clip do Live leva as chaves dos dois canais.
"""

from __future__ import annotations

import json
from pathlib import Path

from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_apply import annotate_origin_keys, apply_proposal
from aafton.organize_plan import build_proposal
from aafton.organize_units import origin_keys_for_track
from aafton.report import Report
from aafton.sidecar import read_anchor
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate
from aafton.verify_organize import rebuild_organized_destination

TEMPLATE = Path("templates/live12.als")

# Os sinais de hoje, sem a compactacao: estes testes conferem a disposicao de
# uma track por track do editor.
SINAIS_SEM_COMPACT = ("file", "meta", "name", "family", "color")


def _clip(idx: int, canal: int, nome: str, inicio: int, fim: int) -> ClipIR:
    return ClipIR(
        id=f"c{idx}_{canal}",
        track_id="comb",
        channel_index=canal,
        event_index=idx,
        name=nome,
        media_id=f"m_{nome}",
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=0,
    )


def _sessao(tmp_path: Path) -> SessionIR:
    nomes = [("A024_A021_0519VG_001.r3d", 0, 48000), ("A017_A013_0514N0_001.r3d", 96000, 144000)]
    clips = []
    midias = []
    for i, (nome, ini, fim) in enumerate(nomes):
        wav = tmp_path / f"{nome}.wav"
        wav.write_bytes(b"")
        midias.append(
            MediaAsset(
                id=f"m_{nome}",
                filename=nome,
                channels=2,
                sample_rate=48000,
                duration_samples=480000,
                status="resolved",
                resolved_path=str(wav),
            )
        )
        clips += [_clip(i, 0, nome, ini, fim), _clip(i, 1, nome, ini, fim)]
    track = TrackIR(
        id="comb",
        source_index=0,
        name="REFAUDIO",
        kind="audio",
        channel_count=2,
        original_source_indices=[0, 1],
        clips=clips,
    )
    return SessionIR(
        name="combinada",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=midias,
        source_path=str(tmp_path / "origem.aaf"),
    )


def test_chave_de_origem_e_o_slot_do_canal(tmp_path):
    sessao = _sessao(tmp_path)
    chaves = origin_keys_for_track(sessao.tracks[0])

    por_canal = sorted((c.channel_index, chaves[id(c)]) for c in sessao.tracks[0].clips)
    assert por_canal == [(0, (0, 0)), (0, (0, 1)), (1, (1, 0)), (1, (1, 1))]


def test_clip_do_live_leva_as_chaves_dos_dois_canais(tmp_path):
    sessao = _sessao(tmp_path)
    annotate_origin_keys(sessao)
    proposta = build_proposal(
        sessao,
        stereo="pair",
        level="permissive",
        signals=SINAIS_SEM_COMPACT,
        media_meta=lambda _: None,
    )
    process_stereo_pairing(sessao, "pair", Report(strict=False), None, dry_run=True)
    assert apply_proposal(sessao, proposta) == []

    saida = tmp_path / "comb.als"
    write_session(sessao, TEMPLATE, saida, WriteOptions(sample_rate=48000), Report(strict=False))

    origens = json.loads(saida.with_name("comb.aafton.json").read_text(encoding="utf-8"))
    chaves = sorted(
        tuple(map(tuple, clip[2])) for track in origens["clip_origins"] for clip in track["clips"]
    )
    assert chaves == [((0, 0), (1, 0)), ((0, 1), (1, 1))]

    # A remontagem do verify devolve um slot por canal, como a origem combinada.
    anchor = read_anchor(saida)
    destino = SessionIR(
        name="d",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[t for t in sessao.tracks],
    )
    for track in destino.tracks:
        from aafton.als_write_tracks import clips_for_live_track

        track.clips = clips_for_live_track(sessao, track)
    _, indices = rebuild_organized_destination(destino, anchor, anchor.source_path)
    assert indices == [[0], [1]]


def _sessao_fonte_unica(tmp_path: Path, nomes: list[tuple[str, int, int]]) -> SessionIR:
    """Combinada que le um WAV estereo: projecoes com source_channel 1 e 2."""
    sessao = _sessao(tmp_path)
    clips = []
    for i, (nome, ini, fim) in enumerate(nomes):
        for canal in (0, 1):
            clip = _clip(i, canal, nome, ini, fim)
            clip.media_id = "m_A024_A021_0519VG_001.r3d"
            clip.source_channel = canal + 1
            clip.source_offset_samples = ini
            clips.append(clip)
    sessao.tracks[0].clips = clips
    return sessao


def test_colapso_de_fonte_unica_leva_as_chaves_do_canal_direito(tmp_path):
    nomes = [("A024_A021_0519VG_001.r3d", 0, 48000), ("A024_A021_0519VG_001.r3d", 96000, 144000)]
    sessao = _sessao_fonte_unica(tmp_path, nomes)
    annotate_origin_keys(sessao)
    proposta = build_proposal(
        sessao,
        stereo="pair",
        level="permissive",
        signals=SINAIS_SEM_COMPACT,
        media_meta=lambda _: None,
    )
    process_stereo_pairing(sessao, "pair", Report(strict=False), None, dry_run=True)

    assert len(sessao.tracks[0].clips) == 2
    assert apply_proposal(sessao, proposta) == []
    chaves = sorted(tuple(map(tuple, c.raw["origin_keys"])) for t in sessao.tracks for c in t.clips)
    assert chaves == [((0, 0), (1, 0)), ((0, 1), (1, 1))]


def test_cadeia_de_crossfade_na_combinada_muda_de_track_inteira(tmp_path):
    # Dois eventos sobrepostos na combinada: uma unidade so, com as quatro chaves.
    nomes = [("A024_A021_0519VG_001.r3d", 0, 60000), ("A024_A021_0519VG_001.r3d", 48000, 144000)]
    sessao = _sessao_fonte_unica(tmp_path, nomes)
    annotate_origin_keys(sessao)
    proposta = build_proposal(
        sessao,
        stereo="pair",
        level="permissive",
        signals=SINAIS_SEM_COMPACT,
        media_meta=lambda _: None,
    )
    process_stereo_pairing(sessao, "pair", Report(strict=False), None, dry_run=True)

    assert apply_proposal(sessao, proposta) == []
    todas = sorted(
        k for t in sessao.tracks for c in t.clips for k in map(tuple, c.raw["origin_keys"])
    )
    assert todas == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_tracks_do_editor_com_o_mesmo_nome_nao_se_fundem(tmp_path):
    from aafton.ir import TrackIR as Track

    sessao = _sessao(tmp_path)
    outra = Track(id="t2", source_index=2, name="REFAUDIO", kind="audio", channel_count=1)
    outra.clips = [_clip(9, 0, "A017_A013_0514N0_001.r3d", 0, 48000)]
    outra.clips[0].track_id = "t2"
    sessao.tracks.append(outra)
    annotate_origin_keys(sessao)
    proposta = build_proposal(
        sessao,
        stereo="pair",
        level="permissive",
        signals=SINAIS_SEM_COMPACT,
        media_meta=lambda _: None,
    )

    chaves = [t.key for g in proposta.groups for t in g.tracks]
    assert len(chaves) == len(set(chaves))
    for grupo in proposta.groups:
        for track in grupo.tracks:
            spans = sorted((u.start_samples, u.end_samples) for u in track.units)
            assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), track.name
