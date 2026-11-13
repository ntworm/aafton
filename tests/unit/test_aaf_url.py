def test_url_para_caminho_remove_barra_inicial_indevida_em_drive_percent_encoded():
    from aafton.aaf_read import _url_para_caminho
    from pathlib import Path

    # 1. Resolve (letra literal)
    resolve_url = "file:///C:/Users/editor/Desktop/PROJECT%20EP2/AAF_NOVO/A1-0001_x.wav"
    resolve_path = _url_para_caminho(resolve_url)
    assert Path(resolve_path).is_absolute()
    assert Path(resolve_path).drive == "C:"

    # 2. Premiere (letra percent-encoded)
    premiere_url = "file:///D%3a/PROJECT_EP02S01_MASTER/EXPORT/AAF%20Media/7d2b3431.aif"
    premiere_path = _url_para_caminho(premiere_url)
    assert Path(premiere_path).is_absolute()
    assert Path(premiere_path).drive == "D:"

    # 3. Pro Tools (letra minuscula percent-encoded)
    pt_url = "file:///c%3a/Users/editor/Desktop/VIDEO_PROJECTS/PROJECT_EP2/AAF_TESTE/x.wav"
    pt_path = _url_para_caminho(pt_url)
    assert Path(pt_path).is_absolute()
    assert Path(pt_path).drive.lower() == "c:"

    # 4. UNC (sem letra de drive) - o lookahead garante que nao remove a barra inicial dupla
    unc_url = "file:////myserver/myshare/media/x.wav"
    unc_path = _url_para_caminho(unc_url)
    # nturl2path converte /myserver/myshare para \\myserver\myshare
    # A barra nao pode ser devorada
    assert Path(unc_path).is_absolute()
    # Em Windows Path("\\myserver\myshare...").drive e o proprio share "\\myserver\myshare"
    # Entao deve manter a formatacao de rede
    assert unc_path.startswith(r"\\") or unc_path.startswith("//")
