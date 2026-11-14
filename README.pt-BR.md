# aafton

[English](README.md)

> **Pré-alfa.** O autor ainda está testando o `aafton` em sessões reais. Não use em trabalho de cliente por enquanto e confira cada sessão convertida contra a origem. Relate problemas nas issues deste repositório.

`aafton` converte sessões de som de pós-produção entre AAF, EDL (CMX 3600), XML do Final Cut Pro 7 (xmeml), FCPXML e Ableton Live 12 (`.als`). As posições dos clips são mantidas em samples; `aafton verify` compara o início e o fim de cada clip de áudio entre a origem e o set do Live. Ele foi projetado para a edição de diálogo, a conformação, o Foley e a troca de sound design entre Pro Tools, DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro e Live 12.

O conversor opera com falha fechada (fail-closed): se um recurso da sessão não puder ser representado sem perda, a operação para com um código de saída diferente de zero. O modo permissivo (`--permissive`) pode ser ativado para gerar uma saída parcial, registrando todos os avisos e erros estruturais.

## Instalação

Há três formas de executar o `aafton`.

### App de desktop para Windows

Baixe a pasta do release, descompacte-a e execute `aafton.exe`. A primeira execução de um app sem assinatura mostra o aviso do SmartScreen: escolha **Mais informações** e depois **Executar mesmo assim** para continuar. O `aafton-cli.exe` da mesma pasta é a versão de linha de comando. A janela precisa do Microsoft Edge WebView2 Runtime, que já vem instalado no Windows 11.

### macOS

Ainda não há build para macOS. Instale com `pipx`, como descrito abaixo.

### Com Python 3.10 ou mais recente

Quando o pacote estiver no PyPI:

```bash
pipx install aafton
```

Até lá, instale direto do GitHub:

```bash
pipx install "aafton[desktop] @ git+https://github.com/ntworm/aafton"
```

Ou a partir de um checkout do repositório:

```bash
pipx install .
```

O comando `pip install .` também funciona. A janela do studio precisa do extra `desktop`:

```bash
pipx install ".[desktop]"
```

Depois da instalação, `aafton app` abre o studio em uma janela própria e `aafton ui` abre a mesma página no navegador.

Para habilitar o intercâmbio de EDL e XML do Final Cut Pro 7 via OpenTimelineIO:

```bash
pip install ".[formats]"
```

A leitura e a escrita de AAF e FCPXML funcionam sem dependências opcionais. `ffmpeg` e `ffprobe` são instalados à parte e procurados no `PATH`; eles só são necessários para vídeo e para áudio dentro de MXF.

## Comandos

O ponto de entrada da CLI é o `aafton` (ou `python -m aafton.cli`).

### `import`
Converte um AAF, EDL, FCP7 XML ou FCPXML em um set do Ableton Live 12 (`.als`).
```bash
aafton import input.aaf output.als --audio-dir ./media --stereo pair --fades absorb
```
O template do Live que acompanha o pacote é usado, a menos que `--template` indique outro.
Principais opções:
- `--stereo pair|split`: junta canais `_L`/`_R` correspondentes em tracks estéreo ou mantém tracks mono separadas.
- `--fades absorb|keep`: converte fades renderizados em fades nativos do Live ou mantém os renderizados como clips de áudio independentes.
- `--media extract|link`: extrai o áudio embarcado para a pasta do projeto ou mantém links absolutos do sistema de arquivos.
- `--groups prefix|none`: agrupa as tracks automaticamente pelo prefixo do nome.
- `--video <file>`: ancora um vídeo externo ao timecode da sessão.
- `--ltc`: adiciona ao set do Live uma track de áudio LTC SMPTE ST 12-1 gerada.

### `export`
Converte um projeto Live 12 (`.als`) de volta para AAF, EDL, XML ou FCPXML, conforme a extensão do arquivo.
```bash
aafton export session.als return.aaf --report export-report.json
```
O exportador relê o arquivo de saída para confirmar que todos os clips de origem cabem dentro dos limites declarados dos assets antes de retornar sucesso.

### `inspect`
Despeja a representação intermediária normalizada (`SessionIR`) como JSON na saída padrão.
```bash
aafton inspect timeline.aaf
```

### `verify`
Compara o início e o fim de cada clip de áudio, em samples, entre o arquivo de intercâmbio de origem e o `.als` gerado.
```bash
aafton verify input.aaf output.als --report verify-report.md
```
O código de saída 0 significa que todo clip de áudio da origem foi comparado e que seu início e seu fim estão dentro da tolerância que o relatório declara: zero samples quando a origem conta em samples, um frame quando ela conta em frames. O deslocamento de mídia, o conteúdo de áudio, o ganho, o pan e os envelopes estão fora desta verificação.

### `audit-audio`
Realiza testes nulos de nível de áudio independentes entre os bounces WAV de referência (por exemplo, do Pro Tools) e os renders candidatos do Live, usando o timecode BWF e o alinhamento pelo sidecar.
```bash
aafton audit-audio --reference ./pt_bounces --candidate ./live_renders --sidecar set.aafton.json --map track_map.json --mode keep
```

### `turnover`
Gera um pacote de entrega autossuficiente com a mídia, os arquivos de sessão e um manifesto de integridade SHA-256.
```bash
aafton turnover session.als --media-dir ./media --output ./turnover_pkg
```

### `ui`
Inicia a interface web local (Studio) para a inspeção visual da timeline e o monitoramento de timecode.
```bash
aafton ui --port 8765
```

### `app`
Abre o studio em uma janela própria; ele precisa do extra `desktop`.
```bash
aafton app
```
`aafton app --smoke` verifica o servidor local e sai.

