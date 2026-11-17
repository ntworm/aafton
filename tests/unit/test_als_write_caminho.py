"""O set escrito aponta a midia que esta dentro da pasta dele por caminho relativo.

O Live relativiza a midia depois do Save As: o ``FileRef`` passa a ter
``RelativePathType`` 1 e o ``RelativePath`` relativo a pasta do .als. Com o
caminho absoluto, uma pasta do set copiada para outro lugar deixa de achar os
arquivos que ela leva junto. A midia de fora (o caso do ``--media link``)
continua com o caminho absoluto, porque o Live precisa acha-la no lugar de
sempre.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_paths import find_prototype
from aafton.als_write import WriteOptions, build_audio_clip, write_session
from aafton.als_write_caminho import aplicar_caminho_relativo
from aafton.als_xml import IdAllocator, classify_id_tags, get_value, load_als
from aafton.collect_als import resolve_file_ref
from aafton.ir import ClipIR, MediaAsset
from aafton.report import Report

from tests.unit.test_aaf_write_embed import RATE, _asset, _sessao, _wav

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture
def contexto():
    root = load_als(TEMPLATE)
    posicionais, unicos = classify_id_tags(root)
    prototipo = find_prototype(root, "AudioClip")
    return IdAllocator.from_root(root), prototipo, posicionais, unicos


def _clip_do_contexto(contexto, wav: Path) -> ET.Element:
    """Clip do Live com a midia do arquivo, montado como o escritor monta."""
    allocator, prototipo, posicionais, unicos = contexto
    asset = MediaAsset(
        id="m1",
        filename=wav.name,
        channels=1,
        sample_rate=RATE,
        duration_samples=480,
        status="resolved",
        resolved_path=str(wav),
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name=wav.name,
        media_id="m1",
        timeline_start_samples=0,
        timeline_end_samples=480,
        source_offset_samples=0,
    )
    return build_audio_clip(
        clip,
        asset,
        prototipo,
        allocator,
        posicionais,
        unicos,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )


def _file_ref_do_set(als: Path) -> ET.Element:
    """O FileRef do primeiro clip do set escrito."""
    root = load_als(als)
    file_ref = root.find(".//AudioClip/SampleRef/FileRef")
    assert file_ref is not None, "o set escrito nao tem clip com midia"
    return file_ref


def _wav_dentro(tmp_path: Path) -> tuple[Path, Path]:
    """WAV dentro da pasta do set, no lugar que o importador extrai."""
    als_dir = tmp_path / "projeto"
    media = als_dir / "Samples" / "Imported"
    media.mkdir(parents=True)
    wav = media / "x.wav"
    _wav(wav, frames=480)
    return als_dir, wav


def test_midia_dentro_da_pasta_do_set_vira_caminho_relativo(tmp_path, contexto):
    als_dir, wav = _wav_dentro(tmp_path)

    node = _clip_do_contexto(contexto, wav)
    aplicar_caminho_relativo(node, str(wav), als_dir)

    file_ref = node.find("SampleRef/FileRef")
    assert get_value(file_ref, "RelativePathType") == "1"
    assert get_value(file_ref, "RelativePath") == "Samples/Imported/x.wav"
    # O Path absoluto fica: e ele que o Live consulta com o set no lugar.
    absoluto = get_value(file_ref, "Path")
    assert Path(absoluto).is_absolute()
    assert absoluto.endswith("x.wav")


def test_midia_fora_da_pasta_do_set_fica_como_esta(tmp_path, contexto):
    als_dir = tmp_path / "projeto"
    als_dir.mkdir()
    wav = tmp_path / "fora" / "x.wav"
    wav.parent.mkdir()
    _wav(wav, frames=480)

    node = _clip_do_contexto(contexto, wav)
    antes = ET.tostring(node.find("SampleRef/FileRef"))
    aplicar_caminho_relativo(node, str(wav), als_dir)
    assert ET.tostring(node.find("SampleRef/FileRef")) == antes


def test_sem_pasta_do_set_o_file_ref_fica_como_esta(tmp_path, contexto):
    wav = tmp_path / "x.wav"
    _wav(wav, frames=480)

    node = _clip_do_contexto(contexto, wav)
    antes = ET.tostring(node.find("SampleRef/FileRef"))
    aplicar_caminho_relativo(node, str(wav), None)
    assert ET.tostring(node.find("SampleRef/FileRef")) == antes


def test_saida_com_caminho_relativo_gera_o_caminho_relativo_da_midia(tmp_path, monkeypatch):
    _, wav = _wav_dentro(tmp_path)
    # O template sai do lugar com o cwd: o caminho dele fica absoluto antes.
    template = TEMPLATE.resolve()
    monkeypatch.chdir(tmp_path)

    write_session(
        _sessao([_asset("m", wav, 1, 480)], clips=[("m", 0, 480, 0)]),
        template,
        Path("projeto") / "set.als",
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )

    file_ref = _file_ref_do_set(Path("projeto") / "set.als")
    assert get_value(file_ref, "RelativePathType") == "1"
    assert get_value(file_ref, "RelativePath") == "Samples/Imported/x.wav"


def test_pasta_do_set_copiada_acha_a_midia(tmp_path):
    als_dir, wav = _wav_dentro(tmp_path)
    als = als_dir / "set.als"

    write_session(
        _sessao([_asset("m", wav, 1, 480)], clips=[("m", 0, 480, 0)]),
        TEMPLATE,
        als,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )

    file_ref = _file_ref_do_set(als)
    assert get_value(file_ref, "RelativePathType") == "1"
    assert get_value(file_ref, "RelativePath") == "Samples/Imported/x.wav"

    copia = tmp_path / "copia"
    shutil.copytree(als_dir, copia)
    als_dir.rename(tmp_path / "antigo")

    copiado = _file_ref_do_set(copia / "set.als")
    status, resolvido, _ = resolve_file_ref(copiado, copia / "set.als")
    assert status == "resolved"
    assert resolvido == (copia / "Samples" / "Imported" / "x.wav").resolve()
