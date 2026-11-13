"""Contrato entre o .als do aafton e o schema do modelo do Live.

O Live publica, em Resources/Schema/<MinorVersion>.txt, o schema do proprio
modelo de objetos. O template do repositorio e um espécime salvo pelo Live;
o schema e a outra autoridade. Estes testes medem se os dois concordam e se o
.als gerado pelo aafton usa algum nome que o schema nao declara.
"""

from pathlib import Path

import pytest
from aafton.als_paths import (
    AUDIO_CLIP_FIELDS,
    LIVESET_FIELDS,
    LOCATOR_FIELDS,
    TRACK_FIELDS,
)
from aafton.als_schema import (
    CONCEITOS_AAF,
    SCHEMA_BUILD,
    SCHEMA_DIGEST,
    SCHEMA_SNAPSHOT,
    conferir_snapshot,
    destino_xml,
    digest_bytes,
    ler_bytes,
    schema_do_build,
    schema_instalado,
)
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")
UMA_HORA = 172800000

# A raiz do documento .als nao faz parte do modelo: o schema descreve o modelo.
RAIZ_DOCUMENTO = "Ableton"

# Mapa de campos declarados -> tag do no aonde o caminho e relativo.
DECLARADOS = {
    "AudioClip": AUDIO_CLIP_FIELDS,
    "Locator": LOCATOR_FIELDS,
    "AudioTrack": TRACK_FIELDS,
    "LiveSet": LIVESET_FIELDS,
}

# Caminhos que o schema do Live nao declara: nenhum deles pode resolver.
CAMINHOS_FORA_DO_SCHEMA = [
    ("ClasseInexistente", "QualquerCoisa"),
    ("AudioClip", ""),
    ("AudioClip", "SampleRef/FileRef/Path/"),
    ("AudioClip", "SampleRef//FileRef"),
    ("AudioClip", "SampleRef/FileRef/Path/NaoExiste"),
    ("AudioTrack", "AutomationEnvelopes/Envelopes/Lixo/FloatEvent"),
    ("AudioClip", "Fades/Erro"),
]

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture(scope="module")
def schema():
    return schema_do_build()


@pytest.fixture(scope="module")
def template():
    return load_als(TEMPLATE)


def _sessao(tmp_path):
    wav = tmp_path / "cena.wav"
    wav.write_bytes(b"RIFF")
    asset = MediaAsset(
        id="m1",
        filename="cena.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav),
    )
    faixa = TrackIR(
        id="t0",
        source_index=0,
        name="DIALOGO 1",
        kind="audio",
        channel_count=1,
        pan=0.25,
        gain_points=[(UMA_HORA, 1.0), (UMA_HORA + 48000, 0.5)],
    )
    faixa.clips.append(
        ClipIR(
            id="c0",
            track_id=faixa.id,
            channel_index=0,
            event_index=0,
            name="cena.wav",
            media_id="m1",
            timeline_start_samples=UMA_HORA,
            timeline_end_samples=UMA_HORA + 96000,
            source_offset_samples=0,
        )
    )
    return SessionIR(
        name="contrato",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        media=[asset],
        tracks=[faixa],
    )


def test_o_snapshot_bate_com_o_digest_do_vendor():
    """O snapshot versionado e a copia do arquivo publicado pelo Live."""
    assert conferir_snapshot(), (
        "o snapshot do schema nao bate com o digest do arquivo publicado pelo "
        "vendor; regenere a partir de Resources/Schema do Live instalado"
    )
    assert digest_bytes(ler_bytes(SCHEMA_SNAPSHOT)) == SCHEMA_DIGEST


def test_o_schema_descreve_o_build_do_template(schema, template):
    """O schema versionado e o do build que o template declara."""
    assert template.get("MinorVersion") == SCHEMA_BUILD
    assert str(schema.translator) == SCHEMA_BUILD.split("_")[1]
    assert schema.change_count == template.get("SchemaChangeCount")


