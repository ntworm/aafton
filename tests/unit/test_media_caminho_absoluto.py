"""Caminho da midia resolvido a partir de pasta de busca relativa.

Defeito medido antes da correcao: com a pasta de busca relativa
(``--audio-dir .local/res``), o ``.als`` gravava ``Path`` e ``RelativePath``
iguais a ``.local/res/arquivo.wav``. O Live e o ``aafton export`` procuram
esse caminho relativo a pasta do ``.als`` e nao acham o arquivo.

O AAF que reproduz o defeito vem de outra maquina: o caminho declarado no
locator nao existe no disco, e o arquivo aparece so na pasta de busca. Com a
pasta de busca relativa, o candidato sai dela como caminho relativo e chega
ao ``.als`` como caminho relativo, que ninguem resolve depois.

A correcao fica em ``media_extract.resolve_asset``: o caminho guardado no
asset e o absoluto, para que o endereco do ``.als`` nao dependa do diretorio
de trabalho de quem rodou a conversao.
"""

from __future__ import annotations

import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.cli import main
from aafton.ir import MediaAsset
from aafton.media_extract import resolve_asset
from aafton.report import Report
from tests.aaf_factory import build_linked_aaf, wav_bytes

RAIZ_DO_REPO = Path(__file__).resolve().parents[2]
TEMPLATE = RAIZ_DO_REPO / "templates" / "live12.als"
NOME_DO_WAV = "cena12_tk3.wav"
FRAMES = 48000


def _raiz_do_als(caminho: Path) -> ET.Element:
    """Le a raiz do .als, que o Live guarda compactado em gzip."""
    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(2)
    if inicio == b"\x1f\x8b":
        with gzip.open(caminho, "rb") as arquivo:
            return ET.parse(arquivo).getroot()
    return ET.parse(caminho).getroot()


def _endereco_do_clip(caminho: Path, nome: str) -> tuple[str, str]:
    """``Path`` e ``RelativePath`` do clip que aponta para o arquivo de nome dado."""
    for clip in _raiz_do_als(caminho).iter("AudioClip"):
        referencia = clip.find("SampleRef")
        if referencia is None:
            continue
        arquivo = referencia.find("FileRef")
        if arquivo is None:
            continue
        elemento = arquivo.find("Path")
        valor = "" if elemento is None else (elemento.get("Value") or "")
        if not valor.replace("\\", "/").endswith(nome):
            continue
        relativo = arquivo.find("RelativePath")
        return valor, "" if relativo is None else (relativo.get("Value") or "")
    raise AssertionError(f"nenhum clip do .als aponta para {nome}")


def _codigos(relatorio: Path) -> list[str]:
    """Codigos de issue do relatorio JSON da conversao."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def _aaf_de_outra_maquina(tmp_path: Path, wav: Path) -> None:
    """Grava ``origem.aaf`` com um locator que nao existe nesta maquina.

    O arquivo de midia some do caminho declarado e passa a existir so no
    destino final: e assim que o candidato sai da pasta de busca.
    """
    declarado = tmp_path / "de_outra_maquina" / wav.name
    declarado.parent.mkdir(parents=True, exist_ok=True)
    declarado.write_bytes(wav_bytes(frames=FRAMES))
    build_linked_aaf(Path("origem.aaf"), [(0, FRAMES, 0)], declarado)
    declarado.unlink()
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(wav_bytes(frames=FRAMES))


def test_resolve_asset_com_pasta_de_busca_relativa(tmp_path, monkeypatch):
    """Com a pasta de busca relativa, o caminho guardado no asset e absoluto."""
    monkeypatch.chdir(tmp_path)
    wav = tmp_path / "res" / NOME_DO_WAV
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(wav_bytes(frames=FRAMES))

    asset = MediaAsset(id="m1", filename=NOME_DO_WAV, declared_location=NOME_DO_WAV)
    resolve_asset(asset, [Path("res")], Report(strict=True))

    assert asset.status == "resolved"
    resolvido = Path(asset.resolved_path)
    assert resolvido.is_absolute()
    assert resolvido.exists()
    assert resolvido.samefile(wav)


def test_import_com_audio_dir_relativo_grava_caminho_absoluto(tmp_path, monkeypatch):
    """Import com ``--audio-dir`` relativo: o .als aponta para o caminho absoluto."""
    monkeypatch.chdir(tmp_path)
    wav = tmp_path / ".local" / "res" / NOME_DO_WAV
    _aaf_de_outra_maquina(tmp_path, wav)
    (tmp_path / "out").mkdir(exist_ok=True)
    relatorio = tmp_path / "relatorio.json"

    codigo = main(
        [
            "import",
            "origem.aaf",
            "out/pt.als",
            "--template",
            str(TEMPLATE),
            "--audio-dir",
            ".local/res",
            "--report",
            str(relatorio),
        ]
    )
    assert codigo == 0
    assert "MEDIA_MISSING" not in _codigos(relatorio)

    als = tmp_path / "out" / "pt.als"
    caminho, relativo = _endereco_do_clip(als, NOME_DO_WAV)
    assert Path(caminho).is_absolute()
    assert Path(caminho).exists()
    assert Path(caminho).samefile(wav)
    assert Path(als.parent / relativo).samefile(wav)

    relatorio_export = tmp_path / "relatorio_export.json"
    codigo_export = main(
        [
            "export",
            "out/pt.als",
            "saida.aaf",
            "--report",
            str(relatorio_export),
        ]
    )
    assert codigo_export == 0
    assert "MEDIA_MISSING" not in _codigos(relatorio_export)


def test_import_com_aaf_relativo_grava_caminho_absoluto(tmp_path, monkeypatch):
    """AAF e midia por caminho relativo: o .als aponta para o caminho absoluto."""
    monkeypatch.chdir(tmp_path)
    wav = tmp_path / NOME_DO_WAV
    _aaf_de_outra_maquina(tmp_path, wav)
    (tmp_path / "out").mkdir(exist_ok=True)
    relatorio = tmp_path / "relatorio.json"

    codigo = main(
        [
            "import",
            "origem.aaf",
            "out/pt.als",
            "--template",
            str(TEMPLATE),
            "--report",
            str(relatorio),
        ]
    )
    assert codigo == 0
    assert "MEDIA_MISSING" not in _codigos(relatorio)

    als = tmp_path / "out" / "pt.als"
    caminho, relativo = _endereco_do_clip(als, NOME_DO_WAV)
    assert Path(caminho).is_absolute()
    assert Path(caminho).exists()
    assert Path(caminho).samefile(wav)
    assert Path(als.parent / relativo).samefile(wav)

    relatorio_export = tmp_path / "relatorio_export.json"
    codigo_export = main(
        [
            "export",
            "out/pt.als",
            "saida.aaf",
            "--report",
            str(relatorio_export),
        ]
    )
    assert codigo_export == 0
    assert "MEDIA_MISSING" not in _codigos(relatorio_export)
