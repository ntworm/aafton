"""Regressao C03: o WAV estereo do emparelhamento tem de caber no MAX_PATH.

Com a pasta de extracao funda o nome antigo passa de 259 unidades UTF-16, o
orcamento legado de caminho do Windows. O teste usa o emparelhamento de
verdade e mede o caminho que sai: ele diz que o nome cabe nesse orcamento, e
nao afirma nada sobre o Live, que nao foi retestado aqui.
"""

import dataclasses
import hashlib
import struct
import wave
from pathlib import Path

import pytest

from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.stereo import _try_pair, process_stereo_pairing
from aafton.stereo_pair_state import par_com_transacao
from aafton.timecode import TimecodeRate

MAX_PATH_UNITS = 259
SR = 48000
QUADROS = 64
AMOSTRA_L = [1000, -1000, 2000, -2000] * 16
AMOSTRA_R = [-2000, 2000, -1000, 1000] * 16


@pytest.fixture
def fontes(tmp_path):
    """Duas mono PCM16 com sons diferentes, como o Pro Tools entrega."""
    caminhos = [tmp_path / "fonte-L.wav", tmp_path / "fonte-R.wav"]
    for caminho, amostras in zip(caminhos, (AMOSTRA_L, AMOSTRA_R)):
        with wave.open(str(caminho), "wb") as handle:
            handle.setparams((1, 2, SR, 0, "NONE", "not compressed"))
            handle.writeframes(struct.pack(f"<{len(amostras)}h", *amostras))
    return caminhos


