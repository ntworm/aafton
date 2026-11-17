"""Calculo do envelope de ganho gravado no AAF.

Reune a aritmetica de ganho de um clip: leitura da curva de automacao,
aplicacao dos fades independentes e deduplicacao dos keyframes. As funcoes
sao puras, recebem os dados ja calculados e devolvem os keyframes prontos
para o VaryingValue do AAF.
"""


def _ganho_em(pontos: list[tuple[int, float]], amostra: int) -> float:
    """Valor da curva de automacao numa posicao da timeline, com interpolacao linear.

    Usado so para escolher o valor de cada trecho constante: o AAF recebe degraus,
    mas o valor do degrau e o da curva no comeco dele, e nao a media do trecho.
    Fora dos extremos, o valor extremo vale, que e o que um envelope faz.
    """
    if not pontos:
        return 1.0
    if amostra <= pontos[0][0]:
        return pontos[0][1]
    if amostra >= pontos[-1][0]:
        return pontos[-1][1]
    for (t0, v0), (t1, v1) in zip(pontos, pontos[1:]):
        if t0 <= amostra <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * ((amostra - t0) / (t1 - t0))
    return pontos[-1][1]


def _fades_independentes(clip, consumed_in, consumed_out) -> tuple[bool, bool]:
    """Diz se o clip tem fade independente de entrada e de saida a gravar.

    Cada flag considera o fade do clip, a duracao dele e o conjunto de
    clips ja consumidos correspondente.
    """
    has_indep_fade_in = bool(
        clip.fade_in and clip.fade_in.duration_samples and id(clip) not in consumed_in
    )
    has_indep_fade_out = bool(
        clip.fade_out and clip.fade_out.duration_samples and id(clip) not in consumed_out
    )
    return has_indep_fade_in, has_indep_fade_out


def _ganho_base(track, clip) -> float:
    """Ganho constante do clip, antes de qualquer envelope de automacao ou fade."""
    return track.gain_linear * clip.gain_linear


def clip_mudo(track, clip) -> bool:
    """Track muda ou clip desativado: o clip vai ao AAF mudo, com o ganho normal.

    Decisao de Gabriel (15/11/2026): o editor desmuta o clip no Pro Tools em vez
    de subir um ganho que veio -inf.
    """
    return bool(track.muted or clip.muted)


def _keyframes_automacao(
    gain_points: list[tuple[int, float]],
    base_gain: float,
    clip_start_abs: int,
    clip_end_abs: int,
    duration: int,
) -> list[tuple[float, float]]:
    """Keyframes de ganho do clip, com o tempo normalizado no intervalo [0, 1].

    Com curva de automacao, os pontos ficam no interior do clip e os extremos
    valem o valor da curva no inicio e no fim do clip. Sem curva, o ganho do
    clip e constante.
    """
    if not gain_points:
        return [(0.0, base_gain), (1.0, base_gain)]
    val_start = base_gain * _ganho_em(gain_points, clip_start_abs)
    val_end = base_gain * _ganho_em(gain_points, clip_end_abs)
    keyframes: list[tuple[float, float]] = [(0.0, val_start)]
    for t_abs, v in gain_points:
        if clip_start_abs < t_abs < clip_end_abs:
            t_norm = (t_abs - clip_start_abs) / duration
            keyframes.append((t_norm, base_gain * v))
    keyframes.append((1.0, val_end))
    keyframes.sort(key=lambda k: k[0])
    return keyframes


def _aplicar_fade_in(
    keyframes: list[tuple[float, float]], fade_samples: int, duration: int
) -> list[tuple[float, float]]:
    """Aplica o fade independente de entrada como envelope sobre os keyframes.

    A rampa vai de zero ate o valor do keyframe no fim do fade; um keyframe
    intermediario e criado quando a curva corta o fim do fade.
    """
    fi_len = min(duration, fade_samples)
    fi_norm = fi_len / duration
    new_kfs: list[tuple[float, float]] = []
    fi_split_added = False
    for t_norm, g in keyframes:
        if t_norm < fi_norm:
            factor = t_norm / fi_norm if fi_norm > 0 else 1.0
            new_kfs.append((t_norm, g * factor))
        else:
            if not fi_split_added and t_norm > fi_norm:
                prev_t, prev_g = new_kfs[-1] if new_kfs else (0.0, 0.0)
                interp_g = (
                    prev_g + (g - prev_g) * ((fi_norm - prev_t) / (t_norm - prev_t))
                    if t_norm > prev_t
                    else g
                )
                new_kfs.append((fi_norm, interp_g))
                fi_split_added = True
            new_kfs.append((t_norm, g))
    return new_kfs


def _aplicar_fade_out(
    keyframes: list[tuple[float, float]], fade_samples: int, duration: int
) -> list[tuple[float, float]]:
    """Aplica o fade independente de saida como envelope sobre os keyframes.

    A rampa desce ate zero no fim do clip; um keyframe intermediario e criado
    quando a curva corta o comeco do fade.
    """
    fo_len = min(duration, fade_samples)
    fo_norm = 1.0 - (fo_len / duration)
    new_kfs: list[tuple[float, float]] = []
    fo_split_added = False
    for t_norm, g in keyframes:
        if t_norm <= fo_norm:
            new_kfs.append((t_norm, g))
        else:
            if not fo_split_added and fo_norm > (new_kfs[-1][0] if new_kfs else 0.0):
                prev_t, prev_g = new_kfs[-1]
                interp_g = (
                    prev_g + (g - prev_g) * ((fo_norm - prev_t) / (t_norm - prev_t))
                    if t_norm > prev_t
                    else prev_g
                )
                new_kfs.append((fo_norm, interp_g))
                fo_split_added = True
            factor = (1.0 - t_norm) / (1.0 - fo_norm) if (1.0 - fo_norm) > 0 else 0.0
            new_kfs.append((t_norm, g * factor))
    return new_kfs


def _dedup_keyframes(keyframes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove keyframes com o mesmo instante, mantendo o ultimo valor."""
    dedup_kfs = [keyframes[0]]
    for k in keyframes[1:]:
        if abs(k[0] - dedup_kfs[-1][0]) < 1e-6:
            dedup_kfs[-1] = k
        else:
            dedup_kfs.append(k)
    return dedup_kfs
