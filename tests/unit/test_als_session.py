from pathlib import Path
import pytest
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als, GLOBAL_ID_FLOOR, validate_document
from aafton.ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)

UMA_HORA = 172800000


def _sessao(tmp_path):
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(b"RIFF")
    asset = MediaAsset(
        id="m1",
        filename="cena12_tk3.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav),
    )
    tracks = []
    for indice, nome in enumerate(["DIALOGO 1", "DIALOGO 2", "AMBIENTE"]):
        track = TrackIR(
            id=f"t{indice}", source_index=indice, name=nome, kind="audio", channel_count=1
        )
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id=track.id,
                channel_index=0,
                event_index=0,
                name="cena12_tk3.wav",
                media_id="m1",
                timeline_start_samples=UMA_HORA + indice * 48000,
                timeline_end_samples=UMA_HORA + indice * 48000 + 96000,
                source_offset_samples=0,
            )
        )
        tracks.append(track)
    return SessionIR(
        name="cena12",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        media=[asset],
        tracks=tracks,
        markers=[MarkerIR(id="k1", name="corte seco", position_samples=UMA_HORA + 240000)],
    )


def test_arquivo_gerado_passa_na_validacao(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    validate_document(load_als(destino), frozenset(["ClipSlot"]))  # nao levanta


def test_clip_slots_continuam_em_indice_pequeno(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    slots = [int(n.get("Id")) for n in load_als(destino).iter("ClipSlot") if n.get("Id")]
    assert slots, "o template deveria ter ClipSlot, senao este teste nao prova nada"
    assert max(slots) < GLOBAL_ID_FLOOR


def test_clips_do_arranjo_ficam_em_zero_a_n_menos_um(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    for eventos in load_als(destino).iter("Events"):
        ids = [int(n.get("Id")) for n in eventos if n.tag == "AudioClip"]
        if ids:
            assert sorted(ids) == list(range(len(ids)))


def test_locators_ficam_em_zero_a_n_menos_um(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    ids = [int(n.get("Id")) for n in load_als(destino).iter("Locator")]
    assert sorted(ids) == list(range(len(ids)))


def test_cada_track_tem_id_proprio_e_pequeno(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    ids = [int(n.get("Id")) for n in load_als(destino).iter("AudioTrack")]
    assert len(ids) == len(set(ids))
    assert all(i < 1000 for i in ids)


def test_nenhuma_track_aponta_para_grupo_inexistente(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    root = load_als(destino)
    grupos = {n.get("Id") for n in root.iter("GroupTrack")}
    for track in root.iter("AudioTrack"):
        alvo = track.find("TrackGroupId")
        if alvo is not None and alvo.get("Value") != "-1":
            assert alvo.get("Value") in grupos


def test_nenhum_envelope_aponta_para_objeto_removido(tmp_path):
    destino = tmp_path / "saida.als"
    report = Report(strict=True)
    write_session(_sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), report)
    root = load_als(destino)
    presentes = {n.get("Id") for n in root.iter() if n.get("Id")}
    for alvo in root.iter("PointeeId"):
        assert alvo.get("Value") in presentes


def test_nenhuma_saida_aponta_para_grupo_removido(tmp_path):
    """A causa do crash fatal do Live: track que saiu do grupo mas manteve
    AudioOutputRouting/Target = "AudioOut/GroupTrack". O destino deixa de existir
    e o Live fecha com "A serious program error has occurred", sem mensagem util.
    A referencia e por nome de destino, entao nenhuma checagem de Id a pega."""
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    root = load_als(destino)
    grupos_existentes = list(root.iter("GroupTrack"))
    for track in root.iter("AudioTrack"):
        alvo = track.find("DeviceChain/AudioOutputRouting/Target")
        assert alvo is not None
        if alvo.get("Value") == "AudioOut/GroupTrack":
            assert grupos_existentes, (
                f"a track {track.get('Id')} manda audio para um GroupTrack, "
                f"mas o documento nao tem nenhum"
            )


def test_o_prototipo_do_template_de_fato_vinha_de_um_grupo(tmp_path):
    """Se o template mudar e a track prototipo deixar de estar num grupo, o teste
    acima passa a nao provar nada. Este falha para avisar."""
    prototipo = load_als(TEMPLATE).find(".//LiveSet/Tracks/AudioTrack")
    alvo = prototipo.find("DeviceChain/AudioOutputRouting/Target")
    assert alvo is not None and alvo.get("Value") == "AudioOut/GroupTrack", (
        "a AudioTrack prototipo do template nao vem mais de dentro de um "
        "GroupTrack; o teste de regressao de roteamento perdeu o valor"
    )


def test_nome_da_track_chega_no_user_name(tmp_path):
    """EffectiveName e derivado: o Live o recalcula ao carregar. Gravar so ele faz
    a track aparecer como "1-Audio" em vez do nome vindo do AAF."""
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    nomes = [t.find("Name/UserName").get("Value") for t in load_als(destino).iter("AudioTrack")]
    assert "DIALOGO 1" in nomes


def test_atributo_time_do_clip_acompanha_current_start(tmp_path):
    """O Live posiciona o clip pelo atributo Time do elemento <AudioClip>, nao
    pelo filho CurrentStart. Deixar o Time do prototipo faz o Live calcular
    comprimento negativo e apagar o clip, registrando no Log.txt
    "Delete clip because its length is too small"."""
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    clips = list(load_als(destino).iter("AudioClip"))
    assert clips
    for clip in clips:
        assert clip.get("Time") is not None
        assert float(clip.get("Time")) == float(clip.find("CurrentStart").get("Value"))
        assert float(clip.get("Time")) < float(clip.find("CurrentEnd").get("Value"))


def test_nenhum_clip_aponta_para_o_arquivo_do_template(tmp_path):
    """OriginalFileRef guarda uma segunda copia da referencia; se ela ficar
    apontando para o sample do template, o Live registra "could not be opened"."""
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path), TEMPLATE, destino, WriteOptions(sample_rate=48000), Report(strict=True)
    )
    for clip in load_als(destino).iter("AudioClip"):
        for caminho in clip.iter("Path"):
            assert "Core Library" not in (caminho.get("Value") or "")


def test_sem_envelope_de_tempo_o_bpm_manual_vale(tmp_path):
    """Um envelope de automacao de tempo sobrepoe o BPM manual: o set abria a
    134.999 mesmo com Manual=60 gravado."""
    destino = tmp_path / "saida.als"
    write_session(
        _sessao(tmp_path),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60),
        Report(strict=True),
    )
    root = load_als(destino)
    alvos = {
        n.get("Id")
        for main in root.iter("MainTrack")
        for tempo in main.iter("Tempo")
        for n in tempo.iter("AutomationTarget")
        if n.get("Id")
    }
    for ponteiro in root.iter("PointeeId"):
        assert ponteiro.get("Value") not in alvos, "sobrou envelope de tempo"
    manual = root.find(".//MainTrack//Tempo/Manual")
    assert float(manual.get("Value")) == 60.0


def test_sessao_com_muitos_clips_na_mesma_track_passa_na_validacao(tmp_path):
    clips = []
    media = []
    for i in range(150):
        clips.append(
            ClipIR(
                id=f"c{i}",
                track_id="t1",
                channel_index=0,
                event_index=i,
                name=f"clip {i}",
                media_id=f"a{i}",
                timeline_start_samples=UMA_HORA + i * 48000,
                timeline_end_samples=UMA_HORA + i * 48000 + 48000,
                source_offset_samples=0,
            )
        )
        media.append(MediaAsset(id=f"a{i}", filename="teste.wav", channels=1, sample_rate=48000))
    sessao = SessionIR(
        name="gigante",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[
            TrackIR(
                id="t1", name="gigante", kind="audio", channel_count=1, source_index=0, clips=clips
            )
        ],
        media=media,
        markers=[],
    )
    destino = tmp_path / "gigante.als"
    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, permissive=True),
        Report(strict=False),
    )
    validate_document(
        load_als(destino), frozenset(["ClipSlot", "AudioClip", "WarpMarker", "FileRef"])
    )


