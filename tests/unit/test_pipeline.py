"""Tests of the import and export pipeline of the aafton."""

from __future__ import annotations

from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session as read_session_do_set
from aafton.cli import build_parser
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.pipeline import (
    ImportOptions,
    NoTracksError,
    export_session,
    import_session,
    media_dir_for,
    media_roots,
    replay_issues,
    select_tracks,
)
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_calibration_aaf, build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

CAMPOS_DE_IMPORT = [
    "input",
    "output",
    "template",
    "live_origin",
    "bpm",
    "media",
    "stereo",
    "fades",
    "groups",
    "audio_dir",
    "video",
    "video_start",
    "video_no_burn",
    "session_text",
    "permissive",
    "ltc",
    "edl_rate",
]

AUTOMACAO_DIALOGO = "automacao de 'DIALOGO 1' ainda nao e reconciliada para exportacao"
AUTOMACAO_MUSICA = "automacao de 'MUSICA 1' ainda nao e reconciliada para exportacao"


def _clip(identificador: str, track_id: str, media_id: str) -> ClipIR:
    """Clip of one second, linked to the given media."""
    return ClipIR(
        id=identificador,
        track_id=track_id,
        channel_index=0,
        event_index=0,
        name="cena",
        media_id=media_id,
        timeline_start_samples=0,
        timeline_end_samples=48000,
    )


def _sessao_com_duas_tracks() -> SessionIR:
    """Session built by hand, with two tracks, two clips and two media."""
    return SessionIR(
        name="sessao",
        sample_rate=48000,
        timecode_rate=TimecodeRate(num=25, den=1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(
                id="t0",
                source_index=0,
                name="DIALOGO 1",
                kind="audio",
                channel_count=1,
                clips=[_clip("c1", "t0", "m1")],
            ),
            TrackIR(
                id="t1",
                source_index=1,
                name="MUSICA 1",
                kind="audio",
                channel_count=1,
                clips=[_clip("c2", "t1", "m2")],
            ),
        ],
        media=[
            MediaAsset(
                id="m1",
                filename="dialogo.wav",
                status="resolved",
                resolved_path="/midia/dialogo.wav",
            ),
            MediaAsset(
                id="m2",
                filename="musica.wav",
                status="resolved",
                resolved_path="/midia/musica.wav",
            ),
        ],
        markers=[],
        unsupported_export_features=[
            AUTOMACAO_DIALOGO,
            AUTOMACAO_MUSICA,
        ],
    )


def _leitura_da_faixa(nome: str, mensagem: str) -> Report:
    """Report of a read that warned about the automation of one track."""
    reading = Report(strict=False)
    reading.warning("ALS_AUTOMATION_UNSUPPORTED", mensagem, {"track": nome})
    return reading


def _set_importado(tmp_path: Path) -> Path:
    """Imports a calibration AAF with two tracks and returns the set path."""
    wav = tmp_path / "musica.wav"
    wav.write_bytes(wav_bytes(48000 * 120))
    faixas = [
        {
            "name": "DIALOGO 1",
            "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 48000}],
        },
        {
            "name": "MUSICA 1",
            "clips": [{"path": str(wav), "start": 0, "offset": 0, "duration": 48000}],
        },
    ]
    aaf = build_calibration_aaf(tmp_path / "calibracao.aaf", faixas, frames=48000)
    set_path = tmp_path / "saida.als"
    import_session(
        ImportOptions(input=str(aaf), output=str(set_path), template=str(TEMPLATE)),
        Report(strict=False),
    )
    return set_path


def test_import_options_have_the_fields_of_the_contract():
    assert [campo.name for campo in fields(ImportOptions)] == CAMPOS_DE_IMPORT


def test_import_options_defaults_match_the_import_command():
    namespace = build_parser().parse_args(["import", "a.aaf", "b.als"])
    campos = fields(ImportOptions)
    defaults = {f.name: f.default for f in campos if f.default is not MISSING}
    for name, value in defaults.items():
        assert value == getattr(namespace, name), name
    required = {f.name for f in campos if f.default is MISSING}
    assert required == {"input", "output"}


def test_media_roots_and_media_dir_for_take_plain_values(tmp_path):
    origem = str(tmp_path / "origem.aaf")

    assert media_roots(origem, None) == [tmp_path]
    assert media_roots(origem, str(tmp_path / "midia")) == [tmp_path / "midia", tmp_path]
    esperado = (tmp_path / "projeto Project" / "Samples" / "Imported").resolve()
    assert media_dir_for(str(tmp_path / "projeto.als")) == esperado


def test_import_session_writes_the_als_and_the_sidecar(tmp_path):
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(48000 * 120))
    aaf = build_linked_aaf(
        tmp_path / "cena12.aaf",
        [(0, 96000, 24000), (192000, 96000, 0)],
        wav,
    )
    output = tmp_path / "saida.als"
    report = Report(strict=False)

    session = import_session(
        ImportOptions(input=str(aaf), output=str(output), template=str(TEMPLATE)),
        report,
    )

    assert output.exists()
    assert output.with_name(output.stem + ".aafton.json").exists()
    assert [track.name for track in session.tracks] == ["DIALOGO 1"]
    assert len(session.tracks[0].clips) == 2
    inicio = session.start_timecode_samples
    assert [clip.timeline_start_samples - inicio for clip in session.tracks[0].clips] == [
        0,
        192000,
    ]
    assert session.sample_rate == 48000
    assert session.timecode_rate.fps == 25


