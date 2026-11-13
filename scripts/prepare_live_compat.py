"""Pacote do oraculo diferencial do Live (R07.6, E2) e a medicao da volta.

``preparar`` gera um caso por pasta: o .als que o operador abre no Live
(``entrada.als``) e a pergunta. O resto (o AAF, as midias-portadoras, o
sidecar e o relatorio) fica em ``_maquina/``. O operador responde a pergunta
e salva como ``saida_do_live.als`` na mesma pasta: e o unico gesto humano. O
Save As padrao do Live cria a pasta ``saida_do_live Project``: a volta
aninhada nela tambem vale.

``medir`` compara cada saida presente com a entrada, calcula a perfeicao
antes e depois do Live e grava ``medicao.json`` e ``medicao.md``. Caso sem
saida fica pendente; nada e medido no lugar dele.

Garantias de evidencia: o preflight de ``preparar`` e somente de leitura e
recusa um pacote que ja tem devolucao; o manifesto amarra por hash a
entrada, o .als gerado, o sidecar, o AAF, o relatorio e as midias do .als
(``live_compat_proveniencia``); e ``medir`` confere a proveniencia de todos
os casos antes de escrever. Os C01-C04 usam copia da fixture, com midia local.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from aafton.aaf_write import write_aaf  # noqa: E402
from aafton.als_compare import comparar, contagem, identidade, tabela_markdown  # noqa: E402
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR  # noqa: E402
from aafton.live_compat import (  # noqa: E402
    Conversao,
    converter_fixture,
    fixtures_disponiveis,
    importar,
    perfeicao_antes,
    perfeicao_depois,
)
from aafton.live_compat_media import MidiaLocalInvalida, inventario_de_midias  # noqa: E402
from aafton.live_compat_proveniencia import (  # noqa: E402
    PASTA_DO_LIVE,
    SAIDA_ESPERADA,
    ProvenienciaInvalida,
    caminhos_do_caso,
    hashes_do_caso,
    sha256_arquivo,
    substituir_sidecar_atomico,
    validar_pacote,
)
from aafton.report import Report  # noqa: E402
from aafton.sidecar import sidecar_path_for  # noqa: E402
from aafton.timecode import TimecodeRate  # noqa: E402
from scripts.live_compat_c04 import caso_c04  # noqa: E402
from scripts.live_compat_c11 import aaf_com_combiner, fonte_com_canais_distintos  # noqa: E402
from tests import aaf_factory  # noqa: E402

SR = 48000
ENTRADA = "entrada.als"
SAIDA = SAIDA_ESPERADA
PERGUNTA = "PERGUNTA.txt"
PADRAO = Path("out/r077b")
ABRE_LIMPO = "O Live abre sem reparo, conversao de versao, midia ausente ou relink"


class PacoteComDevolucao(RuntimeError):
    """O pacote ja tem devolucao do operador: preparar recusa mexer nele."""


@dataclass(frozen=True)
class Caso:
    id: str
    titulo: str
    dialeto: str
    origem: str
    pergunta: str
    construir: Callable[[Path], Conversao]


def _fixture_cheia(nome: str) -> Callable[[Path], Conversao]:
    """Converte a fixture de verdade em copia isolada, com a midia no pacote."""
    return lambda pasta: converter_fixture(nome, pasta, cheio=True, isolada=True)


def _wav(pasta: Path, nome: str = "fonte.wav", canais: int = 1, segundos: int = 70) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    caminho.write_bytes(aaf_factory.wav_bytes(SR * segundos, channels=canais, sample_rate=SR))
    return caminho


def _sintetico(
    construtor: Callable[..., object], *, canais: int = 1, fonte: Callable[..., Path] | None = None
) -> Callable[[Path], Conversao]:
    def construir(pasta: Path) -> Conversao:
        wav = fonte(pasta / "midia", canais) if fonte else _wav(pasta / "midia", canais=canais)
        aaf = pasta / "fonte.aaf"
        construtor(aaf, wav)
        return importar(aaf, pasta / "caso.als", wav.parent)

    return construir


def _sessao_de_pan(pasta: Path) -> SessionIR:
    """Uma track mono com pan e uma estereo com pan, como o Live as guarda no IR."""
    mono = _wav(pasta, "mono.wav")
    estereo = _wav(pasta, "estereo.wav", canais=2)
    media = [
        MediaAsset(
            id="m",
            filename=mono.name,
            channels=1,
            sample_rate=SR,
            duration_samples=SR * 70,
            status="resolved",
            resolved_path=str(mono),
        ),
        MediaAsset(
            id="e",
            filename=estereo.name,
            channels=2,
            sample_rate=SR,
            duration_samples=SR * 70,
            status="resolved",
            resolved_path=str(estereo),
        ),
    ]
    origem = 3600 * SR
    tracks = []
    for indice, (nome, asset, canais, pan) in enumerate(
        (("PAN MONO", "m", 1, -0.5), ("PAN ESTEREO", "e", 2, 0.3))
    ):
        track = TrackIR(
            id=f"t{indice}",
            source_index=indice,
            name=nome,
            kind="audio",
            channel_count=canais,
            pan=pan,
        )
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id=track.id,
                channel_index=0,
                event_index=0,
                name=nome,
                media_id=asset,
                timeline_start_samples=origem + SR,
                timeline_end_samples=origem + 5 * SR,
                source_offset_samples=0,
            )
        )
        tracks.append(track)
    return SessionIR(
        name="pan",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=origem,
        tracks=tracks,
        media=media,
    )


def _caso_de_pan(pasta: Path) -> Conversao:
    midia = pasta / "midia"
    aaf = pasta / "fonte.aaf"
    report = Report(strict=True)
    write_aaf(_sessao_de_pan(midia), aaf, report)
    return importar(aaf, pasta / "caso.als", midia)


def _fades_assimetricos(aaf: Path, wav: Path) -> object:
    sequencia = [
        ("filler", SR),
        ("fade", 4800, "Fade 1"),
        ("clip", 4 * SR, SR),
        ("fade", 19200, "Fade 2"),
    ]
    return aaf_factory.build_aaf_with_rendered_fades(aaf, wav, sequencia)


def _pergunta(especifica: str) -> str:
    return f"{ABRE_LIMPO}, e {especifica}"


CASOS: tuple[Caso, ...] = (
    Caso(
        "C01",
        "posicao fora da grade",
        "Pro Tools",
        "TESTE_DF_2997",
        _pergunta(
            "os clips ficam onde estavam, sem pular para a grade? "
            "Resultado esperado: nenhum clip se move ao abrir."
        ),
        _fixture_cheia("TESTE_DF_2997"),
    ),
    Caso(
        "C02",
        "timecode drop frame",
        "Pro Tools",
        "TESTE_DF_2997_correto",
        _pergunta("a regua mostra SMPTE 29.97 drop? Resultado esperado: formato 29.97 drop."),
        _fixture_cheia("TESTE_DF_2997_correto"),
    ),
    Caso(
        "C03",
        "marcadores e automacao de ganho",
        "Pro Tools",
        "SPOT_AAF_TESTE",
        _pergunta(
            "os locators e as curvas de volume aparecem? "
            "Resultado esperado: locators com nome e curvas nas tracks automatizadas."
        ),
        _fixture_cheia("SPOT_AAF_TESTE"),
    ),
    Caso(
        "C04",
        "multicanal e estereo",
        "Pro Tools",
        "TESTE_AFF_PROTOOLS",
        _pergunta(
            "as tracks 5.1 e ambisonicas aparecem agrupadas por canal? "
            "Resultado esperado: um grupo por track multicanal, um canal por track."
        ),
        caso_c04,
    ),
    Caso(
        "C05",
        "ganho estatico",
        "pyaaf2",
        "aaf_factory.build_aaf_with_gain",
        _pergunta("o ganho do clip mostra cerca de -6 dB? Resultado esperado: -6.0 dB no clip."),
        _sintetico(aaf_factory.build_aaf_with_gain),
    ),
    Caso(
        "C06",
        "automacao de ganho do clip",
        "pyaaf2",
        "aaf_factory.build_aaf_with_varying_gain",
        _pergunta(
            "a curva de volume da track sobe e desce? "
            "Resultado esperado: rampa de 0 dB para -6 dB e de volta."
        ),
        _sintetico(
            lambda a, w: aaf_factory.build_aaf_with_varying_gain(
                a, w, [((0, 1), (1, 1)), ((1, 2), (1, 2)), ((1, 1), (1, 1))]
            )
        ),
    ),
    Caso(
        "C07",
        "crossfade",
        "pyaaf2",
        "aaf_factory.build_aaf_with_transition",
        _pergunta(
            "os dois clips se cruzam com crossfade? "
            "Resultado esperado: crossfade de meio segundo entre os clips."
        ),
        _sintetico(aaf_factory.build_aaf_with_transition),
    ),
    Caso(
        "C08",
        "fade assimetrico",
        "Pro Tools (sintetico)",
        "aaf_factory.build_aaf_with_rendered_fades",
        _pergunta(
            "o clip tem fade-in curto e fade-out longo? "
            "Resultado esperado: fade-in de 0,1 s e fade-out de 0,4 s."
        ),
        _sintetico(_fades_assimetricos),
    ),
    Caso(
        "C09",
        "pan mono e estereo",
        "aafton",
        "write_aaf de um SessionIR com pan",
        _pergunta(
            "o pan da track mono esta a esquerda e o da estereo no centro? "
            "Resultado esperado: mono 25L; estereo C, porque o pan estereo nao "
            "atravessa o AAF (defeito D3)."
        ),
        _caso_de_pan,
    ),
    # Sem caso Premiere: a fixture real pede 2,7 GB de portadoras e o sintetico do
    # aaf_factory e recusado no estrito (AAF_PAN_VALUE_INVALID). Ver LIVE_COMPAT.md.
    Caso(
        "C10",
        "dialeto Resolve",
        "Resolve (sintetico)",
        "aaf_factory.build_resolve_synthetic_aaf",
        _pergunta("ha dois clips com transicao? Resultado esperado: dois clips com crossfade."),
        _sintetico(
            lambda a, w: aaf_factory.build_resolve_synthetic_aaf(a, w, include_video_slot=False)
        ),
    ),
    Caso(
        "C11",
        "dialeto Pro Tools com combiner",
        "Pro Tools (sintetico)",
        "tests.aaf_factory.build_calibration_aaf",
        _pergunta(
            "a track estereo tem um clip estereo so? "
            "Resultado esperado: uma track estereo, sem par L/R separado."
        ),
        _sintetico(aaf_com_combiner, canais=2, fonte=fonte_com_canais_distintos),
    ),
)


def _pasta_do_caso(pacote: Path, caso: Caso) -> Path:
    return pacote / f"{caso.id}-{caso.titulo.replace(' ', '-')}"


def _recusar_com_devolucao(pacote: Path) -> None:
    """Recusa o pacote que ja tem devolucao do operador, antes de escrever.

    Varre o pacote inteiro, nao so os casos escolhidos: uma devolucao em um
    caso que nao vai ser refeito continua sendo evidencia do operador.
    """
    achadas = sorted(pacote.rglob(SAIDA)) if pacote.is_dir() else []
    if not achadas:
        return
    raise PacoteComDevolucao(
        "preparar recusa: o pacote ja tem devolucao do operador em "
        + ", ".join(p.relative_to(pacote).as_posix() for p in achadas)
        + ". Nada foi reescrito: gere o pacote em um diretorio novo."
    )


def _preparar_caso(pacote: Path, caso: Caso) -> dict:
    maquina = pacote / "_maquina" / caso.id
    if maquina.exists():
        shutil.rmtree(maquina)
    conversao = caso.construir(maquina)
    if conversao.codigo != 0:
        raise RuntimeError(f"{caso.id}: a importacao falhou\n{conversao.relatorio[-2000:]}")
    (maquina / "relatorio.md").write_text(conversao.relatorio, encoding="utf-8")
    pasta = _pasta_do_caso(pacote, caso)
    pasta.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(conversao.als, pasta / ENTRADA)
    (pasta / PERGUNTA).write_text(
        f"{caso.id} - {caso.titulo}\n\nAbra {ENTRADA} no Live, responda e salve como "
        f"{SAIDA} nesta pasta (a pasta {PASTA_DO_LIVE} que o Live cria tambem vale).\n\n"
        f"{caso.pergunta}\n",
        encoding="utf-8",
    )
    caminhos = caminhos_do_caso(
        pacote,
        entrada=pasta / ENTRADA,
        gerado=conversao.als,
        aaf=conversao.aaf,
        relatorio=maquina / "relatorio.md",
    )
    return {
        "id": caso.id,
        "titulo": caso.titulo,
        "dialeto": caso.dialeto,
        "origem": caso.origem,
        "pasta": pasta.relative_to(pacote).as_posix(),
        "als_de_entrada": f"{pasta.relative_to(pacote).as_posix()}/{ENTRADA}",
        "saida_esperada": f"{pasta.relative_to(pacote).as_posix()}/{SAIDA}",
        "pergunta": caso.pergunta,
        "aaf": conversao.aaf.relative_to(pacote).as_posix(),
        "als_gerado": conversao.als.relative_to(pacote).as_posix(),
        "build_do_gerado": identidade(conversao.als),
        "caminhos_proveniente": caminhos,
        "hashes": hashes_do_caso(pacote, caminhos),
        "midias_proveniente": inventario_de_midias(pacote, conversao.als),
    }


def preparar(pacote: Path, ids: list[str] | None = None) -> dict:
    pacote = pacote.resolve()
    _recusar_com_devolucao(pacote)
    pacote.mkdir(parents=True, exist_ok=True)
    escolhidos = [c for c in CASOS if not ids or c.id in ids]
    manifesto = {
        "contrato": "R07.6 E2",
        "gesto_do_operador": f"abrir {ENTRADA} no Live, responder a pergunta e salvar como {SAIDA}",
        "casos": [_preparar_caso(pacote, caso) for caso in escolhidos],
    }
    (pacote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifesto


def _medir_caso(item: dict, validados: dict[str, Path]) -> dict:
    """Mede o caso com os caminhos conferidos: nenhum e recalculado aqui."""
    entrada = validados["entrada"]
    saida = validados["saida"]
    gerado = validados["als_gerado"]
    aaf = validados["aaf"]
    relatorio = validados["relatorio"].read_text(encoding="utf-8")
    antes = perfeicao_antes(Conversao(aaf, gerado, 0, relatorio), item["dialeto"])
    medida = {
        "id": item["id"],
        "dialeto": item["dialeto"],
        "antes": antes.__dict__ | {"valor": antes.valor},
    }
    if not saida.is_file():
        return medida | {"situacao": "pendente: o operador ainda nao devolveu " + SAIDA}
    substituir_sidecar_atomico(validados["sidecar"], sidecar_path_for(saida))
    diferencas = comparar(entrada, saida)
    depois = perfeicao_depois(
        aaf,
        saida,
        diferencas,
        item["dialeto"],
        perdas_abertas_antes=antes.perdas_d,
    )
    return medida | {
        "situacao": "medido",
        "depois": depois.__dict__ | {"valor": depois.valor},
        "hashes": {"entrada": sha256_arquivo(entrada), "saida": sha256_arquivo(saida)},
        "contagem": contagem(diferencas),
        "diferencas": [d.__dict__ for d in diferencas],
        "tabela": tabela_markdown(diferencas, caso=item["id"]),
    }


def perfeicao_das_fixtures(pasta: Path) -> list[dict]:
    """Coluna antes do Live para as oito fixtures, com portadoras de uma amostra."""
    linhas = []
    for item in fixtures_disponiveis():
        conversao = converter_fixture(item.nome, pasta / item.nome)
        medida = perfeicao_antes(conversao, item.dialeto)
        linhas.append({"fixture": item.nome} | medida.__dict__ | {"valor": medida.valor})
    return linhas


def _markdown(medidas: list[dict], fixtures: list[dict]) -> str:
    linhas = ["| caso | dialeto | antes | depois | situacao |", "| --- | --- | --- | --- | --- |"]
    for m in medidas:
        depois = f"{m['depois']['valor']:.4f}" if "depois" in m else "pendente"
        linhas.append(
            f"| {m['id']} | {m['dialeto']} | {m['antes']['valor']:.4f} | {depois} | {m['situacao']} |"
        )
    linhas += [
        "",
        "| fixture | dialeto | build | entrada | exatos | perdas D | antes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in fixtures:
        linhas.append(
            f"| {f['fixture']} | {f['dialeto']} | {f['build']} | {f['entrada']} | "
            f"{f['exatos']} | {f['perdas_d']} | {f['valor']:.4f} |"
        )
    tabelas = [m["tabela"] for m in medidas if m.get("tabela") and m["diferencas"]]
    return "\n".join(linhas + [""] + tabelas) + "\n"


def medir(pacote: Path, *, fixtures: bool = True) -> dict:
    pacote = pacote.resolve()
    manifesto = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    validados = validar_pacote(pacote, manifesto)
    casos = zip(manifesto["casos"], validados, strict=True)
    medidas = [_medir_caso(item, validado) for item, validado in casos]
    fixtures = perfeicao_das_fixtures(pacote / "_maquina" / "_antes") if fixtures else []
    resultado = {"casos": medidas, "fixtures": fixtures}
    (pacote / "medicao.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pacote / "medicao.md").write_text(_markdown(medidas, fixtures), encoding="utf-8")
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("preparar", help="gera um caso por pasta e o manifesto.json")
    p.add_argument("--pacote", type=Path, required=True)
    p.add_argument("--caso", action="append", help="so este caso (C01..C11); repetivel")
    m = sub.add_parser("medir", help="compara as saidas devolvidas e calcula a perfeicao")
    m.add_argument("--pacote", type=Path, default=PADRAO)
    m.add_argument("--sem-fixtures", action="store_true", help="nao recalcula a coluna antes")
    args = parser.parse_args(argv)
    try:
        if args.comando == "preparar":
            manifesto = preparar(args.pacote, args.caso)
            for item in manifesto["casos"]:
                print(f"{item['id']}  {item['als_de_entrada']}")
            return 0
        medir(args.pacote, fixtures=not args.sem_fixtures)
    except (PacoteComDevolucao, ProvenienciaInvalida, MidiaLocalInvalida) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print((args.pacote.resolve() / "medicao.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
