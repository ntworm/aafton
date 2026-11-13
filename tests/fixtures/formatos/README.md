# Amostras de formatos de intercâmbio — CENA12

Fixtures da fase 3 do `ableton-aaf`: a mesma cena, CENA12, escrita nos três
formatos de intercâmbio que o OpenTimelineIO lê.

## O que há aqui

| Arquivo | Formato |
|---|---|
| `cena12.edl` | EDL CMX 3600 |
| `cena12_premiere.xml` | XML do Final Cut Pro 7 / Premiere (xmeml versão 4) |
| `cena12.fcpxml` | FCPXML versão 1.9 |

Os três arquivos foram escritos à mão, a partir das especificações publicadas
de cada formato. Nenhuma mídia acompanha as amostras e nenhum arquivo do
OpenTimelineIO (nem dos adaptadores dele) foi copiado para cá.
Portanto nenhuma licença de terceiros se aplica a estas amostras.

## A cena

- Taxa: 25 fps, non drop frame.
- Início da sessão: 01:00:00:00.
- Arquivos: `dial01.wav` (mono, 48 kHz, timecode de arquivo 10:00:00:00,
  20 s) e `dial02.wav` (mono, 48 kHz, timecode de arquivo 00:00:00:00, 10 s).

| Evento | Track | Arquivo | Source | Record |
|---|---|---|---|---|
| 001 | 1 | dial01.wav | 10:00:01:00 - 10:00:03:00 | 01:00:00:00 - 01:00:02:00 |
| 002 | 2 | dial02.wav | 00:00:00:00 - 00:00:02:00 | 01:00:01:00 - 01:00:03:00 |
| 003 | 1 | dial01.wav | 10:00:05:00 - 10:00:06:00 | 01:00:04:00 - 01:00:05:00 |

Marcador `TESTE` em 01:00:02:00. Ele aparece no XML do Premiere e no FCPXML; o
CMX 3600 não tem marcador, então o EDL não o carrega.

## Mídia

`dial01.wav` e `dial02.wav` não estão no repositório: os próprios testes os
criam, com 48 kHz mono e duração suficiente para cobrir os trechos da cena.

## O que ainda falta

Exportações reais do Premiere e do Resolve entram depois, no script de
aceitação. Estas amostras são a referência mínima para a leitura, não
substituem o material de produção.