def test_escrever_sessao_mantem_ids_de_tracks_unicos(tmp_path):
    sessao = SessionIR(
        name="multi",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[
            TrackIR(
                id="t1", name="track1", kind="audio", channel_count=1, source_index=0, clips=[]
            ),
            TrackIR(
                id="t2", name="track2", kind="audio", channel_count=1, source_index=1, clips=[]
            ),
            TrackIR(
                id="t3", name="track3", kind="audio", channel_count=1, source_index=2, clips=[]
            ),
        ],
        media=[],
        markers=[],
    )
    destino = tmp_path / "multi.als"
    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, permissive=True, groups="none"),
        Report(strict=False),
    )

    root = load_als(destino)
    ids_de_track = [
        int(t.get("Id"))
        for t in root.findall(".//LiveSet/Tracks/*")
        if (t.get("Id") or "").lstrip("-").isdigit()
    ]
    # Nao pode haver IDs repetidos
    assert len(ids_de_track) == len(set(ids_de_track))
    # E temos as 3 novas + 1 preservada (ReturnTrack)
    assert len(ids_de_track) == 4


def _sessao_grupo() -> SessionIR:
    return SessionIR(
        name="grupos",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[
            TrackIR(id="t1", name="SFX 1", kind="audio", channel_count=1, source_index=0),
            TrackIR(id="t2", name="Corta relva", kind="audio", channel_count=1, source_index=1),
            TrackIR(id="t3", name="SFX 2", kind="audio", channel_count=1, source_index=2),
            TrackIR(id="t4", name="Audio 1", kind="audio", channel_count=1, source_index=3),
            TrackIR(id="t5", name="SFX 3", kind="audio", channel_count=1, source_index=4),
            TrackIR(id="t6", name="Audio 2", kind="audio", channel_count=1, source_index=5),
        ],
        media=[],
        markers=[],
    )


