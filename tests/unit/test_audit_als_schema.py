"""O que a auditoria do schema afirma, e o que ela mede de verdade.

A auditoria classifica cada caminho do modelo a partir do `.als` realmente
gerado. Declaracao em `als_paths` nao prova escrita; presenca no arquivo
gerado tambem nao. So a medicao controlada prova: duas entradas com valores
diferentes, escritas pelo proprio aafton, e comparadas com o previsto.
"""

import copy
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest
from aafton import als_write_clips
from aafton.als_schema import (
    SCHEMA_SNAPSHOT,
    SchemaLive,
    destino_xml,
    ler_bytes,
    schema_do_build,
)
from aafton.als_xml import load_als, save_als
from scripts.audit_als_schema import (
    AUSENTE,
    ESCRITO,
    PRESENTE,
    classifica,
    coletar,
    confere_build,
    declarados,
    main,
)
from scripts.audit_als_schema_measure import (
    AMOSTRAS,
    CAMINHO_EVENTOS,
    CAMINHO_PONTEE,
    SENTINEL_OBSERVADO,
    caminhos_provados,
    gerar_documentos,
    medir,
)
import scripts.audit_als_schema_measure as audit_measure

TEMPLATE = Path("templates/live12.als")

# O caminho do envelope de ganho, como a tabela de conceitos o escreve.
CHAVE_EVENTOS = f"AudioTrack/{CAMINHO_EVENTOS}"
# O caminho do alvo de automacao do volume, que a tabela de conceitos escreve.
CHAVE_ALVO = "AudioTrack/DeviceChain/Mixer/Volume/AutomationTarget"
# A conferencia de integridade: o ponteiro que o envelope carrega.
CHAVE_LIGACAO = (
    "AudioTrack/AutomationEnvelopes/Envelopes/AutomationEnvelope/EnvelopeTarget/PointeeId"
)
CHAVE_PAN = "AudioTrack/DeviceChain/Mixer/Pan/Manual"
CHAVE_GANHO_CLIP = "AudioClip/SampleVolume"
CHAVE_INICIO = "AudioClip/CurrentStart"
CHAVE_FIM = "AudioClip/CurrentEnd"
CHAVE_TEMPO = "AudioClip/Time"

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture(scope="module")
def template_root():
    return load_als(TEMPLATE)


@pytest.fixture(scope="module")
def schema():
    return schema_do_build()


