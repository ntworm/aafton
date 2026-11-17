"""Testes dos nomes de track da revisao da organizacao de sessao.

Desmarcar uma track de destino faz a proposta ser montada de novo: a
compactacao reempacota o bloco, as chaves das tracks mudam e o nome digitado
na revisao precisa seguir os clips que ele ancorou.
"""

from __future__ import annotations

from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_plan import Edits, Proposal, build_proposal
from aafton.timecode import TimecodeRate

SINAIS_COM_COMPACT = frozenset({"file", "meta", "name", "compact"})
SINAIS_SEM_COMPACT = frozenset({"file", "meta", "name"})
SINAIS_COM_FAMILIA = frozenset({"file", "meta", "name", "family", "compact"})

# Cinco tracks de producao: os clips de 1, 2 e 5 se sobrepoem, o de 3 e o de 4
# nao. A compactacao junta os que nao se atrapalham em tres tracks de destino.
CLIPES = (
    (0, "Audio 1", "001CENA01PL1TK1-001.WAV", 0, 1000),
    (1, "Audio 2", "002CENA02PL1TK1-002.WAV", 0, 1000),
    (2, "Audio 3", "003CENA03PL1TK1-003.WAV", 2000, 3000),
    (3, "Audio 4", "004CENA04PL1TK1-004.WAV", 4000, 5000),
    (4, "Audio 5", "005CENA05PL1TK1-005.WAV", 0, 1000),
)

# Tres tracks: os clips de 2 e de 3 tocam juntos e o de 1 vem depois, sozinho.
# A compactacao herda as chaves por indice, entao desmarcar o clip de 1
# reempacota o bloco e a chave "Audio 2" passa a carregar outro clip.
CLIPES_TROCA = (
    (0, "Audio 1", "001CENA01PL1TK1-001.WAV", 4000, 5000),
    (1, "Audio 2", "002CENA02PL1TK1-002.WAV", 0, 1000),
    (2, "Audio 3", "003CENA03PL1TK1-003.WAV", 0, 1000),
)

# Duas familias de arquivos: cada uma com dois clips que nao se sobrepoem, o
# que faz a compactacao juntar os dois numa track so.
CLIPES_FAMILIAS = (
    (0, "Audio 1", "HomemJovem_006.mp3", 0, 1000),
    (1, "Audio 2", "HomemJovem_007.mp3", 2000, 3000),
    (2, "Audio 3", "MulherJovem_006.mp3", 0, 1000),
    (3, "Audio 4", "MulherJovem_007.mp3", 2000, 3000),
)


