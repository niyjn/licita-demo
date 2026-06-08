from dataclasses import asdict, is_dataclass
from pathlib import Path

from pncp_query.config import AREAS, DB_PATH, PDF_DIR
from pncp_query.services.candidate_filter import CandidateFilter, cnpj_valido
from pncp_query.services.common import somente_digitos
from pncp_query.services.downloader_service import DownloaderService
from pncp_query.services.enrichment_service import EnrichmentService
from pncp_query.services.pdf_parser_service import PDFParserService
from pncp_query.services.pncp_search_service import PNCPSearchService
from pncp_query.services.resultado_service import ResultadoService
from pncp_query.services.storage import Storage


def analisar(area, data_inicial, data_final, uf, limite, db_path=DB_PATH, progress=None):
    if area not in AREAS:
        raise ValueError(f"Area desconhecida: {area}")

    db_path = Path(db_path)
    storage = Storage(db_path)
    search = PNCPSearchService()
    downloader = DownloaderService()
    parser = PDFParserService()
    resultados = ResultadoService()
    enrichment = EnrichmentService()
    filtro = CandidateFilter()

    _emit(progress, "busca", f"Buscando compras em {uf} para {area}.")
    compras = _buscar_compras(search, area, data_inicial, data_final, uf, limite, progress)

    contratos_salvos = 0
    participantes_salvos = 0
    total_compras = len(compras)

    for indice, compra in enumerate(compras, start=1):
        linha = _linha(compra)
        controle = linha.get("numero_controle_pncp") or _chave_linha(linha)
        _emit(progress, "contrato", f"Analisando {controle}.", indice, total_compras)

        chaves = downloader.resolver_chaves_compra(linha)
        if not chaves:
            _emit(progress, "ignorado", f"Compra sem chaves PNCP suficientes: {controle}.", indice, total_compras)
            continue

        try:
            adjudicatarios = resultados.adjudicatarios(*chaves)
        except Exception as exc:
            mensagem = f"Falha ao buscar resultado estruturado de {controle}: {exc}."
            _emit(progress, "erro", mensagem, indice, total_compras)
            adjudicatarios = []

        cnpjs_total = set()
        try:
            arquivos = downloader.listar_arquivos_relevantes(linha, PDF_DIR, chaves)
        except Exception as exc:
            _emit(progress, "erro", f"Falha ao listar arquivos de {controle}: {exc}.", indice, total_compras)
            arquivos = []

        for arquivo in arquivos:
            try:
                downloader.baixar(arquivo)
                if arquivo.destino.exists():
                    resultado_pdf = parser.extrair_resultado(arquivo.destino)
                    cnpjs_total.update(resultado_pdf.cnpjs_total)
            except Exception as exc:
                _emit(progress, "erro", f"Falha ao processar {arquivo.titulo}: {exc}.", indice, total_compras)

        participantes = _montar_participantes(
            adjudicatarios,
            cnpjs_total,
            linha.get("orgao_cnpj"),
            enrichment,
            filtro,
        )
        contrato = _montar_contrato(linha, chaves)
        storage.salvar_contrato(contrato, participantes)

        contratos_salvos += 1
        participantes_salvos += len(participantes)

    resumo = {"contratos": contratos_salvos, "participantes": participantes_salvos}
    _emit(progress, "concluido", "Analise concluida.", total_compras, total_compras)
    return resumo


def _buscar_compras(search, area, data_inicial, data_final, uf, limite, progress):
    vistas = set()
    compras = []
    for compra in search.buscar_iter(
        _data_str(data_inicial),
        _data_str(data_final),
        limite_por_combinacao=limite,
        pausa=0,
        logger=lambda mensagem: _emit(progress, "busca", mensagem),
        palavras_chave=AREAS[area],
        ufs=uf,
    ):
        linha = _linha(compra)
        chave = linha.get("numero_controle_pncp") or _chave_linha(linha)
        if not chave or chave in vistas:
            continue
        vistas.add(chave)
        compras.append(compra)
    _emit(progress, "busca", f"{len(compras)} compras unicas encontradas.")
    return compras


def _montar_participantes(adjudicatarios, cnpjs_total, orgao_cnpj, enrichment, filtro):
    participantes = []
    cnpjs_adjudicatarios = set()

    for item in adjudicatarios:
        cnpj = somente_digitos(item.get("cnpj"))
        if not cnpj or not cnpj_valido(cnpj):
            continue
        cnpjs_adjudicatarios.add(cnpj)
        participantes.append(
            {
                "cnpj": cnpj,
                "nome": item.get("nome", ""),
                "papel": "adjudicatario",
                "valor_homologado": item.get("valor_homologado"),
            }
        )

    for cnpj_bruto in sorted(cnpjs_total):
        decisao = filtro.evaluate(cnpj_bruto, buyer_org_cnpj=orgao_cnpj, source_org_cnpj=orgao_cnpj)
        if not decisao.accepted or decisao.cnpj in cnpjs_adjudicatarios:
            continue
        participantes.append(
            {
                "cnpj": decisao.cnpj,
                "nome": enrichment.nome(decisao.cnpj),
                "papel": "participante",
                "valor_homologado": None,
            }
        )

    return participantes


def _montar_contrato(linha, chaves):
    orgao_cnpj, ano, sequencial = chaves
    return {
        "numero_controle": linha.get("numero_controle_pncp") or _chave_linha(linha),
        "orgao_cnpj": orgao_cnpj,
        "orgao_nome": linha.get("orgao_nome", ""),
        "uf": linha.get("uf", ""),
        "municipio": linha.get("municipio_nome", ""),
        "ano": ano,
        "sequencial": sequencial,
        "objeto": linha.get("description") or linha.get("title", ""),
        "valor": linha.get("valor_global", ""),
        "data_publicacao": linha.get("data_publicacao_pncp", ""),
    }


def _linha(compra):
    if is_dataclass(compra):
        return asdict(compra)
    return dict(compra)


def _chave_linha(linha):
    return f"{somente_digitos(linha.get('orgao_cnpj'))}-{linha.get('ano')}-{linha.get('numero_sequencial')}"


def _data_str(valor):
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


def _emit(progress, etapa, mensagem, atual=None, total=None):
    if not progress:
        return
    progress({"etapa": etapa, "mensagem": mensagem, "atual": atual, "total": total})