@pytest.mark.parametrize("raiz", sorted(DECLARADOS))
def test_cada_campo_declarado_existe_no_template(template, raiz):
    """Todo caminho declarado em als_paths existe no espécime do Live."""
    no = template.find(f".//{raiz}")
    assert no is not None, f"o template nao contem <{raiz}>"
    for nome, caminho in DECLARADOS[raiz].items():
        assert no.find(caminho) is not None, (
            f"o campo {nome!r} foi declarado como {caminho!r} em {raiz}, mas nao "
            f"existe no template. Corrija com base no Live, nao de memoria."
        )


@pytest.mark.parametrize("raiz", sorted(DECLARADOS))
def test_cada_campo_declarado_existe_no_schema(schema, raiz):
    """Todo caminho declarado em als_paths resolve no schema do Live."""
    for nome, caminho in DECLARADOS[raiz].items():
        resolucao = schema.resolver(caminho, raiz)
        assert resolucao is not None, (
            f"o campo {nome!r} foi declarado como {raiz}/{caminho!r}, mas o schema "
            f"do Live nao declara esse caminho."
        )
        assert resolucao.cauda == (), (
            f"o campo {nome!r} ({raiz}/{caminho!r}) resolve com cauda de runtime "
            f"{resolucao.cauda}: o schema declara o caminho inteiro."
        )


@pytest.mark.parametrize("conceito", CONCEITOS_AAF, ids=lambda c: c.id)
def test_todo_caminho_de_conceito_resolve_no_schema(schema, conceito):
    """Nenhum caminho da tabela de conceitos e inventado."""
    for raiz, caminho in conceito.caminhos:
        assert schema.resolver(caminho, raiz) is not None, (
            f"o conceito {conceito.id!r} cita {raiz}/{caminho!r}, que o schema do Live nao declara."
        )


@pytest.mark.parametrize(("raiz", "caminho"), CAMINHOS_FORA_DO_SCHEMA)
def test_caminho_que_o_schema_nao_declara_nao_resolve(schema, raiz, caminho):
    """O resolvedor recusa caminho que o schema do Live nao declara.

    Raiz desconhecida, caminho vazio, componente vazio, barra no fim, caminho
    abaixo de um valor primitivo e nome que ninguem declara: nenhum resolve.
    O schema e o contrato; o resolvedor nao fabrica cauda de runtime para
    tapar buraco.
    """
    assert schema.resolver(caminho, raiz) is None


@pytest.mark.parametrize(("raiz", "caminho"), CAMINHOS_FORA_DO_SCHEMA)
def test_resolve_ou_falha_recusa_caminho_que_o_schema_nao_declara(schema, raiz, caminho):
    """A variante que falha levanta ValueError com o caminho que nao bateu."""
    with pytest.raises(ValueError):
        schema.resolve_ou_falha(caminho, raiz)


def test_item_de_lista_resolve_pelo_nome_da_classe(schema):
    """Depois de um campo de lista, o segmento seguinte nomeia o item."""
    resolucao = schema.resolver(
        "AutomationEnvelopes/Envelopes/AutomationEnvelope",
        "AudioTrack",
    )
    assert resolucao is not None
    assert resolucao.classe == "AutomationEnvelope"


def test_caminho_de_automacao_resolve_ate_a_classe_do_evento(schema):
    """O caminho inteiro de automacao resolve, sem cauda de runtime."""
    resolucao = schema.resolver(
        "AutomationEnvelopes/Envelopes/AutomationEnvelope/Automation/Events/FloatEvent",
        "AudioTrack",
    )
    assert resolucao is not None
    assert resolucao.classe == "FloatEvent"
    assert resolucao.cauda == ()


def test_resolver_nao_fabrica_cauda_de_runtime(schema):
    """Nenhum nome de runtime e inventado: a cauda da resolucao fica vazia."""
    resolucao = schema.resolver("SampleRef/FileRef/Path", "AudioClip")
    assert resolucao is not None
    assert resolucao.classe == "String"
    assert resolucao.cauda == ()


