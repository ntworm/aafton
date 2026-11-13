"""R07.6, E1: regressao do comparador semantico (midia, tempo e numero).

O comparador nao pode esconder mudanca de midia, de tempo nem de texto que
parece numero. So a reescrita de forma da mesma midia (o Live relativiza o
caminho) e ruido, e ainda assim so quando os dois lados apontam para o mesmo
arquivo. O endereco efetivo da midia e comparado fora do texto, e a grade e os
filhos do FileRef entram como campos declarados. Cada teste diz qual seria o
defeito se ele falhar.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_compare import (
    AUSENTE_NO_LIVE,
    DROPPED_POR_LIVE,
    RESCRITO_POR_LIVE,
    VALOR_DIVERGENTE,
    _normalizar_caminho,
    _referencia_efetiva,
    comparar,
)
from aafton.als_compare_campos import RUIDO_PADRAO, projetar

FIM_DO_PATH = "/SampleRef/FileRef/Path"
PADRAO = {
    "caminho": "C:/midia/cena.wav",
    "relativo": "cena.wav",
    "tipo": "5",
    "current_start": "4096",
    "tempo": "120",
    "annotation": "001",
    "loop_start": "0",
    "time_atributo": "4096",
    "time_locator": "0",
    "volume": "0.5",
    "fade": "4800",
    "grid": "001",
    "extra": "0",
}
_XML = """<ALS_Container>
  <LiveSet>
    <MainTrack>
      <DeviceChain><Mixer><Tempo><Manual Value="{tempo}"/></Tempo></Mixer></DeviceChain>
    </MainTrack>
    <Grid>
      <FixedNumerator Value="4"/>
      <FixedDenominator Value="4"/>
      <CampoDesconhecido Value="{grid}"/>
    </Grid>
    <Tracks>
      <AudioTrack Id="1">
        <Name><UserName Value="DX 1"/><EffectiveName Value="DX 1"/></Name>
        <DeviceChain><MainSequencer><Sample><ArrangerAutomation><Events>
          <AudioClip Id="1" Time="{time_atributo}">
            <Name Value="cena"/>
            <CurrentStart Value="{current_start}"/>
            <Loop><LoopStart Value="{loop_start}"/><LoopEnd Value="4096"/></Loop>
            <Fades><FadeInLength Value="{fade}"/></Fades>
            <SampleVolume Value="{volume}"/>
            <SampleRef><FileRef>
              <Path Value="{caminho}"/>{relativo}
              <RelativePathType Value="{tipo}"/>
              <OutroValor Value="{extra}"/>
            </FileRef></SampleRef>
          </AudioClip>
        </Events></ArrangerAutomation></Sample></MainSequencer></DeviceChain>
      </AudioTrack>
    </Tracks>
    <Locators><Locators><Locator>
      <Time Value="{time_locator}"/>
      <Name Value="L1"/>
      <Annotation Value="{annotation}"/>
      <IsSongStart Value="true"/>
    </Locator></Locators></Locators>
  </LiveSet>