### `ltc`
Gera arquivos de áudio WAV de timecode linear SMPTE ST 12-1 avulsos.
```bash
aafton ltc --start 01:00:00:00 --rate 25 --duration 00:10:00:00 output_ltc.wav
```

## Comportamento comprovado e verificação

O conversor foi verificado em sessões de produção reais e em benchmarks controlados:
- **Precisão de amostra**: quatro sessões reais do Pro Tools (`TESTE_AFF_PROTOOLS`, `SPOT_AAF_TESTE`, `TESTE_DF_2997` e `TESTE_DF_2997_correto`) e uma sessão real do Premiere Pro, cada uma convertida para o Live e de volta nos dois modos de pareamento estéreo (dez combinações), com todo clip comparado com desvio de zero samples pelo `aafton verify`; o Resolve aparece apenas como uma fixture de dialeto de três clips para o tratamento de `edit_rate`, não como prova de uma sessão real.
- **Alinhamento drop-frame 29.97**: verificado em sessões reais exportadas em variantes drop-frame e non-drop-frame. A contagem drop-frame exata evita a deriva de 3.6036 segundos por hora que ocorre ao tratar 29.97 fps como 30 fps.
- **Aceite nativo do Live 12**: testado no Ableton Live 12.4.15b7. Os 11 casos de teste, que cobrem limites de clip fora da grade, drop frame, automação de ganho, crossfades, fades assimétricos, pan mono e combinadores de canal, foram abertos e salvos de novo pelo Save As, atingindo um índice geométrico de 1.0000 em todos os 303 eventos de áudio.
- **Auditoria acústica**: renders de Pro Tools e de Live gerados a partir de fontes AAF idênticas atingiram alinhamento com lag 0 e pico residual máximo abaixo de −144 dBFS no modo keep, em 4 tracks e 8 canais.

## Limitações conhecidas

- **Warp e loop**: clips com Warp habilitado, loop ativo ou deslocamentos de início não nulos são recusados (`ALS_CLIP_MODE_UNSUPPORTED`). Os clips precisam ser renderizados para áudio plano antes da exportação.
- **Mudanças de tempo**: a automação de tempo da track principal ou desvios do BPM do sidecar são recusados no modo estrito (`ALS_TEMPO_AUTOMATION_UNSUPPORTED` para a automação de tempo, `ALS_TEMPO_CHANGED` para um tempo diferente do sidecar).
- **Processamento de DAW**: plugins, instrumentos virtuais e cadeias de efeitos de return/master não são reconstruídos no AAF.
- **Fades e transições**: um fade de clip que não tem um fade correspondente em um clip vizinho é gravado como uma `Transition` do AAF ao lado de um `Filler` (fade-in a partir do silêncio, fade-out para o silêncio), a forma que o Resolve e o Premiere gravam; o relatório registra isso como `FADE_WRITTEN_AS_TRANSITION`. Dois fades que não cabem no clip, ou um fade com um ponto de automação de ganho dentro dele, não podem ser gravados assim: o modo estrito os recusa (`WRITE_FADE_UNSUPPORTED`) e o modo permissivo os converte em um envelope de automação de ganho (`FADE_CONVERTED_TO_ENVELOPE`). A curva de um fade do Live não é transportada; o AAF guarda um dissolve de áudio simples.
- **Automação de pan**: o pan importado é estático; a automação contínua de pan do AAF não é importada. A exportação de automação de pan para AAF só é suportada em tracks mono.
- **Formatos de arquivo**: contêineres RF64 e essências RIFF individuais maiores que 4 GiB não são suportados.
- **Tracks de vídeo**: tracks de vídeo embarcadas no AAF são descartadas na importação; o vídeo não é gravado nos arquivos AAF exportados.

## Executando os testes

Rode a suíte completa de testes automatizados com `pytest`:

```bash
pytest
```

Para rodar os testes estruturais de corpus, o corpus de silêncio dos testes é necessário. Na primeira execução, o `pytest` o gera em `.local/corpus/teste_aaf` a partir do manifesto pinado, então um clone novo não exige passo manual.

Configure o caminho do corpus usando a variável de ambiente; aponte `AAFTON_CORPUS_DIR` para outra pasta para usar um corpus guardado em outro lugar:

```bash
export AAFTON_CORPUS_DIR=.local/corpus/teste_aaf
```

Para gerar o mesmo corpus à mão:

```bash
python scripts/make_silence_corpus.py tests/corpus/teste_aaf.json .local/corpus/teste_aaf
```

Os testes de mídia que não é distribuída com o repositório são pulados com motivos que começam com `not distributed:`. Os assets de teste locais opcionais podem ser apontados por `AAFTON_LOCAL_MEDIA_DIR` e `AAFTON_REEXPORT_DIR`.

## Documentação

- [Formatos de intercâmbio e MXF](docs/FORMATS.md)
- [Matriz de dialetos](docs/DIALECT_MATRIX.md)
- [Mapa XML do Live](docs/ALS_XML_MAP.md)
- [Contrato do schema do Live](docs/ALS_SCHEMA.md)
- [Timecode e sincronização](docs/TIMECODE.md)
- [Timecode em arquivos de projeto](docs/TIMECODE-FORMATS.md)
- [Especificação da auditoria de áudio](docs/AUDIO_AUDIT.md)
- [Especificação de pós-produção](docs/POSTPRO_SPEC.md)
- [Matriz de testes](docs/POSTPRO_TEST_MATRIX.md)
- [Edições de ida e volta](docs/ROUNDTRIP_EDITS.md)
- [Roteiro de aceite de formatos](docs/FORMATS-ACCEPTANCE.md)
- [Roteiro de aceite de timecode](docs/TIMECODE-ACCEPTANCE.md)

## Licença

Licença MIT. Veja [LICENSE](LICENSE) para os detalhes.
