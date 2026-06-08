from pncp_query.services.candidate_filter import cnpj_valido


def test_cnpj_valido_aceita_com_e_sem_mascara():
    assert cnpj_valido("11.222.333/0001-81")
    assert cnpj_valido("11222333000181")


def test_cnpj_valido_rejeita_digito_invalido_e_repetido():
    assert not cnpj_valido("11.222.333/0001-82")
    assert not cnpj_valido("00.000.000/0000-00")
