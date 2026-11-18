"""Escrita de arquivos AAF a partir da representacao intermediaria.

Implementa os mobs minimos exigidos pelos NLEs:
- CompositionMob (a timeline)
- MasterMob (o asset logico, referenciado pelo CompositionMob)
- SourceMob (o arquivo no disco, referenciado pelo MasterMob via SourceClip interno)
"""

import tempfile
from pathlib import Path

import aaf2

from .ir import SessionIR
from .ir_trim import trim_to_media_end
from .report import Report
from .timecode import TimecodeRate, samples_to_frames_exact

from .aaf_write_check import (
    _iter_source_clips as _iter_source_clips,
    _mob_com_descritor as _mob_com_descritor,
    check_exported_aaf as check_exported_aaf,
)
from .aaf_write_preflight import (
    _is_supported_transition as _is_supported_transition,
    _validate_session_overlaps as _validate_session_overlaps,
)
from .aaf_write_defs import (
    DEFAULT_COMPOSITION_NAME,
    AafBuilder,
    _AAF_INT16_TYPE_AUID as _AAF_INT16_TYPE_AUID,
    _AUDIO_CHANNEL_COMBINER_AUID as _AUDIO_CHANNEL_COMBINER_AUID,
    _AUDIO_CHANNEL_COMBINER_EFFECT_ID as _AUDIO_CHANNEL_COMBINER_EFFECT_ID,
    _AVID_BAG_OF_BITS_TYPE_AUID as _AVID_BAG_OF_BITS_TYPE_AUID,
    _AVID_EFFECT_ID_AUID as _AVID_EFFECT_ID_AUID,
    _AVID_PARAMETER_BYTE_ORDER_AUID as _AVID_PARAMETER_BYTE_ORDER_AUID,
    _MONO_AUDIO_PAN_AUID as _MONO_AUDIO_PAN_AUID,
    _PAN_PARAMETER_AUID as _PAN_PARAMETER_AUID,
)
from .aaf_write_media import (
    MobResolver as _MobResolver,
    _split_stereo_embed as _split_stereo_embed,
    checar_formatos_media as _checar_formatos_media,
    link_media as _link_media,
    min_duration_from_clips as _min_duration_from_clips,
)
from .aaf_write_embed import (
    check_embed_size,
    embed_payload_size,
    preflight_embed,
    verify_embedded_aaf,
)
from .aaf_write_fades import reportar_fades
from .aaf_write_gain import _ganho_em as _ganho_em
from .aaf_write_labels import rotular_mastermobs
from .aaf_write_tracks import (
    _KNOWN_LAYOUT_SUFFIXES as _KNOWN_LAYOUT_SUFFIXES,
    _LAYOUT_SUFFIX_RE as _LAYOUT_SUFFIX_RE,
    TrackContext,
    _strip_channel_suffix as _strip_channel_suffix,
    clips_pulados_da_track,
    write_markers,
    write_tracks,
)
from .aaf_write_timecode import frame_de_inicio


def _curva_suportada() -> bool:
    """Diz se esta instalacao do pyaaf2 tem as definicoes necessarias para VaryingValue.

    Verifica se a classe VaryingValue declara as propriedades obrigatorias
    ``Interpolation`` e ``PointList`` e se ``InterpolationDefinition`` esta
    presente no dicionario de classes do pyaaf2.
    """
    try:
        from aaf2.model.classdefs import classdefs
    except ImportError:
        return False
    variando = classdefs.get("VaryingValue")
    definicao = classdefs.get("InterpolationDefinition")
    if not variando or not definicao:
        return False
    return "Interpolation" in variando[3] and "PointList" in variando[3]


def _reportar_pan(track, report: Report) -> None:
    """Reporta o suporte (ou a falta dele) a pan estatico ou automacao de pan de uma track."""
    _reportar_pan_estatico(track, report)
    _reportar_pan_automacao(track, report)


def _reportar_pan_estatico(track, report: Report) -> None:
    """Avisa quando o pan estatico de track estereo/multicanal nao entra no AAF."""
    if getattr(track, "channel_count", 1) <= 1:
        return
    pan_val = getattr(track, "pan", 0.0)
    try:
        val = float(pan_val)
    except (TypeError, ValueError, OverflowError):
        val = 0.0
    if abs(val) > 1e-6:
        code = (
            "WRITE_STEREO_PAN_UNSUPPORTED"
            if track.channel_count == 2
            else "WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED"
        )
        report.warning(
            code,
            f"static pan of {track.name!r} ({track.channel_count} channels, pan={val:.3f}) "
            "is not written in the AAF; only mono tracks support pan in the AAF",
            {"track": track.name, "channels": track.channel_count, "pan": val},
        )