def test_agrupamento_reordena_membros_contiguos(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao_grupo(),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )

    root = load_als(destino)
    tracks = root.findall(".//LiveSet/Tracks/*")
    # IDs precisam ser unicos apos reordenacao
    ids = [t.get("Id") for t in tracks if t.get("Id")]
    assert len(ids) == len(set(ids))

    # Vamos ver a ordem dos filhos em LiveSet/Tracks (ignorando as originais ReturnTrack/MainTrack)
    nomes = []
    for t in tracks:
        if t.tag in ("AudioTrack", "GroupTrack"):
            # Para AudioTrack, pega UserName. Para GroupTrack, pega EffectiveName ou UserName
            nome = t.find(".//UserName")
            if nome is not None:
                nomes.append(nome.get("Value"))

    # Ordem esperada:
    # 1. GroupTrack SFX
    # 2. SFX 1
    # 3. SFX 2
    # 4. SFX 3
    # 5. Corta relva (solta)
    # 6. GroupTrack Audio
    # 7. Audio 1
    # 8. Audio 2
    assert nomes == ["SFX", "SFX 1", "SFX 2", "SFX 3", "Corta relva", "Audio", "Audio 1", "Audio 2"]


def test_tracks_soltas_tem_trackgroupid_menos_um_e_saida_main(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao_grupo(),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )
    root = load_als(destino)
    corta_relva = None
    for t in root.findall(".//LiveSet/Tracks/AudioTrack"):
        if t.find(".//UserName").get("Value") == "Corta relva":
            corta_relva = t
            break
    assert corta_relva is not None
    assert corta_relva.find("TrackGroupId").get("Value") == "-1"
    alvo_routing = corta_relva.find("DeviceChain/AudioOutputRouting/Target")
    assert alvo_routing.get("Value") == "AudioOut/Main"


def test_membros_tem_trackgroupid_e_saida_group(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao_grupo(),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )
    root = load_als(destino)
    sfx_group_id = None
    for t in root.findall(".//LiveSet/Tracks/GroupTrack"):
        if t.find(".//UserName").get("Value") == "SFX":
            sfx_group_id = t.get("Id")
            break

    assert sfx_group_id is not None
    for t in root.findall(".//LiveSet/Tracks/AudioTrack"):
        if "SFX" in t.find(".//UserName").get("Value"):
            assert t.find("TrackGroupId").get("Value") == sfx_group_id
            alvo = t.find("DeviceChain/AudioOutputRouting/Target")
            assert alvo.get("Value") == "AudioOut/GroupTrack"


def test_groups_none_nao_cria_grouptrack_e_nao_reordena(tmp_path):
    destino = tmp_path / "saida.als"
    write_session(
        _sessao_grupo(),
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="none"),
        Report(strict=False),
    )
    root = load_als(destino)

    assert len(root.findall(".//LiveSet/Tracks/GroupTrack")) == 0
    nomes = [
        t.find(".//UserName").get("Value") for t in root.findall(".//LiveSet/Tracks/AudioTrack")
    ]
    assert nomes == ["SFX 1", "Corta relva", "SFX 2", "Audio 1", "SFX 3", "Audio 2"]

    for t in root.findall(".//LiveSet/Tracks/AudioTrack"):
        assert t.find("TrackGroupId").get("Value") == "-1"