def _pasta(base: Path, alvo: int) -> Path:
    """Diretorio aninhado com caminho absoluto de alvo unidades UTF-16."""
    raiz = Path(base).resolve()
    resto = alvo - len(str(raiz).encode("utf-16-le")) // 2 - 1
    quantos = max(0, (resto - 5) // 21)
    resto -= 21 * quantos
    partes = ["n" * 20] * quantos + (["f" * resto] if resto > 0 else [])
    pasta = raiz.joinpath(*partes)
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _sessao(prefixo: str, fontes: list[Path]) -> SessionIR:
    """Duas tracks mono com o mesmo nome base, como o Pro Tools entrega."""
    midias = [
        MediaAsset(id=f"m{i}", filename=wav.name, resolved_path=str(wav))
        for i, wav in enumerate(fontes)
    ]
    tracks = []
    for i, rotulo in enumerate(("L", "R")):
        nome = f"{prefixo}.{rotulo}"
        clip = ClipIR(
            id=f"c{i}",
            track_id=f"t{i}",
            channel_index=i,
            event_index=0,
            name=nome,
            media_id=f"m{i}",
            timeline_start_samples=0,
            timeline_end_samples=QUADROS,
        )
        track = TrackIR(
            id=f"t{i}", source_index=i, name=nome, kind="audio", channel_count=1, clips=[clip]
        )
        tracks.append(track)
    return SessionIR(
        name="regressao-c03",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=tracks,
        media=midias,
    )


def _par(sessao: SessionIR, pasta: Path) -> Report:
    """Emparelha a sessao na pasta e devolve o relatorio."""
    report = Report()
    process_stereo_pairing(sessao, "pair", report, pasta)
    return report


def _estereo(sessao: SessionIR) -> Path:
    """O WAV estereo final, depois do emparelhamento."""
    asset = sessao.media_by_id(sessao.tracks[0].clips[0].media_id)
    return Path(asset.resolved_path)


def test_padrao_antigo_e_preservado_quando_o_caminho_cabe(tmp_path, fontes):
    """Com pasta curta o nome sai byte a byte como sempre: prefixo_digest.wav."""
    prefixo = "Take 03 - Stereo Mix"
    sessao = _sessao(prefixo, fontes)
    _par(sessao, tmp_path)
    caminho = _estereo(sessao)
    digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
    assert caminho.name == f"{prefixo}_{digest}.wav"


def test_pasta_funda_cabe_no_max_path_com_digest_inteiro(tmp_path, fontes):
    """Pasta de 180 unidades: o nome curto cabe e o digest continua inteiro."""
    pasta = _pasta(tmp_path, 180)
    prefixo = "nome bem longo de clip que antes estourava o limite do caminho"
    sessao = _sessao(prefixo, fontes)
    _par(sessao, pasta)
    caminho = _estereo(sessao)
    assert caminho.is_file()
    assert len(str(caminho).encode("utf-16-le")) // 2 <= MAX_PATH_UNITS
    digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
    assert digest in caminho.name
    with wave.open(str(caminho), "rb") as handle:
        assert handle.getnchannels() == 2
        quadros = handle.readframes(handle.getnframes())
    esperado = b"".join(struct.pack("<2h", e, d) for e, d in zip(AMOSTRA_L, AMOSTRA_R))
    assert quadros == esperado


def test_sessao_nova_reaproveita_o_wav_sem_reescrever(tmp_path, fontes):
    """A segunda sessao com as mesmas fontes nao pode reescrever o arquivo."""
    pasta = _pasta(tmp_path, 180)
    prefixo = "nome bem longo de clip que antes estourava o limite do caminho"
    sessao = _sessao(prefixo, fontes)
    _par(sessao, pasta)
    caminho = _estereo(sessao)
    antes = caminho.stat()
    sessao2 = _sessao(prefixo, fontes)
    _par(sessao2, pasta)
    assert _estereo(sessao2) == caminho
    depois = caminho.stat()
    assert (depois.st_ino, depois.st_mtime_ns) == (antes.st_ino, antes.st_mtime_ns)


def test_prefixo_unicode_nao_quebra_o_par_surrogado(tmp_path, fontes):
    """Com emoji (nonBMP) o prefixo e cortado sem partir o par substituto."""
    pasta = _pasta(tmp_path, 181)
    prefixo = "Ñandú \U0001f3b5 café"
    sessao = _sessao(prefixo, fontes)
    _par(sessao, pasta)
    caminho = _estereo(sessao)
    assert len(str(caminho).encode("utf-16-le")) // 2 <= MAX_PATH_UNITS
    assert caminho.name.startswith("Ñandú \U0001f3b5")
    assert caminho.name.encode("utf-16-le").decode("utf-16-le") == caminho.name
    digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
    assert digest in caminho.name


def test_raiz_impossivel_falha_com_erro_claro_e_fontes_intactas(tmp_path, fontes):
    """Nem o digest sozinho cabe: o emparelhamento recusa e explica."""
    antes = [wav.read_bytes() for wav in fontes]
    pasta = _pasta(tmp_path, 205)
    sessao = _sessao("qualquer", fontes)
    report = _par(sessao, pasta)
    assert len(sessao.tracks) == 2
    avisos = [i.message for i in report.issues if i.code == "STEREO_PATH_TOO_LONG"]
    assert avisos and "shorter" in avisos[0]
    assert [wav.read_bytes() for wav in fontes] == antes
    assert list(pasta.glob("*.wav")) == []


def test_par_recusado_nao_deixa_a_sessao_pela_metade(tmp_path, fontes):
    """Recusar o par nao muda a sessao: nem nome, nem canais, nem clip.

    O par recusado nao virou track estereo, entao a sessao nao pode dizer que
    virou: o nome e a contagem de canais mudariam no meio da conversao.
    """
    pasta = _pasta(tmp_path, 205)
    sessao = _sessao("qualquer", fontes)
    antes = dataclasses.asdict(sessao)
    report = _par(sessao, pasta)
    depois = dataclasses.asdict(sessao)
    assert [i.code for i in report.issues if i.code == "STEREO_PATH_TOO_LONG"]
    assert depois == antes


# --- Rollback do par recusado no fim -----------------------------------------


def _mono(caminho: Path, valor: int) -> Path:
    """Um mono PCM16 de 64 quadros com o valor dado."""
    amostras = [valor if i % 2 else -valor for i in range(QUADROS)]
    with wave.open(str(caminho), "wb") as handle:
        handle.setparams((1, 2, SR, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack(f"<{len(amostras)}h", *amostras))
    return caminho


@pytest.fixture
def quatro_fontes(tmp_path):
    """Quatro mono PCM16 com sons diferentes e o mesmo formato."""
    return [_mono(tmp_path / f"fonte-{indice}.wav", 1000 * (indice + 1)) for indice in range(4)]


def _clip_de_evento(clip_id, track_id, canal, evento, nome, media_id) -> ClipIR:
    """Um clip de evento, com a duracao de um quadro."""
    return ClipIR(
        id=clip_id,
        track_id=track_id,
        channel_index=canal,
        event_index=evento,
        name=nome,
        media_id=media_id,
        timeline_start_samples=evento * QUADROS,
        timeline_end_samples=(evento + 1) * QUADROS,
    )


def _sessao_de_pares(prefixo: str, pares: list[tuple[Path, Path]]) -> SessionIR:
    """Duas tracks L/R, com um par de fontes por evento."""
    midias = []
    clips_l = []
    clips_r = []
    for evento, (esquerda, direita) in enumerate(pares):
        id_l = f"m{len(midias)}"
        midias.append(
            MediaAsset(id=id_l, filename=esquerda.name, resolved_path=str(esquerda), channels=1)
        )
        id_r = f"m{len(midias)}"
        midias.append(
            MediaAsset(id=id_r, filename=direita.name, resolved_path=str(direita), channels=1)
        )
        clips_l.append(_clip_de_evento(f"cL{evento}", "tL", 0, evento, f"{prefixo}.L", id_l))
        clips_r.append(_clip_de_evento(f"cR{evento}", "tR", 1, evento, f"{prefixo}.R", id_r))
    tracks = [
        TrackIR(
            id="tL",
            source_index=0,
            name=f"{prefixo}.L",
            kind="audio",
            channel_count=1,
            clips=clips_l,
        ),
        TrackIR(
            id="tR",
            source_index=1,
            name=f"{prefixo}.R",
            kind="audio",
            channel_count=1,
            clips=clips_r,
        ),
    ]
    return SessionIR(
        name="regressao-c03",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=tracks,
        media=midias,
    )


def _sessao_combiner(pares: list[tuple[Path, Path]]) -> SessionIR:
    """Uma track com duas fontes mono por evento, como o combiner entrega."""
    midias = []
    clips = []
    for evento, (esquerda, direita) in enumerate(pares):
        id_l = f"m{len(midias)}"
        midias.append(
            MediaAsset(id=id_l, filename=esquerda.name, resolved_path=str(esquerda), channels=1)
        )
        id_r = f"m{len(midias)}"
        midias.append(
            MediaAsset(id=id_r, filename=direita.name, resolved_path=str(direita), channels=1)
        )
        clips.append(_clip_de_evento(f"c{evento}L", "t0", 0, evento, "som", id_l))
        clips.append(_clip_de_evento(f"c{evento}R", "t0", 1, evento, "som", id_r))
    track = TrackIR(id="t0", source_index=0, name="som", kind="audio", channel_count=2, clips=clips)
    return SessionIR(
        name="regressao-c03",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=midias,
    )


def _alvo_divergente(pasta: Path, sessao: SessionIR) -> Path:
    """Deixa na pasta o arquivo do par, mas com um conteudo diferente."""
    _par(sessao, pasta)
    saida = list(pasta.glob("som_*.wav"))
    assert len(saida) == 1
    saida[0].write_bytes(b"conteudo que nao e o audio")
    return saida[0]


def _par_com_transacao(pasta: Path, sessao: SessionIR, gerados: dict, report: Report):
    """Roda o par com transacao e devolve o que a chamada devolveu."""
    return par_com_transacao(
        _try_pair,
        (sessao, "som", sessao.tracks[0], sessao.tracks[1], report, pasta, gerados, False),
        sessao,
        sessao.tracks[0],
        gerados,
    )


def _fontes_intactas(fontes: list[Path]) -> None:
    """As fontes continuam no lugar, como estavam antes do par recusado."""
    for fonte in fontes:
        assert fonte.is_file()


def test_par_recusado_no_fim_nao_deixa_a_sessao_pela_metade(tmp_path, quatro_fontes):
    """O segundo evento falha: sessao, clips e cache voltam ao estado de antes.

    O primeiro evento ja gravou o WAV e criou o asset da midia; o segundo
    achou o arquivo com outro conteudo e recusou o par. O rollback acontece
    no lugar: os mesmos objetos, com o mesmo conteudo.
    """
    pasta = tmp_path / "estereo"
    pasta.mkdir()
    esquerda0, direita0, esquerda1, direita1 = quatro_fontes

    alvo = _alvo_divergente(pasta, _sessao_de_pares("som", [(esquerda1, direita1)]))

    sessao = _sessao_de_pares("som", [(esquerda0, direita0), (esquerda1, direita1)])
    clips = list(sessao.tracks[0].clips)
    antes = dataclasses.asdict(sessao)
    gerados: dict = {}
    report = Report()

    sucesso, escritos = _par_com_transacao(pasta, sessao, gerados, report)

    assert not sucesso
    assert [i.code for i in report.issues if i.code == "STEREO_INTERLEAVE_FAILED"]
    assert dataclasses.asdict(sessao) == antes
    assert sessao.tracks[0].clips[0] is clips[0]
    assert [clip.media_id for clip in clips] == ["m0", "m2"]
    # O cache dos arquivos gerados nao guarda o par recusado.
    assert gerados == {}
    # O arquivo divergente e as fontes continuam no lugar.
    assert alvo.read_bytes() == b"conteudo que nao e o audio"
    _fontes_intactas(quatro_fontes)


def test_o_emparelhamento_l_r_recusado_nao_deixa_a_sessao_pela_metade(tmp_path, quatro_fontes):
    """A entrada de par L/R passa pelo wrapper: a sessao fica como estava."""
    pasta = tmp_path / "estereo"
    pasta.mkdir()
    esquerda0, direita0, esquerda1, direita1 = quatro_fontes

    _alvo_divergente(pasta, _sessao_de_pares("som", [(esquerda1, direita1)]))

    sessao = _sessao_de_pares("som", [(esquerda0, direita0), (esquerda1, direita1)])
    antes = dataclasses.asdict(sessao)
    report = _par(sessao, pasta)

    assert [i.code for i in report.issues if i.code == "STEREO_INTERLEAVE_FAILED"]
    assert dataclasses.asdict(sessao) == antes
    assert len(sessao.tracks) == 2


def test_o_combiner_recusado_nao_deixa_a_sessao_pela_metade(tmp_path, quatro_fontes):
    """A entrada do combiner passa pelo wrapper: a sessao fica como estava."""
    pasta = tmp_path / "estereo"
    pasta.mkdir()
    esquerda0, direita0, esquerda1, direita1 = quatro_fontes

    _alvo_divergente(pasta, _sessao_combiner([(esquerda1, direita1)]))

    sessao = _sessao_combiner([(esquerda0, direita0), (esquerda1, direita1)])
    antes = dataclasses.asdict(sessao)
    report = _par(sessao, pasta)

    assert [i.code for i in report.issues if i.code == "STEREO_INTERLEAVE_FAILED"]
    assert dataclasses.asdict(sessao) == antes
    assert len(sessao.tracks) == 1
