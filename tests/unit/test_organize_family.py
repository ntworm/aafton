"""Testes de unidade do agrupamento por familia de nomes (P1)."""

from __future__ import annotations

from aafton.organize_family import families, family_score, normalize, stem

PERSONAGEM_FILES = (
    "Personagem_ 02_Texto_B.mp3",
    "Personagem_02_Texto C.mp3",
    "Personagem_02_Texto_A.mp3",
    "Personagem_02_Texto_D.mp3",
    "Personagem_02_Texto_E.mp3",
    "Personagem_02_Texto_F.mp3",
    "Personagem_02_Texto_G.mp3",
    "Personagem_02_Texto_H.mp3",
    "Personagem_02_Texto_I.mp3",
    "Personagem_02_Texto_J.mp3",
    "Personagem_02_Texto_L.mp3",
)

UUID_FILE = (
    "Quem_haveria_de_cuidar_do_corp_Santa_Clara_02_HD_019d3f0d-9eee-72cf-a108-34c318ce38cd.mp3"
)


def test_personagem_family() -> None:
    result = families(PERSONAGEM_FILES)
    assert len(result) == 1
    fam = result[0]
    assert (fam.label, fam.files, fam.clip_count, fam.score) == (
        "Personagem 02 Texto",
        PERSONAGEM_FILES,
        len(PERSONAGEM_FILES),
        2,
    )


def test_homem_jovem_and_folia_families() -> None:
    hj = families(["HomemJovem_004.mp3", "HomemJovem_006.mp3"])
    folia = families(["C\u00f3pia de folia amarela.wav", "C\u00f3pia de folia azul.wav"])
    assert [f.label for f in hj] == ["HomemJovem"]
    assert [f.label for f in folia] == ["folia"]


def test_versao_rock_family_does_not_merge_other_versao() -> None:
    v1 = "Vers\u00e3o-rock-progressivo_-estilo-Pink-Floyd.-VERS\u00c3O-01.mp3.mp3"
    v2 = "Vers\u00e3o-rock-progressivo_-estilo-Pink-Floyd.-VERS\u00c3O-02.mp3.mp3"
    other = "Vers\u00e3o-com-Organ-e-violino-VERS\u00c3O-01.mp3.mp3"
    result = families([v1, v2, other])
    assert len(result) == 1
    fam = result[0]
    assert (fam.label, fam.files) == (
        "Vers\u00e3o rock progressivo estilo Pink Floyd",
        (v1, v2),
    )


def test_uuid_stripped_and_single_file_forms_no_family() -> None:
    words = normalize(UUID_FILE)
    file_stem = stem(UUID_FILE)
    assert {"019d3f0d", "34c318ce38cd"}.isdisjoint(words)
    assert file_stem == ("Quem", "haveria", "de", "cuidar", "do", "corp", "Santa", "Clara")
    assert families([UUID_FILE, UUID_FILE]) == []


def test_tres_caixas_single_vs_pair() -> None:
    c1 = "C\u00f3pia de 3 caixas.wav"
    c2 = "C\u00f3pia de 3 caixas 2.wav"
    assert families([c1]) == []
    assert families([f"{c1}_L", f"{c1}_R"]) == []
    paired = families([c1, c2])
    assert [f.label for f in paired] == ["3 caixas"]


def test_unrelated_files_and_family_score() -> None:
    unrelated = [
        "CANTO DOS PASS\u00c1ROS.aac",
        "WhatsApp Audio 2026-05-11 at 22.02.55.mpeg",
    ]
    assert families(unrelated) == []
    assert (family_score(2), family_score(3)) == (1, 2)


def test_familias_com_o_mesmo_trecho_comum_tem_chaves_distintas():
    nomes = ["folia 1.wav", "folia 2.wav", "folia amarela.wav", "folia azul.wav"]
    familias = families(nomes)
    assert len({f.key for f in familias}) == len(familias)
    assert len({f.label for f in familias}) == len(familias)


def test_chave_repetida_pula_sufixo_de_outra_familia():
    from aafton.organize_family import Family, _chaves_unicas

    def fam(key: str, arquivo: str) -> Family:
        return Family(key=key, label=key, files=(arquivo,), clip_count=1)

    saida = _chaves_unicas(
        [
            fam("folia", "a.wav"),
            fam("folia", "b.wav"),
            fam("folia 2", "c.wav"),
            fam("folia", "d.wav"),
        ]
    )
    assert [f.key for f in saida] == ["folia", "folia 3", "folia 2", "folia 4"]
    assert [f.label for f in saida] == ["folia", "folia 3", "folia 2", "folia 4"]
    assert [f.files for f in saida] == [("a.wav",), ("b.wav",), ("c.wav",), ("d.wav",)]


def test_family_by_key_nao_sobrescreve_com_sufixo_ocupado():
    from aafton.organize_family import Family, _chaves_unicas

    familias = _chaves_unicas(
        [
            Family(key="folia", label="folia", files=("a.wav", "b.wav"), clip_count=2),
            Family(key="folia 2", label="folia 2", files=("c.wav", "d.wav"), clip_count=2),
            Family(key="folia", label="folia", files=("e.wav", "f.wav"), clip_count=2),
        ]
    )
    por_chave = {fam.key: fam for fam in familias}
    assert len(por_chave) == 3
    assert sorted(f for fam in por_chave.values() for f in fam.files) == [
        "a.wav",
        "b.wav",
        "c.wav",
        "d.wav",
        "e.wav",
        "f.wav",
    ]
