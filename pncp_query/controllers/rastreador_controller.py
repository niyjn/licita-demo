from dataclasses import asdict

from pncp_query.config import LICITACOES_CSV, LICITACOES_JSON
from pncp_query.services.csv_repository import append_csv_dict, append_jsonl, preparar_csv, salvar_json
from pncp_query.services.metrics_service import MetricsService
from pncp_query.services.pncp_search_service import PNCPSearchService

COLUNAS_LICITACAO = [
    "termo_busca",
    "status_busca",
    "tipo_documento_busca",
    "numero_controle_pncp",
    "orgao_cnpj",
    "ano",
    "numero_sequencial",
    "orgao_nome",
    "uf",
    "municipio_nome",
    "modalidade_licitacao_nome",
    "situacao_nome",
    "valor_global",
    "data_publicacao_pncp",
    "data_atualizacao_pncp",
    "title",
    "description",
    "item_url",
]


class RastreadorController:
    def __init__(self, service=None, search_repository=None):
        self.service = service or PNCPSearchService()
        self.metrics = MetricsService()
        self.search_repository = search_repository

    def executar(self, data_inicial, data_final, limite, pausa, logger=print, run_id=None):
        logger(f"Iniciando busca PNCP de {data_inicial} a {data_final}.")
        self.metrics.reset()
        usar_banco = bool(run_id and self.search_repository)
        registros = []
        batch = []
        persistidos = 0
        vistos = set()
        jsonl_path = LICITACOES_JSON.with_suffix(".jsonl")
        if not usar_banco:
            preparar_csv(LICITACOES_CSV, COLUNAS_LICITACAO)
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            jsonl_path.write_text("", encoding="utf-8")

        for registro in self.service.buscar_iter(data_inicial, data_final, limite, pausa, logger):
            linha = asdict(registro)
            chave = (
                linha.get("numero_controle_pncp"),
                linha.get("orgao_cnpj"),
                linha.get("ano"),
                linha.get("numero_sequencial"),
            )
            self.metrics.increment("licitacoes_encontradas")
            if chave in vistos:
                self.metrics.increment("licitacoes_duplicadas")
                continue
            vistos.add(chave)
            if usar_banco:
                batch.append(linha)
                if len(batch) >= 100:
                    self.search_repository.save_results(run_id, batch)
                    persistidos += len(batch)
                    batch.clear()
            else:
                registros.append(registro)
                append_csv_dict(LICITACOES_CSV, linha, COLUNAS_LICITACAO)
                append_jsonl(jsonl_path, linha)
            self.metrics.increment("licitacoes_persistidas")

        if usar_banco and batch:
            self.search_repository.save_results(run_id, batch)
            persistidos += len(batch)
        if not usar_banco:
            salvar_json(LICITACOES_JSON, [asdict(registro) for registro in registros])
        self.metrics.set("periodo_data_inicial", data_inicial)
        self.metrics.set("periodo_data_final", data_final)
        self.metrics.set("licitacoes_unicas", persistidos if usar_banco else len(registros))
        if usar_banco:
            logger(f"Resultados persistidos no PostgreSQL: {persistidos}")
            return persistidos
        logger(f"CSV para downloader: {LICITACOES_CSV}")
        logger(f"JSON bruto: {LICITACOES_JSON}")
        logger(f"JSONL incremental: {jsonl_path}")
        return registros
