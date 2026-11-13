"""Caso C11 do oraculo diferencial do Live: um unico clip estereo.

A pergunta do C11 e se o Live abre um AAF do Pro Tools com combiner como uma
track estereo com um clip so. O material e um WAV de dois canais com L e R
diferentes: um combiner que repetisse a mesma fonte mono nos dois canais
pareceria stereo sem ser. O AAF vem da fabrica de calibracao, que divide o
stereo em duas fontes mono distintas e as junta num unico combiner.
"""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tests.aaf_factory import build_calibration_aaf  # noqa: E402

SR = 48000
DURACAO = 2 * SR
AMPLITUDE = 8000
NOME_TRACK = "DX 1"


def fonte_com_canais_distintos(pasta: Path, canais: int) -> Path:
    """WAV com um sinal por canal: o da direita e o oposto do da esquerda.

    Os canais precisam ser sons diferentes: e o que separa um combiner de duas
    fontes mono distintas de um que repete a mesma fonte nos dois canais.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "fonte.wav"
    amostras = [AMPLITUDE if canal % 2 == 0 else -AMPLITUDE for canal in range(canais)]
    quadro = struct.pack(f"<{canais}h", *amostras)
    with wave.open(str(caminho), "wb") as handle:
        handle.setparams((canais, 2, SR, DURACAO, "NONE", "not compressed"))
        handle.writeframes(quadro * DURACAO)
    return caminho


def aaf_com_combiner(aaf: Path, wav: Path) -> None:
    """Combiner do Pro Tools: uma fonte mono distinta por canal."""
    frames = _frames_do_wav(wav)
    build_calibration_aaf(
        aaf,
        [
            {
                "name": NOME_TRACK,
                "clips": [
                    {
                        "path": str(wav),
                        "start": 0,
                        "duration": frames,
                        "offset": 0,
                        "mob_name": NOME_TRACK,
                    }
                ],
            }
        ],
        frames=frames,
    )


def _frames_do_wav(wav: Path) -> int:
    """Quantos samples o WAV tem, lidos do proprio arquivo."""
    with wave.open(str(wav), "rb") as handle:
        return handle.getnframes()