@pytest.fixture(scope="module")
def documentos(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("medida")
    return gerar_documentos(pasta, TEMPLATE)


def _medicao(medicoes, chave):
    achadas = [medicao for medicao in medicoes if medicao.chave == chave]
    assert len(achadas) == 1, f"a chave {chave!r} nao tem uma unica medicao"
    return achadas[0]


def _beats_por_razao(samples: int, bpm: int = 60, taxa: int = 48000) -> float:
    """Beat pela razao de samples: a previsao independente do escritor."""
    return float(Fraction(samples) * Fraction(bpm) / (60 * taxa))


def test_o_audit_roda_e_mede_todas_as_provas():
    """A auditoria completa termina sem prova em aberto."""
    assert main([]) == 0


def test_o_pan_medido_bate_com_a_entrada(documentos, template_root):
    """O pan gravado e o pan da entrada, e nao o do template."""
    medicao = _medicao(medir(documentos, template_root), CHAVE_PAN)

    assert medicao.ok
    assert medicao.medido == ("0.25", "0.75")
    assert medicao.previsto == (0.25, 0.75)


def test_o_ganho_estatico_do_clip_bate_com_a_entrada(documentos, template_root):
    """O ganho estatico do clip e o da entrada."""
    medicao = _medicao(medir(documentos, template_root), CHAVE_GANHO_CLIP)

    assert medicao.ok
    assert medicao.medido == ("0.375", "0.625")
    assert medicao.previsto == (0.375, 0.625)


def test_os_eventos_de_ganho_tem_valores_e_tempos_previstos(documentos, template_root):
    """Cada evento traz o tempo e o valor previstos a partir da entrada."""
    medicao = _medicao(medir(documentos, template_root), CHAVE_EVENTOS)

    assert medicao.ok
    assert medicao.medido[0] == ((-63072000.0, 1.0), (7.0, 1.0), (10.0, 0.5))
    assert medicao.medido[1] == ((-63072000.0, 0.25), (11.0, 0.25), (12.5, 0.75))


def test_o_envelope_de_ganho_aponta_para_o_volume_da_faixa(documentos, template_root):
    """A ligacao do envelope com o alvo de automacao do volume fecha."""
    medicao = _medicao(medir(documentos, template_root), CHAVE_LIGACAO)

    assert medicao.ok
    for alvo, ponteiro in medicao.medido:
        assert alvo is not None
        assert alvo == ponteiro


def test_a_conferencia_de_ligacao_nao_prova_escrita(documentos, template_root):
    """A ligacao e integridade: ela nao prova caminho escrito pelo escritor.

    O `AutomationTarget` do volume e herdado do template; o escritor le o Id
    dele e escreve o ponteiro do envelope. Igualar os dois Ids nao mostra que
    o caminho do alvo foi gravado, e ele fica `presente`.
    """
    medicoes = medir(documentos, template_root)
    ligacao = _medicao(medicoes, CHAVE_LIGACAO)
    do_gerado = {"AudioTrack": coletar(documentos["A"], "AudioTrack")}
    caminho = "DeviceChain/Mixer/Volume/AutomationTarget"

    situacao = classifica(caminho, "AudioTrack", do_gerado, caminhos_provados(medicoes))

    assert ligacao.tipo == "integridade"
    assert CHAVE_ALVO not in caminhos_provados(medicoes)
    assert situacao == PRESENTE


def test_a_posicao_do_clip_bate_com_a_razao_de_amostras(documentos, template_root):
    """A posicao gravada e a razao de samples, e nao zero."""
    medicoes = medir(documentos, template_root)
    inicio = _medicao(medicoes, CHAVE_INICIO)
    tempo = _medicao(medicoes, CHAVE_TEMPO)
    assert inicio.ok and tempo.ok

    amostras = list(zip(AMOSTRAS, inicio.medido, tempo.medido))
    for amostra, medido_inicio, medido_tempo in amostras:
        esperado = _beats_por_razao(amostra.inicio_relativo)
        assert esperado != 0.0
        assert float(medido_inicio) == pytest.approx(esperado)
        assert float(medido_tempo) == pytest.approx(esperado)


def test_o_fim_do_clip_bate_com_a_razao_de_amostras(documentos, template_root):
    """O fim gravado e o inicio mais a duracao, pela razao de samples."""
    medicao = _medicao(medir(documentos, template_root), CHAVE_FIM)

    assert medicao.ok
    for amostra, medido in zip(AMOSTRAS, medicao.medido):
        total = amostra.inicio_relativo + amostra.duracao
        assert float(medido) == pytest.approx(_beats_por_razao(total))


def test_o_time_do_clip_e_atributo_e_nao_elemento(documentos, template_root):
    """No modelo o campo e Time; no .als e o atributo @Time do <AudioClip>."""
    clip = documentos["A"].find(".//AudioClip")
    clip_do_template = template_root.find(".//AudioClip")

    assert clip.get("Time") is not None
    assert clip.find("Time") is None
    assert clip_do_template.get("Time") is not None
    assert clip_do_template.find("Time") is None
    assert destino_xml("AudioClip", "Time") == ("atributo", "Time")
    assert destino_xml("AudioClip", "CurrentStart") == ("elemento", "CurrentStart")


def test_o_audit_recusa_template_de_outro_build(tmp_path, capsys):
    """Um template de outro build nao descreve o mesmo modelo."""
    raiz = load_als(TEMPLATE)
    raiz.set("MinorVersion", "12.0_12000")
    raiz.set("SchemaChangeCount", "999")
    destino = tmp_path / "outro-build.als"
    save_als(raiz, destino)

    codigo = main(["--template", str(destino)])

    assert codigo != 0
    erro = capsys.readouterr().err
    assert "MinorVersion" in erro
    assert "SchemaChangeCount" in erro


def test_o_audit_recusa_apenas_o_change_count_divergente(tmp_path, capsys):
    """O SchemaChangeCount tambem e chave de casamento com o schema."""
    raiz = load_als(TEMPLATE)
    raiz.set("SchemaChangeCount", "999")
    destino = tmp_path / "change-count.als"
    save_als(raiz, destino)

    codigo = main(["--template", str(destino)])

    assert codigo != 0
    assert "SchemaChangeCount" in capsys.readouterr().err


def test_caminho_declarado_que_some_do_xml_gerado_e_ausente(documentos):
    """Declaracao nao prova escrita: o que manda e o XML gerado."""
    doc = copy.deepcopy(documentos["A"])
    clip = doc.find(".//AudioClip")
    clip.remove(clip.find("CurrentStart"))
    do_gerado = {"AudioClip": coletar(doc, "AudioClip")}

    assert "CurrentStart" in declarados()["AudioClip"]
    situacao = classifica("CurrentStart", "AudioClip", do_gerado, set())

    assert situacao == AUSENTE


def test_caminho_presente_sem_prova_de_escrita_e_presente(documentos):
    """Presenca sem medicao e presente: herdar do template nao se mede."""
    do_gerado = {"AudioTrack": coletar(documentos["A"], "AudioTrack")}
    caminho = "DeviceChain/Mixer/PanMode"

    assert caminho in do_gerado["AudioTrack"].elementos
    situacao = classifica(caminho, "AudioTrack", do_gerado, set())

    assert situacao == PRESENTE


def test_caminho_medido_e_classificado_como_escrito(documentos):
    """O caminho que a medicao provou sai como escrito."""
    do_gerado = {"AudioTrack": coletar(documentos["A"], "AudioTrack")}
    caminho = "DeviceChain/Mixer/Pan/Manual"

    situacao = classifica(caminho, "AudioTrack", do_gerado, {CHAVE_PAN})

    assert situacao == ESCRITO


def test_valor_errado_nao_prova_a_escrita(documentos, template_root):
    """Um valor que nao bate com o previsto nao prova escrita."""
    doc = copy.deepcopy(documentos["A"])
    doc.find(".//AudioTrack/DeviceChain/Mixer/Pan/Manual").set("Value", "0.5")
    documentos = {"A": doc, "B": documentos["B"]}

    medicao = _medicao(medir(documentos, template_root), CHAVE_PAN)

    assert not medicao.ok
    assert medicao.medido[0] == "0.5"


def test_escrita_removida_nao_prova_a_escrita(documentos, template_root):
    """Sem o valor gravado, o caminho que existia no template nao prova nada."""
    doc = copy.deepcopy(documentos["A"])
    mixer = doc.find(".//AudioTrack/DeviceChain/Mixer/Pan")
    mixer.remove(mixer.find("Manual"))
    documentos = {"A": doc, "B": documentos["B"]}

    medicao = _medicao(medir(documentos, template_root), CHAVE_PAN)

    assert not medicao.ok


def test_o_envelope_de_ganho_e_novo_e_nao_herdado(documentos, template_root):
    """O template nao tem envelope de ganho na faixa: o evento e escrito."""
    faixa_do_template = template_root.find(".//AudioTrack")

    assert faixa_do_template.find(CAMINHO_EVENTOS) is None
    medicao = _medicao(medir(documentos, template_root), CHAVE_EVENTOS)

    assert medicao.ok
    assert medicao.medido[0][0][0] == -63072000.0


def _quebra_a_ligacao(documentos, caso: str):
    """Quebra a ligacao do envelope do jeito que o caso pede."""
    copia = {rotulo: copy.deepcopy(doc) for rotulo, doc in documentos.items()}
    for doc in copia.values():
        volume = doc.find(".//AudioTrack/DeviceChain/Mixer/Volume")
        alvo = volume.find("AutomationTarget")
        ponteiro = doc.find(f".//AudioTrack/{CAMINHO_PONTEE}")
        if caso == "sem_id":
            alvo.set("Id", "")
        elif caso == "sem_ponteiro":
            ponteiro.set("Value", "")
        elif caso == "sem_no":
            volume.remove(alvo)
        else:
            ponteiro.set("Value", "999999")
    return copia


def _medicao_de_ligacao(documentos, template_root):
    """A medicao da ligacao, dos documentos que forem."""
    return _medicao(medir(documentos, template_root), CHAVE_LIGACAO)


@pytest.mark.parametrize(
    ("caso", "detalhe"),
    [
        ("sem_id", "Id"),
        ("sem_ponteiro", "PointeeId"),
        ("sem_no", "alvo de automacao"),
        ("outro_id", "nao aponta"),
    ],
)
def test_ligacao_quebrada_nao_fecha(documentos, template_root, caso, detalhe):
    """No ausente, Id vazio ou Id diferente: a ligacao tem de falhar."""
    medicao = _medicao_de_ligacao(_quebra_a_ligacao(documentos, caso), template_root)

    assert not medicao.ok
    assert detalhe in medicao.detalhe


def test_ligacao_sem_os_dois_ids_nao_fecha(documentos, template_root):
    """O caso da revisao: sem os dois Ids, a medicao nao pode dar ok."""
    quebrada = _quebra_a_ligacao(_quebra_a_ligacao(documentos, "sem_id"), "sem_ponteiro")
    medicao = _medicao_de_ligacao(quebrada, template_root)

    assert not medicao.ok
    assert medicao.medido == ((None, None), (None, None))


def test_o_sentinela_esperado_esta_no_template_fixado(template_root):
    """O tempo do sentinela veio do template, e nao do escritor.

    Em `templates/live12.als`, dentro de
    `AutomationEnvelope/Automation/Events`, o `FloatEvent` carrega
    `@Time="-63072000"`.
    """
    eventos = template_root.findall(".//AutomationEnvelope/Automation/Events/FloatEvent")

    assert eventos, "o template fixado nao tem evento de automacao"
    assert str(SENTINEL_OBSERVADO) in {evento.get("Time") for evento in eventos}


def test_mudar_o_sentinela_do_escritor_quebra_a_prova(monkeypatch, tmp_path, template_root):
    """Se o escritor mudar o sentinela, a prova tem de falhar.

    Com a expectativa vinda do escritor, o medido e o previsto moveriam
    juntos, e a prova fecharia sem dizer nada.
    """
    assert not hasattr(audit_measure, "SENTINEL_TIME")
    monkeypatch.setattr(als_write_clips, "SENTINEL_TIME", -1)

    documentos = gerar_documentos(tmp_path, TEMPLATE)
    medicao = _medicao(medir(documentos, template_root), CHAVE_EVENTOS)

    assert not medicao.ok
    assert "nao bate com o previsto" in medicao.detalhe


def _schema_com_metadados(tmp_path, mudancas, nome="schema.txt"):
    """O schema do snapshot com os metadados internos trocados."""
    raiz = ET.fromstring(ler_bytes(SCHEMA_SNAPSHOT))
    for atributo, valor in mudancas.items():
        if valor is None:
            raiz.attrib.pop(atributo, None)
        else:
            raiz.set(atributo, valor)
    destino = tmp_path / nome
    destino.write_bytes(ET.tostring(raiz, encoding="utf-8", xml_declaration=True))
    return SchemaLive.carregar(destino)


def test_o_schema_renomeado_nao_casa_com_o_template(tmp_path):
    """O nome do arquivo nao prova o build: quem prova e o TranslatorNumber.

    O arquivo foi renomeado para `12.0_12000.txt` e o template traz o
    `MinorVersion` desse build; o conteudo do schema declara outro build.
    """
    renomeado = _schema_com_metadados(tmp_path, {}, nome="12.0_12000.txt")
    raiz = load_als(TEMPLATE)
    raiz.set("MinorVersion", "12.0_12000")

    problemas = confere_build(renomeado, raiz, "12.0_12000")

    assert problemas
    assert any("TranslatorNumber" in problema for problema in problemas)


def test_o_schema_sem_metadados_internos_nao_casa(tmp_path):
    """Schema sem a identidade interna nao casa com template nenhum."""
    sem_metadados = _schema_com_metadados(
        tmp_path,
        {"Version": None, "TranslatorNumber": None, "ChangeCount": None},
    )

    problemas = confere_build(sem_metadados, load_als(TEMPLATE))

    assert len(problemas) == 3
    assert all("o schema nao declara" in problema for problema in problemas)


@pytest.mark.parametrize("atributo", ["MinorVersion", "MajorVersion", "SchemaChangeCount"])
def test_o_template_sem_identidade_nao_casa(schema, atributo):
    """O template tem de declarar a propria identidade."""
    raiz = load_als(TEMPLATE)
    raiz.attrib.pop(atributo)

    problemas = confere_build(schema, raiz)

    assert problemas
    assert any(atributo in problema for problema in problemas)