def _reportar_pan_automacao(track, report: Report) -> None:
    """Reporta o suporte (ou a falta dele) a automacao de pan de uma track."""
    if not track.pan_points:
        return
    if track.channel_count != 1:
        report.error(
            "WRITE_PAN_AUTOMATION_UNSUPPORTED",
            f"pan automation in the track {track.name!r} ({track.channel_count} channels) is not supported; only mono tracks support Mono Audio Pan",
            {"track": track.name, "channels": track.channel_count},
        )
    elif _curva_suportada():
        report.info(
            "PAN_AUTOMATION_EXPORTED",
            f"pan automation of {track.name!r} exported to AAF with VaryingValue",
            {"track": track.name, "points": len(track.pan_points)},
        )
    else:
        contexto = {
            "track": track.name,
            "dependency": "pyaaf2",
            "property": "VaryingValue.Interpolation",
            "cause": "the installed pyaaf2 lacks the definitions VaryingValue needs",
        }
        report.error(
            "WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING",
            f"the pan automation of {track.name!r} cannot become a curve in the AAF: {contexto['cause']}",
            contexto,
        )


def _transicoes_consumidas(track, pulados: set[int]) -> tuple[set[int], set[int]]:
    """Fades simetricos que viram Transitions, por canal da track.

    Cada clip vai ao AAF inteiro, com o proprio envelope de ganho, entao a
    automacao dentro do crossfade nao impede a Transition.

    O par e formado pelos clips que o writer escreve: o clip que fica fora do
    AAF (`pulados`) nao faz par com ninguem, e os seus vizinhos fazem par um
    com o outro.
    """
    consumed_in: set[int] = set()
    consumed_out: set[int] = set()
    for channel in {c.channel_index for c in track.clips}:
        ordered = sorted(
            (c for c in track.clips if c.channel_index == channel and id(c) not in pulados),
            key=lambda c: c.timeline_start_samples,
        )
        for previous, current in zip(ordered, ordered[1:]):
            overlap = previous.timeline_end_samples - current.timeline_start_samples
            if overlap > 0 and _is_supported_transition(previous, current, overlap):
                consumed_out.add(id(previous))
                consumed_in.add(id(current))
    return consumed_in, consumed_out


def _preflight(session: SessionIR, report: Report) -> tuple[TimecodeRate, set[int], set[int]]:
    """Checagens de antes da escrita: rate do timecode, overlaps, features e fades."""
    if session.timecode_rate is None:
        report.error("WRITE_NO_TIMECODE_RATE", "Session has no timecode_rate defined", {})
        rate = TimecodeRate(24, 1, False)  # Fallback se nao for estrito
    else:
        rate = session.timecode_rate

    _validate_session_overlaps(session, report)
    for message in session.unsupported_export_features:
        report.error("WRITE_SESSION_FEATURE_UNSUPPORTED", message, {})

    consumed_in: set[int] = set()
    consumed_out: set[int] = set()
    for track in session.tracks:
        if track.is_video:
            continue
        _reportar_pan(track, report)
        # Fades simetricos compoem Transitions; fades independentes viram envelopes de ganho.
        # Cada track nao-video tem os seus conjuntos; a uniao cobre todas, porque
        # os ids sao o id() dos objetos ClipIR e nenhum clip esta em duas tracks.
        pulados = clips_pulados_da_track(track, session)
        track_in, track_out = _transicoes_consumidas(track, pulados)
        reportar_fades(track, track_in, track_out, pulados, report)
        consumed_in |= track_in
        consumed_out |= track_out
    return rate, consumed_in, consumed_out


def _split_stereo_sources(session: SessionIR, destination: Path, report: Report) -> dict | None:
    """Separa as fontes estereo em mono; None quando o split falha e o caso e reportado."""
    # Pro Tools referencia somente o primeiro canal de cada arquivo externo.
    # Separar antes de abrir o AAF tambem preserva um destino preexistente se
    # faltar midia estereo ou seu formato nao puder ser separado sem perdas.
    from .audio_split import split_stereo_source

    mono_sources = {}
    used_media = {clip.media_id for track in session.tracks for clip in track.clips}
    for asset in session.media:
        if asset.id in used_media and asset.channels == 2:
            try:
                mono_sources[asset.id] = split_stereo_source(
                    Path(asset.resolved_path or asset.filename),
                    destination.parent / f"{destination.stem} Media",
                )
            except (OSError, ValueError) as exc:
                report.error("WRITE_STEREO_SOURCE", str(exc), {"media_id": asset.id})
                return None
    return mono_sources