def test_select_tracks_keeps_the_chosen_ones_and_prunes_the_rest():
    session = _sessao_com_duas_tracks()
    reading = _leitura_da_faixa("MUSICA 1", AUTOMACAO_MUSICA)

    removidas = select_tracks(session, [0], reading)

    assert removidas == {"MUSICA 1"}
    assert [track.id for track in session.tracks] == ["t0"]
    assert [asset.id for asset in session.media] == ["m1"]
    assert session.unsupported_export_features == [AUTOMACAO_DIALOGO]


def test_select_tracks_keeps_a_feature_that_only_mentions_the_removed_track():
    session = _sessao_com_duas_tracks()
    session.unsupported_export_features.append("clip de 'MUSICA 1' reescrito pelo exportador")
    reading = _leitura_da_faixa("MUSICA 1", AUTOMACAO_MUSICA)

    select_tracks(session, [0], reading)

    assert session.unsupported_export_features == [
        AUTOMACAO_DIALOGO,
        "clip de 'MUSICA 1' reescrito pelo exportador",
    ]


def test_select_tracks_keeps_the_features_of_a_removed_track_with_a_shared_name():
    session = _sessao_com_duas_tracks()
    session.tracks[1].name = "DIALOGO 1"
    session.unsupported_export_features = [AUTOMACAO_DIALOGO]
    reading = _leitura_da_faixa("DIALOGO 1", AUTOMACAO_DIALOGO)

    removidas = select_tracks(session, [0], reading)

    assert removidas == set()
    assert session.unsupported_export_features == [AUTOMACAO_DIALOGO]
    report = Report(strict=False)
    replay_issues(reading, report, removidas)
    assert [issue.code for issue in report.issues] == ["ALS_AUTOMATION_UNSUPPORTED"]


def test_replay_issues_skips_the_issues_of_the_removed_tracks():
    reading = Report(strict=False)
    reading.error("ERRO", "erro em DIALOGO 1", {"track": "DIALOGO 1"})
    reading.warning("AVISO", "aviso em MUSICA 1", {"track": "MUSICA 1"})
    report = Report(strict=False)

    replay_issues(reading, report, {"MUSICA 1"})

    assert [issue.code for issue in report.issues] == ["ERRO"]


def test_replay_issues_raises_in_a_strict_report():
    reading = Report(strict=False)
    reading.error("ERRO", "erro em DIALOGO 1", {"track": "DIALOGO 1"})
    report = Report(strict=True)

    with pytest.raises(StrictModeError):
        replay_issues(reading, report, {"MUSICA 1"})


def test_export_session_without_a_selection_raises(tmp_path):
    report = Report(strict=False)
    destino = tmp_path / "saida.aaf"

    with pytest.raises(NoTracksError) as erro:
        export_session(tmp_path / "inexistente.als", destino, report, tracks=[])

    assert str(erro.value) == "the export has no track selected"
    assert not destino.exists()


def test_export_session_stops_at_the_error_of_a_kept_track(tmp_path, monkeypatch):
    set_path = _set_importado(tmp_path)

    def leitura_com_erro(caminho, report):
        session = read_session_do_set(caminho, report)
        report.error("ERRO_DE_LEITURA", "erro em DIALOGO 1", {"track": "DIALOGO 1"})
        return session

    monkeypatch.setattr("aafton.pipeline.read_session", leitura_com_erro)
    destino = tmp_path / "parcial.aaf"
    report = Report(strict=True)

    with pytest.raises(StrictModeError):
        export_session(set_path, destino, report, tracks=[0])

    assert not destino.exists()


def test_export_session_ignores_the_error_of_a_removed_track(tmp_path, monkeypatch):
    set_path = _set_importado(tmp_path)

    def leitura_com_erro(caminho, report):
        session = read_session_do_set(caminho, report)
        report.error("ERRO_DE_LEITURA", "erro em MUSICA 1", {"track": "MUSICA 1"})
        return session

    monkeypatch.setattr("aafton.pipeline.read_session", leitura_com_erro)
    destino = tmp_path / "parcial.aaf"
    report = Report(strict=True)

    export_session(set_path, destino, report, tracks=[0])

    assert destino.exists()


def test_export_session_writes_only_the_selected_tracks(tmp_path):
    set_path = _set_importado(tmp_path)
    report = Report(strict=False)

    parcial = tmp_path / "parcial.aaf"
    export_session(set_path, parcial, report, tracks=[0])

    completo = tmp_path / "completo.aaf"
    export_session(set_path, completo, report)

    lido_parcial = read_aaf(parcial, report)
    lido_completo = read_aaf(completo, report)
    nomes_parcial = [track.name for track in lido_parcial.tracks]
    nomes_completo = [track.name for track in lido_completo.tracks]
    assert "DIALOGO 1" in nomes_parcial
    assert "MUSICA 1" not in nomes_parcial
    assert "DIALOGO 1" in nomes_completo
    assert "MUSICA 1" in nomes_completo


def test_export_session_com_uma_selecao_sem_track_nao_escreve_nada(tmp_path):
    set_path = _set_importado(tmp_path)
    report = Report(strict=False)
    destino = tmp_path / "vazio.aaf"

    with pytest.raises(ValueError):
        export_session(set_path, destino, report, tracks=[99])

    assert not destino.exists()
