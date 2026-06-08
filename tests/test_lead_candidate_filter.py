from pncp_query.services.lead_candidate_filter import LeadCandidateFilter, cnpj_valido


def test_cnpj_valido_aceita_com_e_sem_mascara():
    assert cnpj_valido("11.222.333/0001-81")
    assert cnpj_valido("11222333000181")


def test_cnpj_valido_rejeita_digito_invalido_e_repetido():
    assert not cnpj_valido("11.222.333/0001-82")
    assert not cnpj_valido("00.000.000/0000-00")


def test_candidate_filter_rejeita_vazio_e_malformado():
    filtro = LeadCandidateFilter()

    assert filtro.evaluate("").reason == "empty_or_malformed"
    assert filtro.evaluate("123").reason == "empty_or_malformed"


def test_candidate_filter_rejeita_orgao_comprador_e_orgao_fonte():
    filtro = LeadCandidateFilter()

    assert filtro.evaluate("11222333000181", buyer_org_cnpj="11.222.333/0001-81").reason == "buyer_org_cnpj"
    assert filtro.evaluate("11222333000181", source_org_cnpj="11.222.333/0001-81").reason == "source_org_cnpj"


def test_candidate_filter_aceita_cnpj_normalizado():
    decision = LeadCandidateFilter().evaluate("11.222.333/0001-81")

    assert decision.accepted
    assert decision.cnpj == "11222333000181"
