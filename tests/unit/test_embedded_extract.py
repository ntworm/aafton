# tests/unit/test_embedded_extract.py
"""Testes do caminho de extracao de essencia embedded.

Cinco testes alinhados com o pedido:
1. import --media extract termina com exit 0 e produz .als
2. WAV extraido existe, e RIFF valido e tem duracao esperada
3. .als referencia o arquivo extraido com caminho absoluto
4. import --media link falha de forma clara (sem arquivo externo)
5. Controle de robustez: SourceMob com nome completamente diferente -
   a extracao funciona porque usa mob_id, nao nome
"""

import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.cli import main
from tests.aaf_factory import SAMPLE_RATE, build_embedded_aaf


FRAMES = SAMPLE_RATE  # 1 segundo de audio


@pytest.fixture()
def aaf_embedded(tmp_path):
    """AAF com 1 segundo de essencia RIFF WAV embedded."""
    return build_embedded_aaf(
        tmp_path / "embarcado.aaf",
        frames=FRAMES,
        mob_name="fonte",
        embed_as_riff=True,
    )


def test_import_embedded_com_extract_exit0_e_als(tmp_path, aaf_embedded):
    """import --media extract deve terminar com exit 0 e produzir um .als."""
    als = tmp_path / "saida.als"
    resultado = main(
        [
            "import",
            str(aaf_embedded),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
        ]
    )
    assert resultado == 0, "import --media extract deveria retornar exit 0"
    assert als.exists(), "o arquivo .als deveria ter sido criado"