def _sessao(clipes: tuple[tuple[int, str, str, int, int], ...] = CLIPES) -> SessionIR:
    """Sessao sintetica com cinco tracks de producao que se sobrepoem."""
    session = SessionIR(
        name="Renomear",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    for indice, nome, arquivo, inicio, fim in clipes:
        media_id = f"m_{indice}"
        session.media.append(MediaAsset(id=media_id, filename=arquivo, channels=1))
        clip = ClipIR(
            id=f"c_{indice}",
            track_id=f"trk_{indice}",
            channel_index=0,
            event_index=0,
            name=arquivo,
            media_id=media_id,
            timeline_start_samples=inicio,
            timeline_end_samples=fim,
        )
        session.tracks.append(
            TrackIR(
                id=f"trk_{indice}",
                source_index=indice,
                name=nome,
                kind="audio",
                channel_count=1,
                clips=[clip],
            )
        )
    return session


def _proposta(
    session: SessionIR,
    *,
    compact: bool = True,
    edits: Edits | None = None,
    sinais: frozenset[str] | None = None,
) -> Proposal:
    """Proposta de organizacao da sessao, com ou sem compactacao."""
    if sinais is None:
        sinais = SINAIS_COM_COMPACT if compact else SINAIS_SEM_COMPACT
    return build_proposal(
        session,
        stereo="split",
        level="permissive",
        signals=sinais,
        edits=edits,
        accepted_pairs=set(),
        media_meta=lambda _media_id: None,
    )


def _chaves(proposta: Proposal) -> list[str]:
    """Chaves das tracks de destino, na ordem da proposta."""
    return [track.key for grupo in proposta.groups for track in grupo.tracks]


def _nomes(proposta: Proposal) -> list[str]:
    """Nomes das tracks de destino, na ordem da proposta."""
    return [track.name for grupo in proposta.groups for track in grupo.tracks]


def _nome_do_clip(proposta: Proposal, chave: tuple[int, int]) -> str:
    """Nome da track de destino que carrega o clip de origem."""
    for grupo in proposta.groups:
        for track in grupo.tracks:
            if any(chave in unidade.origin_keys for unidade in track.units):
                return track.name
    raise AssertionError(f"o clip {chave} sumiu da proposta")


def test_renomear_e_desmarcar_outra_track_guarda_o_nome_nos_clips() -> None:
    """Com a compactacao o nome segue os clips quando outra track e desmarcada."""
    session = _sessao()
    base = _proposta(session)
    # A primeira disposicao tem tres tracks: a compactacao reempacota o bloco.
    assert _chaves(base) == ["PRODUCTION:Audio 1", "PRODUCTION:Audio 2", "PRODUCTION:Audio 3"]

    edits = Edits(
        tracks={"PRODUCTION:Audio 1": False},
        track_names={"PRODUCTION:Audio 3": "Clipe E"},
    )
    proposta = _proposta(session, edits=edits)
    # A track desmarcada some e o bloco e reempacotado: as chaves mudam.
    assert _chaves(proposta) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 5"]
    # O nome foi para a track que carrega o mesmo clip do editor.
    assert _nome_do_clip(proposta, (4, 0)) == "Clipe E"
    assert _nome_do_clip(proposta, (1, 0)) == "PROD 1"


def test_nome_em_chave_das_duas_disposicoes_fica_no_lugar() -> None:
    """A track que continua na segunda disposicao guarda o proprio nome."""
    session = _sessao()
    edits = Edits(
        tracks={"PRODUCTION:Audio 1": False},
        track_names={"PRODUCTION:Audio 2": "Clipe B"},
    )
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 5"]
    assert _nome_do_clip(proposta, (1, 0)) == "Clipe B"


def test_sem_desmarcar_track_nao_ha_remapeio() -> None:
    """Sem track desmarcada a proposta e a primeira disposicao, sem remapeio."""
    session = _sessao()
    edits = Edits(track_names={"PRODUCTION:Audio 3": "Clipe E"})
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == ["PRODUCTION:Audio 1", "PRODUCTION:Audio 2", "PRODUCTION:Audio 3"]
    assert _nome_do_clip(proposta, (4, 0)) == "Clipe E"


def test_nome_da_track_desmarcada_some_sem_erro() -> None:
    """A track que ficou sem clip aceito perde o nome, sem falhar a proposta."""
    session = _sessao()
    edits = Edits(
        tracks={"PRODUCTION:Audio 1": False},
        track_names={"PRODUCTION:Audio 1": "Desligada", "PRODUCTION:Audio 3": "Clipe E"},
    )
    proposta = _proposta(session, edits=edits)
    nomes = [track.name for grupo in proposta.groups for track in grupo.tracks]
    assert "Desligada" not in nomes
    assert "Clipe E" in nomes
    assert _nome_do_clip(proposta, (4, 0)) == "Clipe E"


def test_sem_compactacao_o_nome_fica_na_chave_da_primeira_disposicao() -> None:
    """Sem compactacao as chaves nao mudam: o nome fica onde o usuario botou."""
    session = _sessao()
    base = _proposta(session, compact=False)
    assert _chaves(base) == [
        "PRODUCTION:Audio 1",
        "PRODUCTION:Audio 2",
        "PRODUCTION:Audio 3",
        "PRODUCTION:Audio 4",
        "PRODUCTION:Audio 5",
    ]
    edits = Edits(
        tracks={"PRODUCTION:Audio 1": False},
        track_names={"PRODUCTION:Audio 5": "Clipe E"},
    )
    proposta = _proposta(session, compact=False, edits=edits)
    assert _chaves(proposta) == [
        "PRODUCTION:Audio 2",
        "PRODUCTION:Audio 3",
        "PRODUCTION:Audio 4",
        "PRODUCTION:Audio 5",
    ]
    assert _nome_do_clip(proposta, (4, 0)) == "Clipe E"


def test_desmarcar_por_origem_move_o_nome_para_a_track_da_ancora() -> None:
    """Desmarcados os clips de outra track pela origem, o nome segue a ancora."""
    session = _sessao()
    edits = Edits(
        origins={(0, 0): False, (2, 0): False, (3, 0): False},
        track_names={"PRODUCTION:Audio 3": "Clipe E"},
        track_name_origins={"PRODUCTION:Audio 3": ((4, 0),)},
    )
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 5"]
    assert _nome_do_clip(proposta, (4, 0)) == "Clipe E"


def test_ancora_sem_origem_aceita_perde_o_nome() -> None:
    """A ancora sem nenhum clip aceito derruba o nome da track."""
    session = _sessao()
    edits = Edits(
        origins={(0, 0): False, (2, 0): False, (3, 0): False},
        track_names={"PRODUCTION:Audio 1": "Clipe A"},
        track_name_origins={"PRODUCTION:Audio 1": ((0, 0), (2, 0), (3, 0))},
    )
    proposta = _proposta(session, edits=edits)
    assert _chaves(proposta) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 5"]
    assert "Clipe A" not in _nomes(proposta)


def test_dois_nomes_disputam_a_mesma_track() -> None:
    """Dois nomes ancorados no mesmo clip disputam a track: um leva, o outro cai."""
    session = _sessao(CLIPES_TROCA)
    # A ancora de cada nome e o clip que ele via no momento da renomeacao.
    edits = Edits(
        origins={(0, 0): False},
        track_names={"PRODUCTION:Audio 2": "Nome B", "PRODUCTION:Audio 3": "Nome C"},
        track_name_origins={"PRODUCTION:Audio 2": ((2, 0),), "PRODUCTION:Audio 3": ((2, 0),)},
    )
    proposta = _proposta(session, edits=edits)
    # A track de mesma chave leva o nome; o outro nome cai sem track.
    assert _nome_do_clip(proposta, (2, 0)) == "Nome C"
    assert "Nome B" not in _nomes(proposta)


def test_familia_nao_pega_o_nome_de_outra_familia() -> None:
    """O nome de uma familia vai para track da mesma familia, nunca de outra."""
    session = _sessao(CLIPES_FAMILIAS)
    base = _proposta(session, sinais=SINAIS_COM_FAMILIA)
    assert _chaves(base) == [
        "family:homemjovem:Audio 1",
        "family:mulherjovem:Audio 3",
    ]

    edits = Edits(
        origins={(0, 0): False},
        track_names={"family:homemjovem:Audio 1": "Familia"},
        track_name_origins={"family:homemjovem:Audio 1": ((0, 0), (1, 0))},
    )
    proposta = _proposta(session, edits=edits, sinais=SINAIS_COM_FAMILIA)
    assert _chaves(proposta) == [
        "family:homemjovem:Audio 2",
        "family:mulherjovem:Audio 3",
    ]
    # O nome foi para a track da mesma familia; a outra familia nao recebeu.
    assert _nome_do_clip(proposta, (1, 0)) == "Familia"
    outra_familia = [
        track.name
        for grupo in proposta.groups
        if grupo.is_family and "mulherjovem" in grupo.key
        for track in grupo.tracks
    ]
    assert "Familia" not in outra_familia


def test_chave_que_sobrevive_com_outros_clips_perde_o_nome() -> None:
    """A chave que sobra com outros clips perde o nome para a track da ancora."""
    session = _sessao(CLIPES_TROCA)
    base = _proposta(session)
    # A compactacao trocou as identidades: a chave "Audio 2" carrega o clip 3.
    assert _chaves(base) == ["PRODUCTION:Audio 1", "PRODUCTION:Audio 2"]
    assert _nome_do_clip(base, (2, 0)) == "PROD 2"

    edits = Edits(
        origins={(0, 0): False},
        track_names={"PRODUCTION:Audio 2": "Clipe C"},
        track_name_origins={"PRODUCTION:Audio 2": ((2, 0),)},
    )
    proposta = _proposta(session, edits=edits)
    # A chave "Audio 2" sobreviveu, mas com o clip 2: o nome vai para o clip 3.
    assert _chaves(proposta) == ["PRODUCTION:Audio 2", "PRODUCTION:Audio 3"]
    assert _nome_do_clip(proposta, (2, 0)) == "Clipe C"
    assert _nome_do_clip(proposta, (1, 0)) == "PROD 1"


def test_sem_compactacao_e_sem_edicoes_a_proposta_nao_muda() -> None:
    """Sem compactacao e sem edicoes a proposta continua a primeira disposicao."""
    session = _sessao()
    base = _proposta(session, compact=False)
    assert _nomes(base) == ["PROD 1", "PROD 2", "PROD 3", "PROD 4", "PROD 5"]
    assert base.to_json() == _proposta(session, compact=False, edits=Edits()).to_json()
    assert base.to_json() == _proposta(session, compact=False, edits=None).to_json()


def test_volta_e_volta_do_json_com_track_name_origins() -> None:
    """As origens da ancora do nome sobrevivem a ida e volta do JSON."""
    edits = Edits(
        track_names={"PRODUCTION:Audio 1": "Clipe A"},
        track_name_origins={"PRODUCTION:Audio 1": ((0, 0), (2, 0))},
    )
    assert Edits.from_json(edits.to_json()) == edits
    assert edits.to_dict()["track_name_origins"] == {"PRODUCTION:Audio 1": [[0, 0], [2, 0]]}
    assert Edits.from_dict(edits.to_dict()) == edits
    # Edicoes antigas, sem a ancora, continuam a ser lidas.
    antigas = Edits.from_dict({"track_names": {"PRODUCTION:Audio 1": "Clipe A"}})
    assert antigas.track_name_origins == {}
    # A proposta com a ancora tambem volta igual do JSON.
    proposta = _proposta(_sessao(), edits=edits)
    assert Proposal.from_json(proposta.to_json()) == proposta


def test_chave_que_sobrevive_com_parte_da_ancora_cede_o_nome_a_que_tem_mais() -> None:
    """A chave que fica com menos origens da ancora que outra track perde o nome."""
    from aafton.organize_home import _HomeBuilder
    from aafton.organize_renomear import resolver_nomes
    from aafton.organize_units import Unit

    def unidade(slot: int) -> Unit:
        return Unit(
            origin_keys=((slot, 0),),
            source_tracks=(slot,),
            source_label=f"Audio {slot}",
            channel_count=1,
            start_samples=slot * 10,
            end_samples=slot * 10 + 5,
            name=f"Take {slot}",
            media_filename=None,
            kind="PRODUCTION",
            score=1,
            conflict=False,
            reasons=(),
            accepted=True,
        )

    pouca = _HomeBuilder("PRODUCTION", "Audio 1", (1,), 1, units=[unidade(1)])
    pouca.key = "PRODUCTION:Audio 1"
    muita = _HomeBuilder("PRODUCTION", "Audio 2", (2,), 1, units=[unidade(2), unidade(3)])
    muita.key = "PRODUCTION:Audio 2"
    edits = Edits(
        track_names={"PRODUCTION:Audio 1": "Boom"},
        track_name_origins={"PRODUCTION:Audio 1": ((1, 0), (2, 0), (3, 0))},
    )
    resolvidas = resolver_nomes([], [pouca, muita], edits)
    assert resolvidas.track_names == {"PRODUCTION:Audio 2": "Boom"}


def _chave_estacionada(ancora: tuple[tuple[int, int], ...]) -> str:
    """Como App.parkedKey: "~" mais a ancora ordenada, unica por nome."""
    return "~" + ",".join(sorted(f"{slot}:{idx}" for slot, idx in ancora))


class _Pagina:
    """Imita a pagina da revisao: guarda as edicoes e adota os nomes resolvidos."""

    def __init__(self, session: SessionIR) -> None:
        self.session = session
        self.origens: dict[tuple[int, int], bool] = {}
        self.nomes: dict[str, str] = {}
        self.ancoras: dict[str, tuple[tuple[int, int], ...]] = {}
        self.proposta = self.buscar()

    def buscar(self) -> Proposal:
        """Pede a proposta e adota os nomes resolvidos, guardando os que cairam."""
        edits = Edits(
            origins=dict(self.origens),
            track_names=dict(self.nomes),
            track_name_origins=dict(self.ancoras),
        )
        self.proposta = _proposta(self.session, edits=edits)
        nomes = dict(self.proposta.edits.track_names)
        ancoras = dict(self.proposta.edits.track_name_origins)
        colocadas = {tuple(sorted(a)) for a in ancoras.values()}
        # Como App.adoptResolvedNames: o nome sem track vai para a chave "~".
        for chave, nome in self.nomes.items():
            ancora = self.ancoras.get(chave, ())
            if not ancora or tuple(sorted(ancora)) in colocadas:
                continue
            estacionada = _chave_estacionada(ancora)
            nomes[estacionada] = nome
            ancoras[estacionada] = ancora
        self.nomes, self.ancoras = nomes, ancoras
        return self.proposta

    def _aceitas(self, chave: str) -> tuple[tuple[int, int], ...]:
        """Origens aceitas da track; chave fora da proposta nao tem origem."""
        for grupo in self.proposta.groups:
            for track in grupo.tracks:
                if track.key == chave:
                    return tuple(
                        sorted(o for u in track.units if u.accepted for o in u.origin_keys)
                    )
        return ()

    def _ancora_de(self, chave: str) -> tuple[tuple[int, int], ...]:
        """A ancora de um nome: a declarada ou, quando nao ha, as origens da chave."""
        if chave in self.ancoras:
            return self.ancoras[chave]
        return self._aceitas(chave)

    def renomear(self, chave: str, nome: str) -> None:
        """Como o handler de nome do JS: apaga sobrepostos e estaciona o antigo."""
        if nome:
            ancora = self._aceitas(chave)
            for outra in list(self.nomes):
                if outra != chave and set(self._ancora_de(outra)) & set(ancora):
                    self.nomes.pop(outra, None)
                    self.ancoras.pop(outra, None)
            antiga = self.ancoras.get(chave, ())
            if chave in self.nomes and antiga and not set(antiga) & set(ancora):
                self.nomes[_chave_estacionada(antiga)] = self.nomes[chave]
                self.ancoras[_chave_estacionada(antiga)] = antiga
            self.nomes[chave] = nome
            self.ancoras[chave] = ancora
        else:
            # Apagar o nome apaga tambem o nome estacionado que cairia nesta
            # track: a ancora dele divide as origens aceitas da track apagada.
            base = self._aceitas(chave)
            for outra in list(self.nomes):
                if outra == chave or not outra.startswith("~"):
                    continue
                if set(self._ancora_de(outra)) & set(base):
                    self.nomes.pop(outra, None)
                    self.ancoras.pop(outra, None)
            self.nomes.pop(chave, None)
            self.ancoras.pop(chave, None)
        self.buscar()

    def desmarcar(self, origem: tuple[int, int]) -> None:
        self.origens[origem] = False
        self.buscar()

    def chave_do_clip(self, origem: tuple[int, int]) -> str:
        for grupo in self.proposta.groups:
            for track in grupo.tracks:
                if any(origem in u.origin_keys for u in track.units):
                    return track.key
        raise AssertionError(f"o clip {origem} sumiu da proposta")


def test_pagina_renomeia_a_track_que_herdou_a_chave_sem_perder_o_outro_nome() -> None:
    """Renomear, desmarcar e renomear a track que ficou com a chave antiga."""
    pagina = _Pagina(_sessao(CLIPES_TROCA))
    chave_c = pagina.chave_do_clip((2, 0))
    pagina.renomear(chave_c, "Clipe C")
    pagina.desmarcar((0, 0))
    assert _nome_do_clip(pagina.proposta, (2, 0)) == "Clipe C"
    nova_c = pagina.chave_do_clip((2, 0))
    # A track com a chave em que o nome foi digitado agora carrega outro clip.
    assert nova_c != chave_c
    assert chave_c in _chaves(pagina.proposta)
    pagina.renomear(chave_c, "Outro")
    assert _nome_do_clip(pagina.proposta, (2, 0)) == "Clipe C"
    assert "Outro" in _nomes(pagina.proposta)


def test_pagina_apaga_um_nome_que_mudou_de_chave() -> None:
    """Apagar pela tela o nome que o servidor levou para outra chave."""
    pagina = _Pagina(_sessao(CLIPES_TROCA))
    pagina.renomear(pagina.chave_do_clip((2, 0)), "Clipe C")
    pagina.desmarcar((0, 0))
    pagina.renomear(pagina.chave_do_clip((2, 0)), "")
    assert "Clipe C" not in _nomes(pagina.proposta)
    pagina.buscar()
    assert "Clipe C" not in _nomes(pagina.proposta)


def test_ancora_vazia_vale_como_sem_ancora() -> None:
    """Nome com ancora vazia fica na chave, como edicao antiga sem ancora."""
    session = _sessao()
    base = _proposta(session)
    chave = _chaves(base)[0]
    edits = Edits(track_names={chave: "Fica"}, track_name_origins={chave: ()})
    assert _nomes(_proposta(session, edits=edits))[0] == "Fica"


def test_json_sem_ancora_fica_igual_ao_de_antes() -> None:
    """Sem ancora nenhuma o JSON das edicoes nao ganha a chave nova."""
    assert "track_name_origins" not in Edits().to_dict()


def test_ancora_malformada_vira_value_error() -> None:
    """Ancora com par curto ou valor nulo e recusada com ValueError."""
    import pytest

    for bruto in ({"k": [[1]]}, {"k": None}, ["x"]):
        with pytest.raises(ValueError):
            Edits.from_dict({"track_name_origins": bruto})


def test_pagina_desmarca_e_remarca_a_track_renomeada_e_o_nome_volta() -> None:
    """O nome de uma track toda desmarcada fica guardado e volta com os clips."""
    pagina = _Pagina(_sessao())
    chave = pagina.chave_do_clip((3, 0))
    pagina.renomear(chave, "Clipe D")
    # Desmarca todos os clips da track renomeada, como o checkbox da track.
    ancora = pagina.ancoras[chave]
    for origem in ancora:
        pagina.desmarcar(origem)
    assert "Clipe D" not in _nomes(pagina.proposta)
    assert any(k.startswith("~") and n == "Clipe D" for k, n in pagina.nomes.items())
    for origem in ancora:
        pagina.origens.pop(origem)
    pagina.buscar()
    assert _nome_do_clip(pagina.proposta, (3, 0)) == "Clipe D"
    assert [n for n in pagina.nomes.values() if n == "Clipe D"] == ["Clipe D"]


def test_ancora_com_par_de_tres_numeros_vira_value_error() -> None:
    """Par de origem com tamanho diferente de dois e recusado."""
    import pytest

    with pytest.raises(ValueError):
        Edits.from_dict({"track_name_origins": {"k": [[1, 2, 3]]}})


def test_pagina_desmarca_duas_tracks_vizinhas_e_os_dois_nomes_voltam() -> None:
    """Dois nomes estacionados um depois do outro nao disputam a mesma chave."""
    pagina = _Pagina(_sessao())
    chaves = _chaves(pagina.proposta)
    pagina.renomear(chaves[0], "Nome A")
    pagina.renomear(chaves[1], "Nome B")
    ancora_a = pagina.ancoras[chaves[0]]
    ancora_b = pagina.ancoras[chaves[1]]
    for origem in ancora_a:
        pagina.desmarcar(origem)
    for origem in ancora_b:
        pagina.desmarcar(origem)
    estacionados = sorted(n for k, n in pagina.nomes.items() if k.startswith("~"))
    assert estacionados == ["Nome A", "Nome B"]
    for origem in (*ancora_a, *ancora_b):
        pagina.origens.pop(origem)
    pagina.buscar()
    nomes = _nomes(pagina.proposta)
    assert nomes.count("Nome A") == 1
    assert nomes.count("Nome B") == 1


def test_pagina_apagar_o_nome_tira_o_nome_estacionado_dos_mesmos_clips() -> None:
    """Apagar o nome de uma track apaga o nome estacionado que cairia nela."""
    pagina = _Pagina(_sessao())
    # A track "PRODUCTION:Audio 1" leva os clips 1, 3 e 4; a "Audio 2" leva o clip 2.
    chave_p = pagina.chave_do_clip((3, 0))
    chave_t = pagina.chave_do_clip((1, 0))
    pagina.renomear(chave_p, "P name")
    pagina.renomear(chave_t, "N")
    ancora_p = pagina.ancoras[chave_p]
    for origem in ancora_p:
        pagina.desmarcar(origem)
    # Sem clip aceito o nome de P fica estacionado, fora de qualquer track.
    assert "P name" not in _nomes(pagina.proposta)
    assert any(k.startswith("~") and n == "P name" for k, n in pagina.nomes.items())
    # O clip de P volta marcado e cai com o clip de T, na track que tem o nome N.
    pagina.origens.pop((2, 0), None)
    pagina.buscar()
    assert pagina.chave_do_clip((2, 0)) == chave_t
    assert pagina.chave_do_clip((1, 0)) == chave_t
    assert _nome_do_clip(pagina.proposta, (1, 0)) == "N"
    assert "P name" not in _nomes(pagina.proposta)
    assert any(k.startswith("~") and n == "P name" for k, n in pagina.nomes.items())
    # Apagar o nome N limpa a track e leva junto o nome estacionado dos mesmos clips.
    pagina.renomear(chave_t, "")
    assert _nome_do_clip(pagina.proposta, (1, 0)) == "PROD 1"
    assert "P name" not in _nomes(pagina.proposta)
    assert not any(k.startswith("~") and pagina.ancoras.get(k) == ancora_p for k in pagina.nomes)


def test_pagina_apagar_o_nome_sem_estacionado_deixa_a_track_com_o_nome_padrao() -> None:
    """Sem nome estacionado, apagar o nome so devolve o nome padrao da track."""
    pagina = _Pagina(_sessao())
    chave = pagina.chave_do_clip((3, 0))
    pagina.renomear(chave, "Clipe D")
    pagina.renomear(chave, "")
    assert _nome_do_clip(pagina.proposta, (3, 0)) == "PROD 1"
    assert not pagina.nomes
    assert not pagina.ancoras
