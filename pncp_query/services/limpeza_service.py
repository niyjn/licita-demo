import csv
from pathlib import Path

from pncp_query.services.lead_candidate_filter import LeadCandidateFilter


class LimpezaService:
    def __init__(self, candidate_filter=None):
        self.candidate_filter = candidate_filter or LeadCandidateFilter()

    def gerar_csv_final(self, resultados_pdf, destino: Path):
        destino.parent.mkdir(parents=True, exist_ok=True)
        aprovados = set()
        for resultado in resultados_pdf:
            if not resultado.get("qualificado_ti", False):
                continue
            for cnpj in resultado.get("cnpjs_derrotados", []):
                decision = self.candidate_filter.evaluate(cnpj)
                if decision.accepted:
                    aprovados.add(decision.cnpj)
        cnpjs = sorted(aprovados)

        with destino.open("w", encoding="utf-8-sig", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=["cnpj"], delimiter=";")
            escritor.writeheader()
            for cnpj in cnpjs:
                escritor.writerow({"cnpj": cnpj})
        return cnpjs
