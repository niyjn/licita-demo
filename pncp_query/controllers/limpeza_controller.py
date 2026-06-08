from pathlib import Path

from pncp_query.config import CNPJS_BRUTOS_JSON, CNPJS_FINAIS_CSV
from pncp_query.services.csv_repository import ler_json
from pncp_query.services.limpeza_service import LimpezaService
from pncp_query.services.metrics_service import MetricsService


class LimpezaController:
    def __init__(self, service=None, lead_repository=None):
        self.service = service or LimpezaService()
        self.metrics = MetricsService()
        self.lead_repository = lead_repository

    def executar(self, origem=CNPJS_BRUTOS_JSON, destino=CNPJS_FINAIS_CSV, logger=print, run_id=None):
        if run_id and self.lead_repository:
            total_brutos = self.lead_repository.save_candidates_from_db_results(run_id)
            total_unicos = self.lead_repository.count_for_run(run_id)
            total_rejeitados = self.lead_repository.count_rejections_for_run(run_id)
            rejeicoes_por_motivo = self.lead_repository.count_rejections_by_reason(run_id)
            self.metrics.set("cnpjs_derrotados_brutos_ultima_limpeza", total_brutos)
            self.metrics.set("cnpjs_duplicados_removidos", max(0, total_brutos - total_rejeitados - total_unicos))
            self.metrics.set("cnpjs_finais_unicos", total_unicos)
            self.metrics.set("lead_candidates_rejected_total", total_rejeitados)
            self.metrics.set("lead_candidates_approved_total", total_unicos)
            metric_aliases = {"buyer_org_cnpj": "buyer_org", "source_org_cnpj": "source_org"}
            for motivo, total in rejeicoes_por_motivo.items():
                self.metrics.set(f"lead_candidates_rejected_{motivo}", total)
                if motivo in metric_aliases:
                    self.metrics.set(f"lead_candidates_rejected_{metric_aliases[motivo]}", total)
            logger(f"Candidatos persistidos no PostgreSQL: {total_unicos}")
            logger(f"Candidatos rejeitados por filtro local: {total_rejeitados}")
            return {
                "total_brutos": total_brutos,
                "cnpjs_finais_unicos": total_unicos,
                "rejeitados": total_rejeitados,
                "rejeicoes_por_motivo": rejeicoes_por_motivo,
            }

        resultados = []
        origem = Path(origem)
        if origem.exists():
            resultados = ler_json(origem)
        brutos = [
            cnpj
            for resultado in resultados
            if resultado.get("qualificado_ti", False)
            for cnpj in resultado.get("cnpjs_derrotados", [])
        ]
        cnpjs = self.service.gerar_csv_final(resultados, Path(destino))
        self.metrics.set("cnpjs_derrotados_brutos_ultima_limpeza", len(brutos))
        self.metrics.set("cnpjs_duplicados_removidos", max(0, len(brutos) - len(cnpjs)))
        self.metrics.set("cnpjs_finais_unicos", len(cnpjs))
        logger(f"CSV final: {destino}")
        logger(f"CNPJs derrotados unicos: {len(cnpjs)}")
        return cnpjs
