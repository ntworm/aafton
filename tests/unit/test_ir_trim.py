from pathlib import Path

import pytest

from aafton.aaf_write import write_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.formats.destino import write_destination
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.ir_trim import TRIMMED_CODE, samples_per_frame, trim_to_media_end
from aafton.pipeline import ImportOptions, export_session, import_session
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")
MIDIA = 48000
# 24 fps a 48 kHz: um frame tem 2000 samples.
QUADRO = 2000

precisa_template = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


def _clip(cid, inicio, fim, *, canal=0, offset=0, media="m1", **extra):
    return ClipIR(
        id=cid,
        track_id="t1",
        channel_index=canal,
        event_index=0,
        name=cid,
        media_id=media,
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=offset,
        **extra,
    )


def _asset(media_id="m1", *, duracao=MIDIA, taxa=48000, caminho=None):
    return MediaAsset(
        id=media_id,
        filename=caminho.name if caminho else f"{media_id}.wav",
        channels=1,
        sample_rate=taxa,
        duration_samples=duracao,
        status="resolved",
        resolved_path=str(caminho) if caminho else None,
    )


def _sessao(clips, media=None, *, canais=1):
    track = TrackIR(
        id="t1", source_index=0, name="Audio 7", kind="audio", channel_count=canais, clips=clips
    )
    return SessionIR(
        name="corte",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=media if media is not None else [_asset()],
    )


def _notas(report):
    return [issue for issue in report.issues if issue.code == TRIMMED_CODE]


def _cortar(session):
    report = Report(strict=False)
    trim_to_media_end(session, report)
    return report


def _sessao_com_wav(tmp_path, fim):
    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(MIDIA))
    return _sessao([_clip("c1", 0, fim)], [_asset(caminho=wav)])


def test_samples_por_frame_e_exato():
    assert samples_per_frame(_sessao([])) == QUADRO
    sessao = _sessao([])
    sessao.timecode_rate = TimecodeRate(30000, 1001)
    assert samples_per_frame(sessao) * 30000 == 48000 * 1001


def test_clip_216_samples_alem_da_midia_e_cortado_com_nota():
    sessao = _sessao([_clip("c1", 0, MIDIA + 216)])
    report = _cortar(sessao)

    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA
    notas = _notas(report)
    assert len(notas) == 1
    assert notas[0].severity == "info"
    assert notas[0].message == (
        "1 clips ended up to 216 samples past the end of their media (less than one frame); "
        "trimmed to the media end"
    )


def test_varios_clips_cortados_geram_uma_nota_com_o_maior_excesso():
    clips = [_clip("c1", 0, MIDIA + 216), _clip("c2", 100000, 100000 + MIDIA + 138, media="m2")]
    report = _cortar(_sessao(clips, [_asset(), _asset("m2")]))

    notas = _notas(report)
    assert len(notas) == 1
    assert notas[0].message.startswith("2 clips ended up to 216 samples past")


def test_clip_exatamente_no_fim_fica_intacto_sem_nota():
    sessao = _sessao([_clip("c1", 0, MIDIA)])
    report = _cortar(sessao)
    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA
    assert _notas(report) == []


def test_excesso_logo_abaixo_de_um_frame_e_cortado():
    sessao = _sessao([_clip("c1", 0, MIDIA + QUADRO - 1)])
    _cortar(sessao)
    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA


def test_clip_que_ficaria_com_menos_de_um_frame_fica_intacto():
    sessao = _sessao([_clip("c1", 0, QUADRO, offset=MIDIA - 1)])
    report = _cortar(sessao)
    assert sessao.tracks[0].clips[0].timeline_end_samples == QUADRO
    assert _notas(report) == []


def test_fade_de_saida_cabe_depois_do_fade_de_entrada():
    sessao = _sessao(
        [
            _clip(
                "c1",
                0,
                MIDIA + 216,
                fade_in=FadeIR(kind="in", duration_samples=24000),
                fade_out=FadeIR(kind="out", duration_samples=24100),
            )
        ]
    )
    _cortar(sessao)
    clip = sessao.tracks[0].clips[0]
    assert clip.fade_in.duration_samples + clip.fade_out.duration_samples == MIDIA


def test_um_frame_inteiro_alem_fica_intacto_e_o_export_reprova(tmp_path):
    sessao = _sessao_com_wav(tmp_path, MIDIA + QUADRO)
    report = Report(strict=False)

    write_aaf(sessao, tmp_path / "frame.aaf", report)

    codes = [issue.code for issue in report.issues]
    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA + QUADRO
    assert TRIMMED_CODE not in codes
    assert "EXPORT_SOURCECLIP_EXCEEDS_SOURCE" in codes


