"""O comando apply: as pecas no AAF escrito e as recusas que nao deixam saida.

O fixture phaseslip traz o AAF ligado e o JSON de edicoes do exemplo. O AAF
embutido e a midia da sonda ficam na pasta dela: sem a variavel
AAFTON_SONDA_DIR os testes que dependem dela pulam.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import wave
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_inspect_media import embedded_media_ids
from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.cli import run_cli
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "phaseslip"
LIGADO = FIXTURES / "SONDA_P0_ligado.aaf"
EDICOES = FIXTURES / "edicoes-exemplo.json"

SEGUNDOS = 48000


def _cli(argv: list[str]) -> tuple[int, str, str]:
    """Roda a linha de comando no processo e devolve o codigo e as duas saidas."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_cli(argv)
    return code, out.getvalue(), err.getvalue()


def _sonda() -> Path:
    """A pasta da sonda, com o AAF embutido e a midia ligada."""
    bruto = os.environ.get("AAFTON_SONDA_DIR")
    if not bruto:
        pytest.skip("AAFTON_SONDA_DIR nao esta no ambiente: a sonda traz o AAF embutido e a midia")
    pasta = Path(bruto)
    if not (pasta / "SONDA_P0_embutido.aaf").is_file():
        pytest.skip(f"AAFTON_SONDA_DIR nao tem o AAF embutido: {pasta}")
    return pasta


def _codigos(resultado: dict) -> list[str]:
    """Os codigos de erro que o apply devolveu."""
    return [erro["code"] for erro in resultado["errors"]]