def _escrever_timecode(f, comp, session: SessionIR, rate: TimecodeRate, report: Report) -> None:
    """Cria o slot de Timecode com o inicio e a duracao da timeline em frames."""
    tc_slot = comp.create_timeline_slot(edit_rate=rate.fps)
    tc_slot.name = "TC"
    tc = f.create.Timecode(fps=rate.nominal_fps, drop=rate.drop_frame)
    start_frames, truncado = frame_de_inicio(
        session.start_timecode_samples, rate, session.sample_rate
    )
    if truncado:
        report.warning(
            "WRITE_TC_ROUNDING",
            f"start timecode does not fall exactly on frame {rate.fps} fps; it was truncated.",
            {},
        )
    tc.start = start_frames
    tc.drop = rate.drop_frame

    # Calculo do cursor maximo em todas as tracks
    max_samples = 0
    for track in session.tracks:
        for clip in track.clips:
            fim = clip.timeline_end_samples
            if fim > max_samples:
                max_samples = fim

    duration_samples = max(0, max_samples - session.start_timecode_samples)
    try:
        length_frames = samples_to_frames_exact(
            duration_samples, rate=rate, sample_rate=session.sample_rate
        )
    except ValueError:
        length_frames = duration_samples * rate.num // (session.sample_rate * rate.den) + 1

    tc.length = length_frames
    tc_slot.segment = tc


def _escrever_tracks(
    f,
    comp,
    session,
    report,
    builder,
    mono_sources,
    consumed_in,
    consumed_out,
    *,
    media: str = "link",
    expected_essence: dict | None = None,
) -> None:
    """Liga a midia e escreve as tracks de audio nos slots da composition."""
    # Duracao minima por asset deduzida dos clips (fallback de duracao).
    min_durations = _min_duration_from_clips(session)

    # Mobs de cada asset de audio, com as referencias por canal.
    mobs_por_media_id = _link_media(
        f,
        session,
        report,
        mono_sources,
        min_durations,
        media=media,
        expected_essence=expected_essence,
    )

    # Resolucao do mob por media_id, com fallback para assets nao declarados.
    resolver = _MobResolver(f, session, report, mobs_por_media_id, min_durations)

    ctx = TrackContext(
        f=f,
        comp=comp,
        session=session,
        report=report,
        builder=builder,
        resolver=resolver,
        consumed_in=consumed_in,
        consumed_out=consumed_out,
    )
    write_tracks(ctx)
    rotular_mastermobs(ctx.slots_escritos, report)


def _escrever_marcadores(f, comp, session, report) -> None:
    """Escreve os marcadores da sessao em um slot de metadata descritiva."""
    write_markers(f, comp, session, report)


def _descartar_parcial(staging: Path, report: Report) -> None:
    """Remove o arquivo parcial; avisa quando a limpeza falha."""
    # O arquivo AAF precisa estar fechado antes de tentar apaga-lo (Windows).
    # Se o unlink falhar (ex: processo ainda segurando), engolimos essa segunda
    # falha e deixamos a excecao original subir intacta.
    try:
        if staging.exists():
            staging.unlink()
    except Exception as cleanup_exc:
        report.warning(
            "WRITE_PARTIAL_FILE_REMAINS",
            f"could not remove the partial file {staging}: {cleanup_exc}",
            {"path": str(staging)},
        )


def _confirmar_exportacao(staging: Path, destination: Path, report: Report) -> None:
    """Confere o AAF exportado e substitui o destino quando tudo esta certo."""
    # O destino anterior so e substituido apos escrita e self-check completos.
    errors_before = report.count("error")
    try:
        check_exported_aaf(staging, report)
        if report.count("error") == errors_before or (
            not report.strict and not destination.exists()
        ):
            # Permissivo conserva arquivo novo para diagnostico, sempre com
            # relatorio incompleto. Falha no self-check nao apaga entrega anterior.
            staging.replace(destination)
    finally:
        if staging.exists():
            staging.unlink()


def _criar_staging(destination: Path) -> Path:
    """Cria o AAF temporario no mesmo diretorio do destino."""
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}-", suffix=".aaf", dir=destination.parent, delete=False
    ) as temporary:
        return Path(temporary.name)


def _confirmar_exportacao_embed(
    staging: Path, destination: Path, report: Report, expected_essence: dict
) -> None:
    """Confere o AAF com essencia e publica apenas quando nada falha.

    No embed nao existe entrega parcial: qualquer erro no relatorio, inclusive
    um erro anterior a esta chamada, impede a substituicao do destino.
    """
    try:
        check_embed_size(staging.stat().st_size, report, stage="file")
        verify_embedded_aaf(staging, expected_essence, report)
        check_exported_aaf(staging, report)
        if report.incomplete:
            report.warning(
                "EXPORT_EMBED_NOT_PUBLISHED",
                f"AAF with embedded media not published: the report is incomplete ({destination})",
                {"path": str(destination)},
            )
            return
        staging.replace(destination)
    finally:
        _descartar_parcial(staging, report)