@pytest.mark.parametrize(
    "seguinte, fade_out",
    [
        # o proximo clip comeca antes do fim: crossfade por sobreposicao
        (dict(inicio=MIDIA - 784, fade_in=FadeIR(kind="in", duration_samples=1000)), 1000),
        # o proximo clip comeca no fim, marcado como crossfade
        (dict(inicio=MIDIA + 216, crossfade_in_state=True), None),
        # o fade de saida faz par com o fade de entrada do proximo
        (dict(inicio=MIDIA + 216, fade_in=FadeIR(kind="in", duration_samples=500)), 500),
    ],
)
def test_clip_alem_da_midia_numa_transicao_fica_intacto(seguinte, fade_out):
    inicio = seguinte.pop("inicio")
    saida = FadeIR(kind="out", duration_samples=fade_out) if fade_out else None
    clips = [
        _clip("c1", 0, MIDIA + 216, fade_out=saida),
        _clip("c2", inicio, inicio + 10000, media="m2", **seguinte),
    ]
    sessao = _sessao(clips, [_asset(), _asset("m2")])
    report = _cortar(sessao)

    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA + 216
    assert _notas(report) == []


def test_proximo_clip_em_outro_canal_nao_impede_o_corte():
    clips = [_clip("c1", 0, MIDIA + 216), _clip("c2", 1000, 60000, canal=1, media="m2")]
    sessao = _sessao(clips, [_asset(), _asset("m2", duracao=100000)], canais=2)
    _cortar(sessao)
    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA


def test_asset_com_taxa_diferente_da_sessao_fica_intacto():
    sessao = _sessao([_clip("c1", 0, MIDIA + 216)], [_asset(taxa=44100)])
    report = _cortar(sessao)
    assert sessao.tracks[0].clips[0].timeline_end_samples == MIDIA + 216
    assert _notas(report) == []


def test_offset_ou_duracao_desconhecidos_ficam_intactos():
    sem_offset = _clip("c1", 0, MIDIA + 216)
    sem_offset.source_offset_samples = None
    sessao = _sessao([sem_offset, _clip("c2", 100000, 100000 + MIDIA + 216, media="m2")])
    sessao.media = [_asset(), _asset("m2", duracao=None)]
    _cortar(sessao)
    assert [c.timeline_end_samples for c in sessao.tracks[0].clips] == [
        MIDIA + 216,
        100000 + MIDIA + 216,
    ]


def test_fade_de_saida_maior_que_o_clip_novo_e_encurtado():
    sessao = _sessao(
        [_clip("c1", 0, MIDIA + 216, fade_out=FadeIR(kind="out", duration_samples=48100))]
    )
    _cortar(sessao)
    assert sessao.tracks[0].clips[0].fade_out.duration_samples == MIDIA


def test_dois_canais_com_o_mesmo_excesso_sao_cortados():
    clips = [_clip("l", 0, MIDIA + 216, canal=0), _clip("r", 0, MIDIA + 216, canal=1)]
    sessao = _sessao(clips, canais=2)
    report = _cortar(sessao)

    assert [c.timeline_end_samples for c in sessao.tracks[0].clips] == [MIDIA, MIDIA]
    assert _notas(report)[0].message.startswith("2 clips ended up to 216 samples")


def test_dois_canais_com_excessos_diferentes_ficam_intactos():
    clips = [_clip("l", 0, MIDIA + 216, canal=0), _clip("r", 0, MIDIA + 216, canal=1, offset=100)]
    sessao = _sessao(clips, canais=2)
    report = _cortar(sessao)

    assert [c.timeline_end_samples for c in sessao.tracks[0].clips] == [MIDIA + 216, MIDIA + 216]
    assert _notas(report) == []


def _sem_erro(report):
    codes = [issue.code for issue in report.issues]
    assert not [issue for issue in report.issues if issue.severity == "error"], codes
    assert TRIMMED_CODE in codes
    assert "EXPORT_SELF_CHECK_OK" in codes


def test_export_pela_cli_corta_e_nao_reprova(tmp_path):
    report = Report(strict=True)
    write_destination(_sessao_com_wav(tmp_path, MIDIA + 216), tmp_path / "cli.aaf", report)
    _sem_erro(report)


@precisa_template
def test_export_session_do_studio_corta_e_nao_reprova(tmp_path):
    als = tmp_path / "corte.als"
    write_session(
        _sessao_com_wav(tmp_path, MIDIA + 216),
        TEMPLATE,
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )
    report = Report(strict=True)

    export_session(als, tmp_path / "studio.aaf", report)

    _sem_erro(report)


@precisa_template
def test_import_corta_o_clip_alem_da_midia(tmp_path):
    # O AAF de teste declara 60 s de midia; o clip pede 216 samples a mais.
    wav = tmp_path / "voz.wav"
    wav.write_bytes(wav_bytes(48000 * 60))
    aaf = build_linked_aaf(tmp_path / "corte.aaf", [(0, 48000 * 60 + 216, 0)], wav)
    report = Report(strict=False)

    session = import_session(
        ImportOptions(input=str(aaf), output=str(tmp_path / "corte.als"), template=str(TEMPLATE)),
        report,
    )

    clip = session.tracks[0].clips[0]
    assert clip.duration_samples == 48000 * 60
    assert len(_notas(report)) == 1