</ALS_Container>"""


def _als(**mudancas: str | None) -> ET.Element:
    """Um .als minimo: uma track, um clip com midia e um locator."""
    valores: dict[str, str | None] = {**PADRAO, **mudancas}
    relativo = valores["relativo"]
    valores["relativo"] = "" if relativo is None else f'<RelativePath Value="{relativo}"/>'
    return ET.fromstring(_XML.format(**valores))


def _salvar(destino: Path, raiz: ET.Element) -> Path:
    destino.write_bytes(ET.tostring(raiz, encoding="utf-8"))
    return destino


def _diferencas_de(comparacao: list, sufixo: str) -> list:
    return [d for d in comparacao if d.caminho.endswith(sufixo)]


def _referencia(raiz: ET.Element, diretorio: str | None = None) -> str | None:
    """Endereco efetivo que a politica deduz do documento."""
    projecao = projetar(raiz, RUIDO_PADRAO, diretorio=diretorio)
    (chave,) = [k for k in projecao.campos if k.endswith(FIM_DO_PATH)]
    return _referencia_efetiva(projecao, chave.removesuffix(FIM_DO_PATH))


def test_midia_trocada_com_tipo_1_dos_dois_lados_e_diferenca(tmp_path):
    """a.wav -> b.wav com o caminho absoluto obsoleto igual: a troca e real."""
    a = _salvar(tmp_path / "a.als", _als(relativo="a.wav", tipo="1"))
    b = _salvar(tmp_path / "b.als", _als(relativo="b.wav", tipo="1"))
    diferencas = comparar(a, b)
    [diferenca] = _diferencas_de(diferencas, "/SampleRef/FileRef/RelativePath")
    assert diferenca.classe == VALOR_DIVERGENTE
    assert diferenca.gerado == "a.wav" and diferenca.devolvido == "b.wav"


def test_relative_path_ausente_no_live_continua_sendo_diferenca(tmp_path):
    """O Live relativizou mas nao escreveu o caminho: a midia ficou sem prova."""
    a = _salvar(tmp_path / "a.als", _als(relativo="a.wav", tipo="1"))
    b = _salvar(tmp_path / "b.als", _als(relativo=None, tipo="1"))
    [diferenca] = _diferencas_de(comparar(a, b), "/SampleRef/FileRef/RelativePath")
    assert diferenca.classe == DROPPED_POR_LIVE


@pytest.mark.parametrize("tipo", ["0", "5"], ids=["legado", "template"])
def test_relocacao_para_o_mesmo_arquivo_com_pasta_real_e_ignorada(tmp_path, tipo):
    """Absoluto -> relativo apontando para o mesmo arquivo: e reescrita de forma."""
    media = tmp_path / "cena.wav"
    a = _salvar(tmp_path / "a.als", _als(caminho=str(media), relativo=None, tipo=tipo))
    b = _salvar(tmp_path / "b.als", _als(caminho=str(media), relativo="cena.wav", tipo="1"))
    assert comparar(a, b) == []


def test_sem_pasta_a_raiz_et_e_conservadora(tmp_path):
    """Raiz ET nao tem pasta: o caminho relativo nao pode ser resolvido."""
    media = tmp_path / "cena.wav"
    a = _als(caminho=str(media), relativo=None, tipo="0")
    b = _als(caminho=str(media), relativo="cena.wav", tipo="1")
    diferencas = comparar(a, b)
    assert diferencas, "sem pasta nao ha prova de que e o mesmo arquivo"
    assert _diferencas_de(diferencas, "/SampleRef/FileRef/RelativePath")


def test_mesmo_nome_em_pastas_diferentes_nao_e_o_mesmo_arquivo(tmp_path):
    """Mesmo nome em outra pasta e outro arquivo: nome sozinho nao prova nada."""
    (tmp_path / "um").mkdir()
    (tmp_path / "dois").mkdir()
    a = _salvar(
        tmp_path / "um" / "a.als",
        _als(caminho=str(tmp_path / "um" / "cena.wav"), relativo="cena.wav", tipo="1"),
    )
    b = _salvar(
        tmp_path / "dois" / "b.als",
        _als(caminho=str(tmp_path / "dois" / "cena.wav"), relativo="cena.wav", tipo="1"),
    )
    assert comparar(a, b), "mesmo nome em pastas diferentes nao pode ser ignorado"


def test_annotation_001_para_1_e_diferenca(tmp_path):
    """Anotacao e texto: 001 nao e 1, mesmo os dois parecendo numero."""
    a = _salvar(tmp_path / "a.als", _als(annotation="001"))
    b = _salvar(tmp_path / "b.als", _als(annotation="1"))
    [diferenca] = _diferencas_de(comparar(a, b), "/Annotation")
    assert diferenca.classe == VALOR_DIVERGENTE


@pytest.mark.parametrize(
    ("campo", "mudanca"),
    [
        ("/@Time", {"time_atributo": "4096.00004"}),
        ("/CurrentStart", {"current_start": "4096.00004"}),
        ("/Loop/LoopStart", {"loop_start": "4096.00004"}),
        ("/Time", {"time_locator": "4096.00004"}),
        ("/MainTrack/DeviceChain/Mixer/Tempo/Manual", {"tempo": "120.00004"}),
    ],
)
def test_mudanca_de_tempo_e_divergente_sem_regra(tmp_path, campo, mudanca):
    """4096 -> 4096.00004 cabe em 32 bits, mas tempo nao se explica por float32."""
    a = _salvar(tmp_path / "a.als", _als())
    b = _salvar(tmp_path / "b.als", _als(**mudanca))
    [diferenca] = _diferencas_de(comparar(a, b), campo)
    assert diferenca.classe == VALOR_DIVERGENTE
    assert diferenca.regra is None


def test_equivalencia_numerica_exata_em_campo_numerico(tmp_path):
    """Em campo numerico, 4096 e 4096.0 sao o mesmo numero."""
    a = _salvar(tmp_path / "a.als", _als(current_start="4096"))
    b = _salvar(tmp_path / "b.als", _als(current_start="4096.0"))
    assert comparar(a, b) == []


def test_numero_grande_nao_some_por_conversao_para_float(tmp_path):
    """float nao distingue 9007199254740993 de 9007199254740992."""
    a = _salvar(tmp_path / "a.als", _als(current_start="9007199254740993"))
    b = _salvar(tmp_path / "b.als", _als(current_start="9007199254740992"))
    [diferenca] = _diferencas_de(comparar(a, b), "/CurrentStart")
    assert diferenca.classe == VALOR_DIVERGENTE


def test_regra_float32_so_explica_o_volume_do_clip(tmp_path):
    """A compatibilidade de 32 bits fica no SampleVolume, como regra com escopo."""
    a = _salvar(tmp_path / "a.als", _als(volume="0.7071067811865476"))
    b = _salvar(tmp_path / "b.als", _als(volume="0.70710676908493042"))
    [diferenca] = _diferencas_de(comparar(a, b), "/SampleVolume")
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert diferenca.regra and diferenca.regra.startswith("float32")


def test_formato_de_campo_numerico_conhecido_normaliza(tmp_path):
    """Campo declarado quantidade: 4800 e 4800.0 sao o mesmo numero."""
    a = _salvar(tmp_path / "a.als", _als(fade="4800"))
    b = _salvar(tmp_path / "b.als", _als(fade="4800.0"))
    assert comparar(a, b) == []


def test_xml_identico_em_pastas_diferentes_aponta_para_midia_diferente(tmp_path):
    """Mesmo XML, pastas diferentes: o endereco efetivo da midia muda."""
    (tmp_path / "um").mkdir()
    (tmp_path / "dois").mkdir()
    xml = _als(relativo="a.wav", tipo="1")
    a = _salvar(tmp_path / "um" / "a.als", xml)
    b = _salvar(tmp_path / "dois" / "b.als", xml)
    diferencas = comparar(a, b)
    [diferenca] = _diferencas_de(diferencas, "#referencia-efetiva")
    assert diferenca.classe == VALOR_DIVERGENTE
    assert diferenca.gerado.endswith("um/a.wav")
    assert diferenca.devolvido.endswith("dois/a.wav")
    assert len(diferencas) == 1, "o resto do XML e igual: so o endereco diverge"


def test_unc_e_caminho_local_enraizado_nao_sao_o_mesmo_arquivo(tmp_path):
    """``//servidor/compart/a.wav`` nao e ``/servidor/compart/a.wav``."""
    a = _salvar(tmp_path / "a.als", _als(caminho="//s/c/a.wav", relativo="//s/c/a.wav", tipo="1"))
    b = _salvar(tmp_path / "b.als", _als(caminho="//s/c/a.wav", relativo="/s/c/a.wav", tipo="1"))
    [diferenca] = _diferencas_de(comparar(a, b), "/SampleRef/FileRef/RelativePath")
    assert diferenca.classe == VALOR_DIVERGENTE