def _write_aaf_embed(
    session: SessionIR,
    destination: Path,
    report: Report,
    rate,
    consumed_in,
    consumed_out,
    composition_name: str = DEFAULT_COMPOSITION_NAME,
) -> None:
    """Grava o AAF com a midia embarcada na essencia dos SourceMobs de arquivo."""
    if not preflight_embed(session, report):
        return
    with tempfile.TemporaryDirectory(prefix="aafton-embed-") as temporario:
        mono_sources = _split_stereo_embed(session, Path(temporario), report)
        if mono_sources is None:
            return
        payload = embed_payload_size(session, mono_sources)
        if not check_embed_size(payload, report, stage="payload"):
            return
        expected_essence: dict[str, int] = {}
        staging = _criar_staging(destination)
        try:
            with aaf2.open(str(staging), "w") as f:
                comp = f.create.CompositionMob(composition_name)
                f.content.mobs.append(comp)

                builder = AafBuilder(f, report, session, _curva_suportada)

                _escrever_timecode(f, comp, session, rate, report)
                _escrever_tracks(
                    f,
                    comp,
                    session,
                    report,
                    builder,
                    mono_sources,
                    consumed_in,
                    consumed_out,
                    media="embed",
                    expected_essence=expected_essence,
                )
                _escrever_marcadores(f, comp, session, report)
            _confirmar_exportacao_embed(staging, destination, report, expected_essence)
        except Exception:
            _descartar_parcial(staging, report)
            raise


def _write_aaf_link(
    session: SessionIR,
    destination: Path,
    report: Report,
    rate,
    consumed_in,
    consumed_out,
    composition_name: str = DEFAULT_COMPOSITION_NAME,
) -> None:
    """Grava o AAF referenciando os arquivos externos, como sempre foi."""
    mono_sources = _split_stereo_sources(session, destination, report)
    if mono_sources is None:
        return
    staging = _criar_staging(destination)
    try:
        with aaf2.open(str(staging), "w") as f:
            # A composition que representa a timeline
            comp = f.create.CompositionMob(composition_name)
            f.content.mobs.append(comp)

            builder = AafBuilder(f, report, session, _curva_suportada)

            # 1. Slot de Timecode (obrigatorio para sinc)
            _escrever_timecode(f, comp, session, rate, report)

            # 2. Tracks de audio
            _escrever_tracks(
                f, comp, session, report, builder, mono_sources, consumed_in, consumed_out
            )

            # 3. Marcadores da sessao
            _escrever_marcadores(f, comp, session, report)
    except Exception:
        _descartar_parcial(staging, report)
        raise

    _confirmar_exportacao(staging, destination, report)


def write_aaf(
    session: SessionIR,
    destination: str | Path,
    report: Report | None = None,
    *,
    media: str = "link",
    composition_name: str | None = None,
) -> None:
    """Grava o SessionIR em um AAF valido com TimecodeMobSlot.

    ``media`` escolhe como a midia entra no AAF: ``link`` referencia os
    arquivos externos e ``embed`` grava a essencia dentro do proprio AAF.
    ``composition_name`` e o nome da CompositionMob; sem ele, o export do
    Live grava ``Exported from Ableton``.
    """
    if report is None:
        report = Report(strict=False)

    if media not in ("link", "embed"):
        raise ValueError(f"invalid media mode: {media!r}")

    # Corta no proprio SessionIR de quem chamou o clip que passa menos de um
    # frame do fim da midia; a mudanca no lugar e aceita e vale para as tres
    # entradas do export (CLI, Studio e web app).
    trim_to_media_end(session, report)

    # consumed_in e consumed_out reunem os fades consumidos de todas as tracks nao-video.
    rate, consumed_in, consumed_out = _preflight(session, report)

    # Midia fora de WAV ou AIFF quebra o pyaaf2 no meio da escrita: o erro
    # entra no relatorio antes de qualquer descritor ser criado.
    if not _checar_formatos_media(session, report):
        return

    destination = Path(destination)
    nome = composition_name or DEFAULT_COMPOSITION_NAME
    if media == "embed":
        _write_aaf_embed(session, destination, report, rate, consumed_in, consumed_out, nome)
    else:
        _write_aaf_link(session, destination, report, rate, consumed_in, consumed_out, nome)