def test_agrupamento_sufixo_canal_e_renomeacao(tmp_path):
    from aafton.als_write import WriteOptions, write_session
    from aafton.als_read import load_als
    from aafton.report import Report
    from aafton.ir import SessionIR, TrackIR
    from aafton.timecode import TimecodeRate

    sessao = SessionIR(
        name="complexo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[
            TrackIR(id="t1", name="5.1 [L]", kind="audio", channel_count=1, source_index=0),
            TrackIR(id="t2", name="5.1 [R]", kind="audio", channel_count=1, source_index=1),
            TrackIR(id="t3", name="5.1 [C]", kind="audio", channel_count=1, source_index=2),
            TrackIR(id="t4", name="5.1 [LFE]", kind="audio", channel_count=1, source_index=3),
            TrackIR(id="t5", name="5.1 [Ls]", kind="audio", channel_count=1, source_index=4),
            TrackIR(id="t6", name="5.1 [Rs]", kind="audio", channel_count=1, source_index=5),
            TrackIR(id="t7", name="AMB 1ST [W]", kind="audio", channel_count=1, source_index=6),
            TrackIR(id="t8", name="AMB 1ST [X]", kind="audio", channel_count=1, source_index=7),
            TrackIR(id="t9", name="AMB 1ST [Y]", kind="audio", channel_count=1, source_index=8),
            TrackIR(id="t10", name="AMB 1ST [Z]", kind="audio", channel_count=1, source_index=9),
            TrackIR(id="t11", name="Bx 1", kind="audio", channel_count=1, source_index=10),
            TrackIR(id="t12", name="Bx 2", kind="audio", channel_count=1, source_index=11),
            TrackIR(id="t13", name="Bx 3 [L]", kind="audio", channel_count=1, source_index=12),
            TrackIR(id="t14", name="Bx 3 [R]", kind="audio", channel_count=1, source_index=13),
            TrackIR(id="t15", name="Bx 4 [L]", kind="audio", channel_count=1, source_index=14),
            TrackIR(id="t16", name="Isolada [L]", kind="audio", channel_count=1, source_index=15),
        ],
        media=[],
        markers=[],
    )

    destino = tmp_path / "saida.als"
    from pathlib import Path

    TEMPLATE = Path("templates/live12.als")
    write_session(
        sessao,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )

    root = load_als(destino)
    tracks = list(root.find(".//LiveSet/Tracks"))

    grupos = [t for t in tracks if t.tag == "GroupTrack"]
    nomes_grupos = [g.find(".//UserName").get("Value") for g in grupos]
    assert "5.1" in nomes_grupos
    assert "AMB 1ST" in nomes_grupos
    assert "Bx" in nomes_grupos

    current_group_id = None
    membros = 0
    for t in tracks:
        if t.tag == "GroupTrack":
            current_group_id = t.get("Id")
            membros = 0
        elif t.tag == "AudioTrack":
            alvo = t.find("TrackGroupId")
            if alvo is not None and alvo.get("Value") == current_group_id:
                membros += 1
            else:
                if current_group_id is not None:
                    nome_grupo = next(
                        g.find(".//UserName").get("Value")
                        for g in grupos
                        if g.get("Id") == current_group_id
                    )
                    if nome_grupo == "5.1":
                        assert membros == 6
                    elif nome_grupo == "AMB 1ST":
                        assert membros == 4
                    elif nome_grupo == "Bx":
                        assert membros == 5
                    current_group_id = None
                    membros = 0

    if current_group_id is not None:
        nome_grupo = next(
            g.find(".//UserName").get("Value") for g in grupos if g.get("Id") == current_group_id
        )
        if nome_grupo == "5.1":
            assert membros == 6
        elif nome_grupo == "AMB 1ST":
            assert membros == 4
        elif nome_grupo == "Bx":
            assert membros == 5

    isolada = next(
        t
        for t in tracks
        if t.tag == "AudioTrack" and t.find(".//UserName").get("Value") == "Isolada [L]"
    )
    assert isolada.find("TrackGroupId").get("Value") == "-1"
