import csv

from pncp_query.services.limpeza_service import LimpezaService


def test_gerar_csv_final_filtra_deduplica_e_rejeita_invalidos(tmp_path):
    destino = tmp_path / "leads.csv"
    resultados = [
        {
            "qualificado_ti": True,
            "cnpjs_derrotados": [
                "11.222.333/0001-81",
                "11222333000181",
                "11.222.333/0001-82",
            ],
        },
        {"qualificado_ti": False, "cnpjs_derrotados": ["00.000.000/0000-00"]},
    ]

    cnpjs = LimpezaService().gerar_csv_final(resultados, destino)

    assert cnpjs == ["11222333000181"]
    with destino.open(encoding="utf-8-sig", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    assert linhas == [{"cnpj": "11222333000181"}]