def test_a_raiz_do_caminho_entra_no_endereco():
    """Raiz, drive e UNC fazem parte do endereco: um nao vira o outro."""
    unc = _normalizar_caminho("//servidor/compart/a.wav")
    local = _normalizar_caminho("/servidor/compart/a.wav")
    assert unc != local, "UNC e caminho local enraizado sao enderecos diferentes"
    assert unc.startswith("//servidor/")
    assert _normalizar_caminho("C:/midia/cena.wav") != _normalizar_caminho("/midia/cena.wav")


def test_relativo_com_raiz_nao_prova_o_mesmo_arquivo(tmp_path):
    """No campo relativo so entra caminho relativo: raiz nao se resolve."""
    a = _salvar(tmp_path / "a.als", _als(relativo="C:cena.wav", tipo="1"))
    b = _salvar(tmp_path / "b.als", _als(relativo="cena.wav", tipo="1"))
    diferencas = comparar(a, b)
    assert _diferencas_de(diferencas, "/SampleRef/FileRef/RelativePath")
    assert not _diferencas_de(diferencas, "#referencia-efetiva")


def test_filho_desconhecido_do_grid_001_para_1_e_diferenca(tmp_path):
    """A grade nao tem tipo declarado: '001' nao e '1'."""
    a = _salvar(tmp_path / "a.als", _als(grid="001"))
    b = _salvar(tmp_path / "b.als", _als(grid="1"))
    [diferenca] = _diferencas_de(comparar(a, b), "/Grid/CampoDesconhecido")
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert not diferenca.escrito


