"""E4 do R07.6: propriedades do conversor que valem para toda fixture, sem humano.

Cada fixture versionada passa pelo caminho completo da CLI (``aafton import``),
com portadoras de silencio no lugar do corpus. As propriedades sao as que o
pipeline inteiro tem de manter; um teste que falha diz qual garantia quebrou e
onde olhar primeiro.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.als_compare import comparar
from aafton.als_compare_campos import CAMINHO_CLIPS
from aafton.als_schema import SCHEMA_BUILD, schema_do_build
from aafton.als_xml import load_als
from aafton.live_compat import FIXTURES, codigos, converter_fixture, importar
from aafton.report import Report
from aafton.sidecar import read_anchor
from aafton.verify import verify_conversion
from tests.aaf_factory import (
    build_protools_synthetic_aaf,
    build_resolve_synthetic_aaf,
    wav_bytes,
)
from tests.support.paths import FIXTURES_AAF, is_private_fixture, private_fixture

NOMES = [item.nome for item in FIXTURES]
TEMPLATE = Path("templates/live12.als")
RAIZ_DOCUMENTO = "Ableton"
COBERTURA = re.compile(r"Coverage: (\d+) of (\d+) audio events compared")
MAPEAMENTO = re.compile(r"Mapping: (\d+) of (\d+) slots mapped")


@pytest.fixture(scope="module")
def convertidas(tmp_path_factory):
    """Cada fixture convertida uma vez pelo caminho completo da CLI."""
    saida = {}
    for nome in NOMES:
        if is_private_fixture(nome) and not (FIXTURES_AAF / f"{nome}.aaf").is_file():
            continue
        pasta = tmp_path_factory.mktemp(nome)
        conversao = converter_fixture(nome, pasta)
        assert conversao.codigo == 0, (
            f"{nome}: a importacao estrita falhou; leia o relatorio antes de qualquer "
            f"propriedade:\n{conversao.relatorio[-1500:]}"
        )
        saida[nome] = conversao
    return saida


@pytest.fixture(scope="module")
def verificadas(convertidas):
    return {nome: verify_conversion(c.aaf, c.als) for nome, c in convertidas.items()}


def _clips_de_audio(aaf: Path) -> list:
    sessao = read_aaf(aaf, Report(strict=False))
    return [clip for track in sessao.tracks if not track.is_video for clip in track.clips]


# 1. Idempotencia do re-export ------------------------------------------------


def _wav(pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "fonte.wav"
    caminho.write_bytes(wav_bytes(48000 * 70))
    return caminho


CASOS_SINTETICOS = {
    "protools": lambda aaf, wav: build_protools_synthetic_aaf(
        aaf, wav, track_name="DX 1", rendered_fades=False
    ),
    "resolve": lambda aaf, wav: build_resolve_synthetic_aaf(aaf, wav, include_video_slot=False),
}


def _ida_e_volta(aaf: Path, midia: Path, pasta: Path) -> tuple[Path, Path, str]:
    from aafton.cli import main

    primeira = importar(aaf, pasta / "um" / "primeiro.als", midia)
    assert primeira.codigo == 0, primeira.relatorio[-1500:]
    volta = pasta / "volta.aaf"
    assert main(["export", str(primeira.als), str(volta)]) == 0, "o export estrito recusou"
    segunda = importar(volta, pasta / "dois" / "segundo.als", midia)
    assert segunda.codigo == 0, segunda.relatorio[-1500:]
    return primeira.als, segunda.als, segunda.relatorio


@pytest.mark.parametrize("caso", sorted(CASOS_SINTETICOS))
def test_reexport_e_idempotente_no_als(caso, tmp_path, capsys):
    """AAF -> ALS -> AAF -> ALS: o segundo .als igual ao primeiro, campo a campo."""
    wav = _wav(tmp_path / "midia")
    aaf = tmp_path / "origem.aaf"
    CASOS_SINTETICOS[caso](aaf, wav)
    primeiro, segundo, _ = _ida_e_volta(aaf, wav.parent, tmp_path)
    diferencas = comparar(primeiro, segundo)
    assert diferencas == [], (
        f"{caso}: o .als depois de passar pelo AAF do aafton mudou em "
        f"{len(diferencas)} campos. Se a perda e conhecida, declare-a na exportacao "
        f"(issue no relatorio) antes de aceitar; primeiras: {diferencas[:4]}"
    )


def _codigos_de_erro(relatorio: str) -> list[str]:
    """Codigos de erro da tabela do relatorio markdown impresso pela CLI."""
    codigos = []
    for linha in relatorio.splitlines():
        if linha.startswith("| error |"):
            codigos.append(linha.split("`")[1])
    return codigos


def test_reexport_de_fixture_real_com_automacao_de_ganho(tmp_path, capsys):
    """Fixture real com automacao de ganho: o export tem de fechar o arquivo.

    D1 do R07.6: o keyframe de ganho acima de 1.0 estourava o Rational32 e o
    export morria com struct.error.
    """
    conversao = converter_fixture("TESTE_DF_2997", tmp_path, cheio=True)
    from aafton.cli import main

    capsys.readouterr()
    volta = tmp_path / "volta.aaf"
    codigo = main(["export", str(conversao.als), str(volta), "--permissive"])
    erros = _codigos_de_erro(capsys.readouterr().out)
    assert set(erros) <= {"WRITE_FADE_UNSUPPORTED"}, (
        f"o export falhou por um motivo que nao e a perda de fade ja conhecida: {erros}"
    )
    esperado = 1 if erros else 0
    assert codigo == esperado, (
        f"o export devolveu {codigo} com {len(erros)} erros declarados no relatorio"
    )
    assert volta.exists() and volta.stat().st_size > 0, "o export nao gravou o AAF"
    sessao = read_aaf(volta, Report(strict=False))
    assert sessao.tracks, "o AAF exportado nao tem trilha"
    with aaf2.open(str(volta)) as arquivo:
        mobs = list(arquivo.content.mobs)
        composicao = [mob for mob in mobs if type(mob).__name__ == "CompositionMob"]
    assert mobs, "o AAF exportado nao tem mob de conteudo"
    assert composicao, "o AAF exportado nao tem mob de composicao"
    ganhos = [valor for trilha in sessao.tracks for _amostra, valor in trilha.gain_points]
    acima = [valor for valor in ganhos if valor > 1.0]
    assert acima, "a fixture nao tem ganho acima de 1.0 para provar o D1"
    assert any(abs(valor - 1.244514610618353) <= 1e-9 for valor in acima), (
        f"o ganho acima de 1.0 nao voltou do AAF: {acima}"
    )


# 2. Conservacao sample-exata --------------------------------------------------


@pytest.mark.parametrize("nome", NOMES)
def test_todo_sourceclip_chega_ao_als_com_desvio_zero(nome, convertidas, verificadas):
    private_fixture(f"{nome}.aaf")
    resultado = verificadas[nome]
    esperados = len(_clips_de_audio(convertidas[nome].aaf))
    assert resultado.audio_expected == esperados, (
        f"{nome}: o verify espera {resultado.audio_expected} clips e o AAF tem "
        f"{esperados}; o indice do verify perdeu ou inventou clips"
    )
    tortos = [
        (linha.track, linha.clip, linha.delta_samples, linha.delta_end_samples)
        for linha in resultado.rows
        if linha.is_audio and (linha.delta_samples != 0 or linha.delta_end_samples != 0)
    ]
    assert not tortos, (
        f"{nome}: clips fora da posicao sample-exata (inicio, fim): {tortos[:5]}. "
        "Confira a conversao de batida antes do verify."
    )
    assert resultado.ok


# 3. Nenhuma perda silenciosa --------------------------------------------------


def test_perda_na_exportacao_nao_convive_com_verify_aprovado(tmp_path, capsys):
    """Crossfade que o AAF nao representa: o export permissivo pula clips e o verify reprova."""
    from aafton.cli import main

    wav = _wav(tmp_path / "midia")
    aaf = tmp_path / "origem.aaf"
    build_protools_synthetic_aaf(aaf, wav, track_name="DX 1", rendered_fades=True)
    primeira = importar(aaf, tmp_path / "um" / "primeiro.als", wav.parent)
    assert primeira.codigo == 0, primeira.relatorio[-1500:]
    capsys.readouterr()
    volta = tmp_path / "volta.aaf"
    assert main(["export", str(primeira.als), str(volta), "--permissive"]) == 1
    relatorio = capsys.readouterr().out
    assert "WRITE_CLIP_SKIPPED" in codigos(relatorio), "o caso precisa de uma perda declarada"
    resultado = verify_conversion(volta, primeira.als)
    assert not resultado.ok, (
        "o export pulou clips e o verify aprovou: perda declarada convivendo com "
        f"cobertura total ({resultado.coverage_msg})"
    )
    assert resultado.audio_extra > 0


@pytest.mark.parametrize("nome", NOMES)
def test_clip_apagado_do_als_derruba_a_cobertura(nome, convertidas, tmp_path):
    """O verify ja aprovou subconjunto de clips como cobertura total (risks.md)."""
    private_fixture(f"{nome}.aaf")
    conversao = convertidas[nome]
    raiz = load_als(conversao.als)
    apagado = False
    for eventos in raiz.iter("Events"):
        clips = eventos.findall("AudioClip")
        if clips:
            eventos.remove(clips[0])
            apagado = True
            break
    assert apagado, f"{nome}: a fixture nao tem clip de audio para apagar"
    mutilado = tmp_path / conversao.als.name
    mutilado.write_bytes(gzip.compress(ET.tostring(raiz, encoding="utf-8")))
    sidecar = conversao.als.with_name(conversao.als.stem + ".aafton.json")
    mutilado.with_name(mutilado.stem + ".aafton.json").write_bytes(sidecar.read_bytes())
    resultado = verify_conversion(conversao.aaf, mutilado)
    assert not resultado.ok, f"{nome}: o verify aprovou um .als sem um dos clips"
    assert resultado.audio_missing > 0
    assert resultado.audio_compared < resultado.audio_expected
    achado = COBERTURA.search(resultado.coverage_msg)
    assert achado, f"{nome}: verify sem linha de Coverage: {resultado.coverage_msg!r}"
    comparados, esperados = int(achado.group(1)), int(achado.group(2))
    assert comparados == resultado.audio_compared
    assert esperados == resultado.audio_expected
    assert comparados < esperados, (
        f"{nome}: a cobertura ainda declara cobertura total depois do clip apagado: "
        f"{resultado.coverage_msg!r}"
    )


# 4. Cobertura confere com a contagem -------------------------------------------


def _slots_de_audio(aaf: Path) -> int:
    """Slots de audio do AAF (indices de origem nao negativos, sem video)."""
    sessao = read_aaf(aaf, Report(strict=False))
    return len(
        {
            indice
            for track in sessao.tracks
            if not track.is_video
            for indice in track.original_source_indices
            if indice >= 0
        }
    )


@pytest.mark.parametrize("nome", NOMES)
def test_a_linha_de_cobertura_bate_com_os_eventos(nome, convertidas, verificadas):
    private_fixture(f"{nome}.aaf")
    resultado = verificadas[nome]
    achado = COBERTURA.search(resultado.coverage_msg)
    assert achado, f"{nome}: verify sem linha de Coverage: {resultado.coverage_msg!r}"
    comparados, total = int(achado.group(1)), int(achado.group(2))
    clips = len(_clips_de_audio(convertidas[nome].aaf))
    assert total == clips == resultado.audio_expected, (
        f"{nome}: a cobertura declara {total} eventos e o AAF tem {clips} clips de audio"
    )
    assert comparados == resultado.audio_compared == total
    mapa = MAPEAMENTO.search(resultado.coverage_msg)
    assert mapa, f"{nome}: verify sem linha de Mapping: {resultado.coverage_msg!r}"
    mapeados, slots = int(mapa.group(1)), int(mapa.group(2))
    slots_reais = _slots_de_audio(convertidas[nome].aaf)
    assert slots == slots_reais, (
        f"{nome}: o mapeamento declara {slots} slots e o AAF tem {slots_reais} slots de audio"
    )
    assert mapeados == slots


# 5. Unidade coerente ----------------------------------------------------------


def _valor(no: ET.Element, caminho: str) -> Fraction:
    return Fraction(no.find(caminho).get("Value"))


def _amostras(batidas: Fraction, ancora) -> Fraction:
    return batidas * 60 * ancora.sample_rate / Fraction(str(ancora.bpm))


@pytest.mark.parametrize("nome", NOMES)
def test_todo_tempo_do_als_e_batida_com_a_mesma_origem(nome, convertidas):
    """Posicao em batida com a origem do sidecar volta ao sample do AAF, sem frame no meio."""
    private_fixture(f"{nome}.aaf")
    conversao = convertidas[nome]
    ancora = read_anchor(conversao.als)
    raiz = load_als(conversao.als)
    inicios_als = []
    for clip in raiz.iterfind(f"LiveSet/Tracks/AudioTrack/{CAMINHO_CLIPS}"):
        inicio = _valor(clip, "CurrentStart")
        assert Fraction(clip.get("Time")) == inicio, "o atributo Time descolou de CurrentStart"
        # O arranjo usa o relogio de clip do Live (perfil nativo, R03) e a fonte nao:
        # as duas duracoes so precisam cair na mesma amostra. Os marcadores de
        # loop do clip sem warp sao segundos do arquivo.
        no_arranjo = _amostras(_valor(clip, "CurrentEnd") - inicio, ancora)
        loop = _valor(clip, "Loop/LoopEnd") - _valor(clip, "Loop/LoopStart")
        na_fonte = loop * ancora.sample_rate
        assert round(no_arranjo) == round(na_fonte), (
            f"{nome}: a duracao no arranjo ({float(no_arranjo)} amostras) e na fonte "
            f"({float(na_fonte)}) nao caem na mesma amostra: unidades diferentes"
        )
        entra = clip.find("Fades/CrossfadeInState")
        no_crossfade = entra is not None and entra.get("Value") == "1"
        inicio_samples = round(ancora.live_origin_samples + _amostras(inicio, ancora))
        inicios_als.append((inicio_samples, no_crossfade))
    clips_aaf = _clips_de_audio(conversao.aaf)
    inicios_aaf = {clip.timeline_start_samples for clip in clips_aaf}
    # Na forma nativa do crossfade o clip que entra comeca no fim do que sai.
    fins_aaf = {clip.timeline_end_samples for clip in clips_aaf}
    fora = sorted(
        inicio
        for inicio, no_crossfade in inicios_als
        if inicio not in inicios_aaf and not (no_crossfade and inicio in fins_aaf)
    )
    assert not fora, (
        f"{nome}: inicios do .als que nao sao inicio de clip no AAF: {fora[:5]} "
        "(frame tratado como sample, ou origem diferente da do sidecar)"
    )


# 6. Schema --------------------------------------------------------------------


@pytest.fixture(scope="module")
def pares_do_live():
    """Pares (pai, filho) que o proprio Live gravou no template."""
    return {(pai.tag, filho.tag) for pai in load_als(TEMPLATE).iter() for filho in pai}


@pytest.mark.parametrize("nome", NOMES)
def test_todo_elemento_gerado_existe_no_schema_do_build(nome, convertidas, pares_do_live):
    private_fixture(f"{nome}.aaf")
    raiz = load_als(convertidas[nome].als)
    assert raiz.get("MinorVersion") == SCHEMA_BUILD, (
        f"{nome}: .als de outro build ({raiz.get('MinorVersion')}); o schema versionado e "
        f"{SCHEMA_BUILD}"
    )
    schema = schema_do_build()
    fora = sorted(
        {n.tag for n in raiz.iter() if n.tag != RAIZ_DOCUMENTO and not schema.tem_elemento(n.tag)}
    )
    assert not fora, f"{nome}: nomes fora do schema do Live: {fora}"
    pais_errados = sorted(
        {(pai.tag, filho.tag) for pai in raiz.iter() for filho in pai} - pares_do_live
    )
    assert not pais_errados, (
        f"{nome}: elementos sob um pai que o Live nunca grava: {pais_errados[:5]}. "
        "Tag conhecida no lugar errado passa no vocabulario e quebra no Live."
    )