def test_wav_extraido_valido_e_com_duracao_correta(tmp_path, aaf_embedded):
    """O WAV extraido deve ser RIFF valido com a duracao correta."""
    als = tmp_path / "saida.als"
    main(["import", str(aaf_embedded), str(als), "--media", "extract", "--groups", "none"])

    projeto = tmp_path / "saida Project" / "Samples" / "Imported"
    wavs = list(projeto.glob("*.wav"))
    assert len(wavs) == 1, f"Esperava 1 WAV em {projeto}, encontrou {len(wavs)}: {wavs}"

    wav_path = wavs[0]
    raw = wav_path.read_bytes()
    assert raw[:4] == b"RIFF", f"WAV extraido nao comeca com RIFF: {raw[:4]!r}"
    assert raw[8:12] == b"WAVE", f"RIFF nao e WAVE: {raw[8:12]!r}"

    with wave.open(str(wav_path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE
        assert w.getnframes() == FRAMES, f"Duracao esperada {FRAMES}, lida {w.getnframes()}"


def test_als_referencia_wav_com_caminho_absoluto(tmp_path, aaf_embedded, monkeypatch):
    """O .als gerado deve referenciar o WAV extraido com caminho absoluto,
    mesmo quando o import e chamado com caminho de saida relativo.
    """
    template = Path("templates/live12.als").resolve()
    aaf_abs = aaf_embedded.resolve()

    monkeypatch.chdir(tmp_path)
    als = Path("saida.als")  # Caminho relativo de proposito
    main(
        [
            "import",
            str(aaf_abs),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
            "--template",
            str(template),
        ]
    )

    root = ET.parse(gzip.open(str(als))).getroot()
    caminhos_wav = [
        elem.get("Value", "")
        for elem in root.iter("Path")
        if elem.get("Value", "").lower().endswith(".wav")
    ]

    assert len(caminhos_wav) >= 1, "Nenhum caminho .wav encontrado no .als"
    for caminho in caminhos_wav:
        assert Path(caminho).is_absolute(), f"Esperado caminho absoluto, obtido: {caminho!r}"
        assert Path(caminho).exists(), f"Caminho no .als nao existe: {caminho!r}"


def test_import_embedded_com_link_falha(tmp_path, aaf_embedded):
    """import --media link deve falhar quando a midia e apenas embedded."""
    als = tmp_path / "saida_link.als"
    resultado = main(
        [
            "import",
            str(aaf_embedded),
            str(als),
            "--media",
            "link",
            "--groups",
            "none",
        ]
    )
    assert resultado != 0, "import --media link deveria falhar quando nao ha arquivo externo"


def test_todo_path_no_als_e_absoluto_em_qualquer_modo(tmp_path, aaf_embedded):
    """A invariante do arquivo inteiro: todos os FileRef/SampleRef devem
    ter caminhos absolutos para clips, independente do modo de midia.
    """
    for modo in ["extract", "link"]:
        als = tmp_path / f"saida_{modo}.als"
        main(
            [
                "import",
                str(aaf_embedded),
                str(als),
                "--media",
                modo,
                "--groups",
                "none",
                "--permissive",
            ]
        )

        root = ET.parse(gzip.open(str(als))).getroot()
        for ref_tag in ["SampleRef", "FileRef"]:
            for elem in root.iter(ref_tag):
                path_elem = elem.find("Path")
                if path_elem is not None:
                    caminho = path_elem.get("Value", "")
                    if caminho and caminho.lower().endswith(".wav"):
                        assert Path(caminho).is_absolute(), (
                            f"Path relativo encontrado em {modo}: {caminho!r}"
                        )


def test_ida_e_volta_com_midia_extraida(tmp_path, aaf_embedded):
    """Importar com --media extract e exportar para AAF deve terminar com sucesso."""
    als = tmp_path / "saida.als"
    resultado_import = main(
        [
            "import",
            str(aaf_embedded),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
        ]
    )
    assert resultado_import == 0, "Import falhou"

    aaf_saida = tmp_path / "exportado.aaf"
    resultado_export = main(
        [
            "export",
            str(als),
            str(aaf_saida),
        ]
    )
    assert resultado_export == 0, "Export de projeto com midia extraida deveria funcionar"
    assert aaf_saida.exists()


def test_extracao_funciona_com_nome_do_sourcemob_diferente(tmp_path):
    """Controle: extracao por mob_id, nao por nome.

    O SourceMob tem nome completamente diferente do asset.filename.
    A busca por nome falharia; a busca por mob_id deve funcionar.
    """
    import aaf2
    import aaf2.rational
    from tests.aaf_factory import _wav_summary_header, wav_bytes

    frames = 48000
    aaf_path = tmp_path / "nome_diferente.aaf"
    als = tmp_path / "saida.als"

    essence = wav_bytes(frames)
    summary = _wav_summary_header()

    with aaf2.open(str(aaf_path), "w") as f:
        composition = f.create.CompositionMob("cena_teste")
        f.content.mobs.append(composition)

        master = f.create.MasterMob("dialogo")
        f.content.mobs.append(master)

        descritor = f.create.WAVEDescriptor()
        descritor["SampleRate"].value = aaf2.rational.AAFRational(SAMPLE_RATE, 1)
        descritor["Length"].value = frames
        descritor["Summary"].value = list(summary)

        source = f.create.SourceMob()
        source.name = "NADA_A_VER_COM_DIALOGO"  # nome completamente diferente
        source["EssenceDescription"].value = descritor
        f.content.mobs.append(source)

        ed = f.create.EssenceData()
        ed.mob = source
        stream = ed.open("w")
        stream.write(essence)
        stream.close()
        f.content.essencedata.append(ed)

        sslot = source.create_timeline_slot(edit_rate=SAMPLE_RATE)
        sslot.slot_id = 1
        sslot.name = "A1"
        sslot.segment = f.create.Filler("sound", length=frames)

        mslot = master.create_timeline_slot(edit_rate=SAMPLE_RATE)
        mslot.slot_id = 1
        mslot.name = "A1"
        sc_src = f.create.SourceClip(media_kind="sound", start=0)
        sc_src.length = frames
        sc_src.mob = source
        sc_src.slot_id = 1
        mslot.segment = sc_src

        cslot = composition.create_timeline_slot(edit_rate=SAMPLE_RATE)
        cslot.name = "DIALOGO"
        seq = f.create.Sequence(media_kind="sound")
        cslot.segment = seq
        sc_master = f.create.SourceClip(media_kind="sound", start=0)
        sc_master.length = frames // 2
        sc_master.mob = master
        sc_master.slot_id = 1
        seq.components.append(sc_master)

        tc_slot = composition.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 90000
        tc.length = 1
        tc_slot.segment = tc

    resultado = main(
        [
            "import",
            str(aaf_path),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
        ]
    )
    assert resultado == 0, (
        "A extracao deveria funcionar com SourceMob de nome diferente do asset. "
        "A busca deve ser por mob_id, nao por nome."
    )

    projeto = tmp_path / "saida Project" / "Samples" / "Imported"
    wavs = list(projeto.glob("*.wav"))
    assert len(wavs) == 1, f"Esperava 1 WAV, encontrou {len(wavs)}: {wavs}"


def test_extracao_pcm_puro_gera_wav_valido(tmp_path):
    """Quando a essencia nao tem cabecalho RIFF, deve ser montado como PCM."""
    aaf_path = build_embedded_aaf(tmp_path / "pcm.aaf", frames=24000, embed_as_riff=False)
    als = tmp_path / "saida.als"
    resultado = main(
        [
            "import",
            str(aaf_path),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
        ]
    )
    assert resultado == 0, "import de AAF com PCM puro deveria retornar 0"

    projeto = tmp_path / "saida Project" / "Samples" / "Imported"
    wavs = list(projeto.glob("*.wav"))
    assert len(wavs) == 1, f"Esperava 1 WAV, encontrou {len(wavs)}"

    raw = wavs[0].read_bytes()
    assert raw[:4] == b"RIFF", "WAV gerado de PCM puro nao e RIFF valido"
    with wave.open(str(wavs[0]), "rb") as w:
        assert w.getnframes() == 24000


def test_fixture_embarcado_aaf_leitura_e_extracao(tmp_path):
    """O fixture tests/fixtures/aaf/embarcado.aaf deve ser lido e extraido."""
    fixture = Path(__file__).parent.parent / "fixtures" / "aaf" / "embarcado.aaf"
    assert fixture.exists(), f"Fixture nao encontrado: {fixture}"

    als = tmp_path / "saida.als"
    resultado = main(
        [
            "import",
            str(fixture),
            str(als),
            "--media",
            "extract",
            "--groups",
            "none",
            "--session-text",
            "none",
        ]
    )
    assert resultado == 0, "Fixture embarcado.aaf deveria importar com exit 0"

    projeto = tmp_path / "saida Project" / "Samples" / "Imported"
    wavs = list(projeto.glob("*.wav"))
    assert len(wavs) >= 1, "Nenhum WAV extraido do fixture embarcado.aaf"
