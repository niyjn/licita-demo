from pncp_query.services.qualification_service import QualificationService


def test_qualifica_texto_com_termo_ti():
    resultado = QualificationService().qualificar_ti("Contratacao de licenciamento de software para gestao municipal")

    assert resultado["qualificado"]
    assert "licenciamento" in resultado["inclusoes"]


def test_reprova_quando_ha_exclusao_setorial():
    resultado = QualificationService().qualificar_ti("Servico de software com vigilancia e portaria")

    assert not resultado["qualificado"]
    assert "software" in resultado["inclusoes"]
    assert "vigilancia" in resultado["exclusoes"]


def test_normaliza_acentos_e_caixa():
    resultado = QualificationService().qualificar_ti("CONTRATACAO DE SUPORTE TECNICO E SERVIDOR")

    assert resultado["qualificado"]
    assert "suporte tecnico" in resultado["inclusoes"]


def test_nao_qualifica_termo_parcial():
    resultado = QualificationService().qualificar_ti("Servico de bateria e consultorio medico")

    assert not resultado["qualificado"]
    assert "software" not in resultado["inclusoes"]