def test_o_als_gerado_nao_usa_nome_fora_do_schema(schema, tmp_path):
    """O .als gerado pelo aafton so usa nomes que o schema do Live declara."""
    destino = tmp_path / "gerado.als"
    write_session(
        _sessao(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000),
        Report(strict=True),
    )
    fora = sorted(
        {
            node.tag
            for node in load_als(destino).iter()
            if not schema.tem_elemento(node.tag) and node.tag != RAIZ_DOCUMENTO
        }
    )
    assert not fora, (
        f"o .als gerado usa nomes que o schema do Live nao declara: {fora}. "
        f"Um nome assim e um elemento que o Live nao conhece."
    )


def test_o_template_tambem_so_usa_nomes_do_schema(schema, template):
    """O espécime do Live concorda com o schema: a mesma autoridade, dois olhos."""
    fora = sorted(
        {
            node.tag
            for node in template.iter()
            if not schema.tem_elemento(node.tag) and node.tag != RAIZ_DOCUMENTO
        }
    )
    assert not fora, f"o template usa nomes fora do schema: {fora}"


def test_o_schema_conta_nomes_de_campo_e_declaracoes(schema):
    """Nomes de campo distintos e pares (classe, campo) sao contagens diferentes.

    O mesmo nome pode ser declarado por varias classes: sao 3.766 nomes de
    campo, mas 7.943 declaracoes classe-campo.
    """
    assert len(schema.elementos) == 3766
    assert schema.declaracoes() == 7943
    assert schema.declaracoes() > len(schema.elementos)


def test_o_time_do_clip_e_atributo_no_xml_e_elemento_no_modelo(schema):
    """A resolucao do modelo e a serializacao do XML sao coisas separadas.

    `AudioClip/Time` resolve no schema como campo de classe `Double`, e o
    `.als` o grava no atributo `@Time` do <AudioClip>, e nao em um elemento
    filho.
    """
    resolucao = schema.resolver("Time", "AudioClip")
    assert resolucao is not None
    assert resolucao.classe == "Double"
    assert destino_xml("AudioClip", "Time") == ("atributo", "Time")
    assert destino_xml("AudioClip", "CurrentStart") == ("elemento", "CurrentStart")


def _instalacao_falsa(raiz: Path, pastas: dict[str, str]) -> Path:
    """Cria uma instalacao do Live falsa, com o schema de cada pasta."""
    for nome, conteudo in pastas.items():
        destino = raiz / nome / "Resources" / "Schema"
        destino.mkdir(parents=True)
        (destino / f"{SCHEMA_BUILD}.txt").write_text(conteudo, encoding="utf-8")
    return raiz


def test_o_schema_instalado_preferiza_a_instalacao_estavel(tmp_path):
    """A beta da primeira raiz nao pode ganhar da estavel da segunda.

    As duas raizes sao vasculhadas antes de escolher: a instalacao estavel
    pode estar na segunda.
    """
    primeiro = tmp_path / "primeiro"
    segundo = tmp_path / "segundo"
    _instalacao_falsa(primeiro, {"Live 12 Beta Suite": "beta"})
    _instalacao_falsa(segundo, {"Live 12 Suite": "estavel"})

    escolhido = schema_instalado(roots=(primeiro, segundo))
    esperado = segundo / "Live 12 Suite" / "Resources" / "Schema" / f"{SCHEMA_BUILD}.txt"

    assert escolhido == esperado


def test_o_schema_instalado_desempata_pelo_caminho(tmp_path):
    """Duas instalacoes estaveis do mesmo build: o caminho normalizado desempata."""
    raiz = tmp_path / "raiz"
    _instalacao_falsa(raiz, {"Live 12 Suite": "um", "Live 12 Education": "outro"})

    escolhido = schema_instalado(roots=(raiz,))
    esperado = raiz / "Live 12 Education" / "Resources" / "Schema" / f"{SCHEMA_BUILD}.txt"

    assert escolhido == esperado


def test_o_schema_instalado_sem_build_nao_devolve_nada(tmp_path):
    """Sem o arquivo do build, nao ha schema instalado."""
    vazio = tmp_path / "vazio"
    vazio.mkdir()

    assert schema_instalado(roots=(vazio,)) is None