def test_filho_herdado_do_grid_que_some_e_ausente_no_live(tmp_path):
    """A grade vem do template: o Live tira-la nao e defeito do aafton."""
    a = _salvar(tmp_path / "a.als", _als())
    raiz = _als()
    grade = raiz.find("LiveSet/Grid")
    grade.remove(grade.find("CampoDesconhecido"))
    b = _salvar(tmp_path / "b.als", raiz)
    [diferenca] = _diferencas_de(comparar(a, b), "/Grid/CampoDesconhecido")
    assert diferenca.classe == AUSENTE_NO_LIVE
    assert not diferenca.defeito


def test_filho_extra_do_file_ref_aparece_na_comparacao(tmp_path):
    """Filho do FileRef que o Live mexe ou tira nao pode sumir."""
    a = _salvar(tmp_path / "a.als", _als(extra="0"))
    b = _salvar(tmp_path / "b.als", _als(extra="1"))
    [diferenca] = _diferencas_de(comparar(a, b), "/SampleRef/FileRef/OutroValor")
    assert diferenca.classe == RESCRITO_POR_LIVE
    assert not diferenca.escrito
    raiz = _als()
    ref = raiz.find(".//SampleRef/FileRef")
    ref.remove(ref.find("OutroValor"))
    c = _salvar(tmp_path / "c.als", raiz)
    [diferenca] = _diferencas_de(comparar(a, c), "/SampleRef/FileRef/OutroValor")
    assert diferenca.classe == AUSENTE_NO_LIVE
    assert not diferenca.defeito


@pytest.mark.parametrize("tipo", ["999", None], ids=["desconhecido", "ausente"])
def test_tipo_sem_politica_nao_esconde_a_mudanca(tmp_path, tipo):
    """Tipo que a politica nao conhece: a mudanca para o tipo conhecido fica."""
    media = tmp_path / "cena.wav"
    raiz = _als(caminho=str(media), relativo=None, tipo="1")
    ref = raiz.find(".//SampleRef/FileRef")
    if tipo is None:
        ref.remove(ref.find("RelativePathType"))
    else:
        ref.find("RelativePathType").set("Value", tipo)
    a = _salvar(tmp_path / "a.als", raiz)
    b = _salvar(tmp_path / "b.als", _als(caminho=str(media), relativo="cena.wav", tipo="1"))
    diferencas = comparar(a, b)
    assert _diferencas_de(diferencas, "/SampleRef/FileRef/RelativePathType"), "o tipo sumiu"
    assert _diferencas_de(diferencas, "/SampleRef/FileRef/RelativePath")


def test_path_drive_relativo_nao_prova_endereco_absoluto():
    """``C:cena.wav`` e drive-relativo: o Path nao prova endereco absoluto."""
    assert _referencia(_als(caminho="C:cena.wav", relativo=None, tipo="5")) is None


def test_als_relativo_e_absoluto_apontam_para_o_mesmo_endereco(tmp_path, monkeypatch):
    """O mesmo .als por nome relativo e por caminho absoluto: o mesmo endereco."""
    media = tmp_path / "cena.wav"
    a = _salvar(tmp_path / "a.als", _als(caminho=str(media), relativo="cena.wav", tipo="1"))
    monkeypatch.chdir(tmp_path)
    assert comparar("a.als", a) == []