def _wav(caminho: Path, canais: int, amostras: int) -> Path:
    """Grava um WAV PCM silencioso com a duracao informada."""
    with wave.open(str(caminho), "wb") as handle:
        handle.setparams((canais, 2, SEGUNDOS, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * canais * amostras)
    return caminho


def _aaf_de_teste(
    destino: Path,
    clips: list[tuple[str, int, int, int]],
    *,
    canais: int = 1,
    canais_media: int = 1,
    nome: str | None = "Sintetico",
) -> Path:
    """Escreve um AAF com um WAV e os clips pedidos."""
    wav = _wav(destino.parent / f"{destino.stem}.wav", canais_media, SEGUNDOS)
    asset = MediaAsset(
        id="m0",
        filename=wav.name,
        channels=canais_media,
        sample_rate=SEGUNDOS,
        duration_samples=SEGUNDOS,
        status="resolved",
        declared_location=str(wav),
        resolved_path=str(wav),
    )
    ircs = [
        ClipIR(
            id=clip_id,
            track_id="t0",
            channel_index=0,
            event_index=indice,
            name=clip_id,
            media_id="m0",
            timeline_start_samples=start,
            timeline_end_samples=end,
            source_offset_samples=offset,
        )
        for indice, (clip_id, start, end, offset) in enumerate(clips)
    ]
    track = TrackIR(
        id="t0",
        source_index=0,
        name="Track 0",
        kind="audio",
        channel_count=canais,
        clips=ircs,
    )
    sessao = SessionIR(
        name="Sintetico",
        sample_rate=SEGUNDOS,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )
    write_aaf(sessao, destino, Report(strict=False), media="link", composition_name=nome)
    return destino


def _ids_dos_clips(caminho: Path) -> list[str]:
    """Os ids de clip do AAF, como o inspect do PhaseSlip os ve."""
    sessao = read_aaf(caminho, Report(strict=False))
    return [clip.id for clip in sessao.all_clips()]


def _track_por_nome(sessao: SessionIR, nome: str) -> TrackIR:
    """A track do AAF com o nome pedido."""
    achados = [track for track in sessao.tracks if track.name == nome]
    assert len(achados) == 1, f"a track {nome!r} nao esta no AAF: {[t.name for t in sessao.tracks]}"
    return achados[0]


def _pecas(track: TrackIR) -> list[tuple[int, int, int]]:
    """As pecas da track, em ordem de timeline."""
    return sorted(
        (clip.timeline_start_samples, clip.timeline_end_samples, clip.source_offset_samples)
        for clip in track.clips
    )


def _composition_mob(caminho: Path) -> str | None:
    """O nome da CompositionMob escrita no AAF."""
    with aaf2.open(str(caminho), "r") as arquivo:
        for mob in arquivo.content.mobs:
            if type(mob).__name__ == "CompositionMob":
                return mob.name
    return None


def _edicoes(destino: Path, clips: dict) -> Path:
    """Grava o JSON de edicoes das pecas pedidas."""
    destino.write_text(json.dumps({"version": 1, "clips": clips}), encoding="utf-8")
    return destino


def test_apply_no_ligado_escreve_as_pecas_pedidas(tmp_path: Path) -> None:
    """O apply do AAF ligado escreve as pecas e mantem a midia fora do arquivo.

    As pecas do exemplo sao a track t3 (SONDA_LAV1.1), partida em duas, e a
    track t4 (SONDA_LAV2_atraso37.1), encurtada em 37 samples.
    """
    sonda = _sonda()
    saida = tmp_path / "ligado_phaseslip.aaf"
    code, out, err = _cli(["apply", str(sonda / "SONDA_P0_ligado.aaf"), str(EDICOES), str(saida)])
    assert code == 0, err
    resultado = json.loads(out)
    assert resultado["ok"] is True
    assert resultado["mode"] == "link"
    assert resultado["clips_changed"] == 2
    assert resultado["errors"] == []
    assert saida.is_file()

    relida = read_aaf(saida, Report(strict=False))
    assert relida.name == "SONDA_P0"
    assert _pecas(_track_por_nome(relida, "SONDA_LAV1.1")) == [
        (0, 700700, 10),
        (700700, 1401375, 700725),
    ]
    assert _pecas(_track_por_nome(relida, "SONDA_LAV2_atraso37.1")) == [(0, 1401363, 37)]
    assert _composition_mob(saida) == "SONDA_P0"
    usados = {clip.media_id for clip in relida.all_clips() if clip.media_id}
    assert embedded_media_ids(saida, relida) & usados == set()


def test_apply_no_embutido_escreve_as_pecas_pedidas(tmp_path: Path) -> None:
    """O apply do AAF embutido escreve as pecas e mantem a midia dentro do arquivo.

    As mesmas pecas do exemplo, na track t3 e na track t4 do AAF embutido.
    """
    sonda = _sonda()
    saida = tmp_path / "embutido_phaseslip.aaf"
    code, out, err = _cli(["apply", str(sonda / "SONDA_P0_embutido.aaf"), str(EDICOES), str(saida)])
    assert code == 0, err
    resultado = json.loads(out)
    assert resultado["ok"] is True
    assert resultado["mode"] == "embed"
    assert resultado["clips_changed"] == 2
    assert resultado["errors"] == []
    assert saida.is_file()

    relida = read_aaf(saida, Report(strict=False))
    assert relida.name == "SONDA_P0"
    assert _pecas(_track_por_nome(relida, "SONDA_LAV1.1")) == [
        (0, 700700, 10),
        (700700, 1401375, 700725),
    ]
    assert _pecas(_track_por_nome(relida, "SONDA_LAV2_atraso37.1")) == [(0, 1401363, 37)]
    assert _composition_mob(saida) == "SONDA_P0"
    usados = {clip.media_id for clip in relida.all_clips() if clip.media_id}
    assert embedded_media_ids(saida, relida) & usados == usados


def test_apply_recusa_o_clip_desconhecido(tmp_path: Path) -> None:
    """Um id de clip que nao existe no AAF sai com 1 e nao deixa arquivo."""
    saida = tmp_path / "saida.aaf"
    edicoes = _edicoes(
        tmp_path / "edicoes.json", {"naoexiste": [{"start": 0, "end": 10, "offset": 0}]}
    )
    code, out, err = _cli(["apply", str(LIGADO), str(edicoes), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert resultado["ok"] is False
    assert "APPLY_UNKNOWN_CLIP" in _codigos(resultado)
    assert not saida.exists()


def test_apply_recusa_a_peca_alem_da_midia(tmp_path: Path) -> None:
    """A peca que passa do fim do arquivo sai com 1 e nao deixa arquivo."""
    entrada = _aaf_de_teste(tmp_path / "entrada.aaf", [("c0", 0, SEGUNDOS, 0)])
    clip_id = _ids_dos_clips(entrada)[0]
    saida = tmp_path / "saida.aaf"
    edicoes = _edicoes(
        tmp_path / "edicoes.json", {clip_id: [{"start": 0, "end": SEGUNDOS + 1, "offset": 0}]}
    )
    code, out, err = _cli(["apply", str(entrada), str(edicoes), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert "APPLY_OUTSIDE_MEDIA" in _codigos(resultado)
    assert not saida.exists()


def test_apply_recusa_a_track_que_nao_e_mono(tmp_path: Path) -> None:
    """O clip de uma track stereo sai com 1 e nao deixa arquivo."""
    entrada = _aaf_de_teste(
        tmp_path / "entrada.aaf", [("c0", 0, SEGUNDOS, 0)], canais=2, canais_media=2
    )
    clip_id = _ids_dos_clips(entrada)[0]
    saida = tmp_path / "saida.aaf"
    edicoes = _edicoes(
        tmp_path / "edicoes.json", {clip_id: [{"start": 0, "end": 100, "offset": 0}]}
    )
    code, out, err = _cli(["apply", str(entrada), str(edicoes), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert "APPLY_TRACK_NOT_MONO" in _codigos(resultado)
    assert not saida.exists()


def test_apply_recusa_a_saida_igual_a_entrada() -> None:
    """A saida nao pode ser o proprio AAF de entrada, que fica intacto."""
    antes = LIGADO.read_bytes()
    code, out, err = _cli(["apply", str(LIGADO), str(EDICOES), str(LIGADO)])
    assert code == 1
    resultado = json.loads(out)
    assert "APPLY_SAME_FILE" in _codigos(resultado)
    assert LIGADO.read_bytes() == antes


def test_apply_recusa_o_json_quebrado(tmp_path: Path) -> None:
    """Um JSON de edicoes que nao e o do exemplo sai com 1 e nao deixa arquivo."""
    saida = tmp_path / "saida.aaf"
    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text("nao json", encoding="utf-8")
    code, out, err = _cli(["apply", str(LIGADO), str(quebrado), str(saida)])
    assert code == 1
    assert "APPLY_BAD_EDITS" in _codigos(json.loads(out))
    assert not saida.exists()

    sem_versao = tmp_path / "sem_versao.json"
    sem_versao.write_text(json.dumps({"version": 2, "clips": {}}), encoding="utf-8")
    code, out, err = _cli(["apply", str(LIGADO), str(sem_versao), str(saida)])
    assert code == 1
    assert "APPLY_BAD_EDITS" in _codigos(json.loads(out))
    assert not saida.exists()


def test_apply_recusa_as_pecas_sobrepostas(tmp_path: Path) -> None:
    """As pecas de um clip que se cruzam saem com 1 e nao deixam arquivo."""
    saida = tmp_path / "saida.aaf"
    edicoes = _edicoes(
        tmp_path / "edicoes.json",
        {
            "t0c0_ch0": [
                {"start": 0, "end": 100, "offset": 0},
                {"start": 50, "end": 200, "offset": 50},
            ]
        },
    )
    code, out, err = _cli(["apply", str(LIGADO), str(edicoes), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert "APPLY_BAD_EDITS" in _codigos(resultado)
    assert not saida.exists()


def test_apply_recusa_a_sobreposicao_criada(tmp_path: Path) -> None:
    """A edicao que faz dois clips da mesma track se cruzarem sai com 1."""
    entrada = _aaf_de_teste(tmp_path / "entrada.aaf", [("c0", 0, 1000, 0), ("c1", 2000, 3000, 0)])
    primeiro, segundo = _ids_dos_clips(entrada)
    saida = tmp_path / "saida.aaf"
    edicoes = _edicoes(
        tmp_path / "edicoes.json", {segundo: [{"start": 500, "end": 3000, "offset": 0}]}
    )
    code, out, err = _cli(["apply", str(entrada), str(edicoes), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert "APPLY_OVERLAP_CREATED" in _codigos(resultado)
    assert not saida.exists()
    assert primeiro != segundo


def test_export_sem_nome_de_composicao_mantem_o_nome_do_live(tmp_path: Path) -> None:
    """O export do Live nao passa nome: a CompositionMob continua a de sempre."""
    destino = _aaf_de_teste(tmp_path / "live.aaf", [("c0", 0, 24000, 0)], nome=None)
    assert _composition_mob(destino) == "Exported from Ableton"


def test_apply_de_entrada_inexistente_sai_1_com_json(tmp_path: Path) -> None:
    """Entrada que nao existe sai 1 com o JSON do contrato, sem saida."""
    saida = tmp_path / "saida.aaf"
    code, out, _err = _cli(["apply", str(tmp_path / "nao.aaf"), str(EDICOES), str(saida)])
    assert code == 1
    resultado = json.loads(out)
    assert _codigos(resultado) == ["APPLY_INPUT_MISSING"]
    assert resultado["mode"] is None
    assert not saida.exists()


def test_apply_recusado_depois_da_escrita_apaga_a_pasta_de_midia(tmp_path: Path) -> None:
    """A pasta de midia que o apply criou sai junto com o AAF recusado."""
    from aafton.aaf_apply import _discard_output

    saida = tmp_path / "saida.aaf"
    saida.write_bytes(b"x")
    pasta = tmp_path / "saida Media"
    pasta.mkdir()
    (pasta / "m.wav").write_bytes(b"x")
    _discard_output(saida, had_aux_dir=False)
    assert not saida.exists()
    assert not pasta.exists()


def test_apply_recusado_preserva_a_pasta_de_midia_que_ja_existia(tmp_path: Path) -> None:
    """Uma pasta de midia de antes do apply nao e apagada."""
    from aafton.aaf_apply import _discard_output

    saida = tmp_path / "saida.aaf"
    pasta = tmp_path / "saida Media"
    pasta.mkdir()
    _discard_output(saida, had_aux_dir=True)
    assert pasta.is_dir()


def test_apply_recusado_devolve_a_saida_que_ja_existia(tmp_path: Path, monkeypatch) -> None:
    """Uma saida de antes volta intacta quando a escrita do apply falha."""
    import aafton.aaf_apply as modulo

    def explode(*_args, **_kwargs):
        raise RuntimeError("disco cheio")

    entrada = _aaf_de_teste(tmp_path / "entrada.aaf", [("c0", 0, 24000, 0)])
    clip_id = _ids_dos_clips(entrada)[0]
    saida = tmp_path / "saida.aaf"
    saida.write_bytes(b"entrega anterior")
    edicoes = tmp_path / "e.json"
    pedido = {"version": 1, "clips": {clip_id: [{"start": 0, "end": 12000, "offset": 0}]}}
    edicoes.write_text(json.dumps(pedido), encoding="utf-8")
    monkeypatch.setattr(modulo, "write_aaf", explode)
    code, _out, _err = _cli(["apply", str(entrada), str(edicoes), str(saida)])
    assert code == 1
    assert saida.read_bytes() == b"entrega anterior"
    assert not (tmp_path / "saida.aaf.aafton-backup").exists()


def test_apply_de_aaf_quebrado_da_um_erro_so(tmp_path: Path) -> None:
    """O AAF que nao abre sai com AAF_UNREADABLE, sem um segundo erro do apply."""
    quebrado = tmp_path / "quebrado.aaf"
    quebrado.write_bytes(b"nao e um AAF")
    code, out, _err = _cli(["apply", str(quebrado), str(EDICOES), str(tmp_path / "s.aaf")])
    assert code == 1
    assert _codigos(json.loads(out)) == ["AAF_UNREADABLE"]


def test_apply_com_excecao_na_escrita_responde_com_json(tmp_path: Path, monkeypatch) -> None:
    """Uma excecao do writer vira APPLY_WRITE_ERROR no JSON, sem traceback e sem saida."""
    import aafton.aaf_apply as modulo

    def explode(*_args, **_kwargs):
        raise RuntimeError("disco cheio")

    entrada = _aaf_de_teste(tmp_path / "entrada.aaf", [("c0", 0, 24000, 0)])
    clip_id = _ids_dos_clips(entrada)[0]
    edicoes = tmp_path / "e.json"
    pedido = {"version": 1, "clips": {clip_id: [{"start": 0, "end": 12000, "offset": 0}]}}
    edicoes.write_text(json.dumps(pedido), encoding="utf-8")
    monkeypatch.setattr(modulo, "write_aaf", explode)
    saida = tmp_path / "saida.aaf"
    code, out, _err = _cli(["apply", str(entrada), str(edicoes), str(saida)])
    assert code == 1
    assert _codigos(json.loads(out)) == ["APPLY_WRITE_ERROR"]
    assert not saida.exists()
