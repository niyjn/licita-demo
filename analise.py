import unicodedata
from dataclasses import asdict, is_dataclass
from pathlib import Path

from pncp_query.config import AREAS, DB_PATH, PDF_DIR
from pncp_query.services.candidate_filter import cnpj_valido
from pncp_query.services.common import somente_digitos
from pncp_query.services.downloader_service import DownloaderService
from pncp_query.services.enrichment_service import EnrichmentService
from pncp_query.services.pdf_parser_service import PDFParserService
from pncp_query.services.pncp_search_service import PNCPSearchService
from pncp_query.services.resultado_service import ResultadoService
from pncp_query.services.storage import Storage

DESCARTE_DIRETO_TERMOS = (
    "dispensa",
    "inexigibilidade",
    "contratacao direta",
    "fornecedor exclusivo",
    "exclusividade",
    "notoria especializacao",
    "inviabilidade de competicao",
)


def analisar(area, data_inicial, data_final, uf, limite, db_path=DB_PATH, run_id=None, progress=None):
    if callable(run_id) and progress is None:
        progress = run_id
        run_id = None

    if area not in AREAS:
        raise ValueError(f"Área desconhecida: {area}")

    db_path = Path(db_path)
    storage = Storage(db_path)
    search = PNCPSearchService()
    downloader = DownloaderService()
    parser = PDFParserService()
    resultados = ResultadoService()
    enrichment = EnrichmentService()

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

        atas_lidas = 0
        atas_falhas = 0
        cnpjs_origem = {}
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
                    atas_lidas += 1
                    for cnpj in resultado_pdf.cnpjs_total:
                        cnpj_normalizado = somente_digitos(cnpj)
                        if cnpj_normalizado:
                            cnpjs_origem.setdefault(cnpj_normalizado, set()).add(arquivo.destino.name)
                else:
                    atas_falhas += 1
            except Exception as exc:
                atas_falhas += 1
                _emit(progress, "erro", f"Falha ao processar {arquivo.titulo}: {exc}.", indice, total_compras)

        auditoria = _montar_auditoria(
            adjudicatarios,
            set(cnpjs_origem),
            linha.get("orgao_cnpj"),
            enrichment,
            cnpjs_origem,
            atas_lidas,
            atas_falhas,
        )
        contrato = _montar_contrato(linha, chaves)
        contrato["run_id"] = run_id
        contrato["status"], contrato["motivo_status"] = _status_contrato(linha, auditoria)
        contrato_id = storage.salvar_contrato(contrato, auditoria["participantes"])
        if run_id:
            storage.salvar_cnpjs_auditoria(contrato_id, run_id, auditoria["registros"])
            storage.salvar_metricas_funil(contrato_id, run_id, auditoria["metricas"])

        contratos_salvos += 1
        participantes_salvos += len(auditoria["participantes"])

    resumo = {"contratos": contratos_salvos, "participantes": participantes_salvos}
    if run_id:
        resumo.update(storage.somar_metricas_run(run_id))
    _emit(progress, "concluido", "Análise concluída.", total_compras, total_compras)
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


def _montar_auditoria(adjudicatarios, cnpjs_ata, orgao_cnpj, enrichment, cnpjs_origem=None, atas_lidas=0, atas_falhas=0):
    participantes = []
    registros = []
    cnpjs_vencedores = set()
    cnpjs_origem = cnpjs_origem or {}

    for item in adjudicatarios:
        cnpj = somente_digitos(item.get("cnpj"))
        if not cnpj:
            continue
        cnpjs_vencedores.add(cnpj)
        dados_empresa = enrichment.consultar(cnpj)
        situacao = dados_empresa.get("situacao_cadastral", "")
        participantes.append(
            {
                "cnpj": cnpj,
                "nome": item.get("nome", "") or dados_empresa.get("razao_social", ""),
                "papel": "adjudicatario",
                "valor_homologado": item.get("valor_homologado"),
                "situacao_cadastral": situacao,
            }
        )
        registros.append(
            {
                "cnpj": cnpj,
                "nome": item.get("nome", "") or dados_empresa.get("razao_social", ""),
                "source": "estruturada",
                "disposition": "vencedor",
                "reason": "resultado_pncp_estruturado",
                "situacao_cadastral": situacao,
            }
        )

    cnpjs_ata_unicos = {somente_digitos(cnpj) for cnpj in cnpjs_ata if somente_digitos(cnpj)}
    restantes = set(cnpjs_ata_unicos)
    removido_invalido = {cnpj for cnpj in restantes if not cnpj_valido(cnpj)}
    restantes -= removido_invalido

    comprador_raiz = somente_digitos(orgao_cnpj)[:8]
    removido_orgao = {cnpj for cnpj in restantes if comprador_raiz and cnpj[:8] == comprador_raiz}
    restantes -= removido_orgao

    removido_vencedor = restantes & cnpjs_vencedores
    restantes -= removido_vencedor
    perdedores_final = restantes

    assert len(cnpjs_ata_unicos) == (
        len(removido_invalido) + len(removido_orgao) + len(removido_vencedor) + len(perdedores_final)
    )

    for disposition, cnpjs, reason in (
        ("removido_invalido", removido_invalido, "digito_verificador_invalido"),
        ("removido_orgao", removido_orgao, "orgao_comprador"),
        ("removido_vencedor", removido_vencedor, "coincidente_com_vencedor"),
    ):
        for cnpj in sorted(cnpjs):
            dados_empresa = {}
            if disposition != "removido_invalido":
                dados_empresa = enrichment.consultar(cnpj)
            nome = dados_empresa.get("razao_social", "")
            situacao = dados_empresa.get("situacao_cadastral", "")
            
            registro = _registro_ata(cnpj, disposition, reason, cnpjs_origem, situacao_cadastral=situacao)
            registro["nome"] = nome
            registros.append(registro)

    for cnpj in sorted(perdedores_final):
        dados_empresa = enrichment.consultar(cnpj)
        nome = dados_empresa.get("razao_social", "")
        situacao = dados_empresa.get("situacao_cadastral", "")
        participantes.append(
            {
                "cnpj": cnpj,
                "nome": nome,
                "papel": "participante",
                "valor_homologado": None,
                "situacao_cadastral": situacao,
            }
        )
        registro = _registro_ata(cnpj, "perdedor_final", "cnpj_valido_da_ata", cnpjs_origem, situacao_cadastral=situacao)
        registro["nome"] = nome
        registros.append(registro)

    metricas = {
        "atas_lidas": atas_lidas,
        "atas_falhas": atas_falhas,
        "cnpjs_ata_unicos": len(cnpjs_ata_unicos),
        "removido_invalido": len(removido_invalido),
        "removido_orgao": len(removido_orgao),
        "removido_vencedor": len(removido_vencedor),
        "perdedores_final": len(perdedores_final),
        "vencedores": len(cnpjs_vencedores),
        "resultado_final": len(perdedores_final) + len(cnpjs_vencedores),
    }
    return {"participantes": participantes, "registros": registros, "metricas": metricas}


def _registro_ata(cnpj, disposition, reason, cnpjs_origem, situacao_cadastral=""):
    return {
        "cnpj": cnpj,
        "source": "ata",
        "disposition": disposition,
        "reason": reason,
        "origin_file": ",".join(sorted(cnpjs_origem.get(cnpj, []))),
    }


def _status_contrato(linha, auditoria):
    motivo_descarte = _motivo_descarte(linha)
    if motivo_descarte:
        return "descartado", motivo_descarte
    if auditoria["metricas"]["vencedores"] == 0:
        return "vazio", "sem_vencedor_estruturado"
    if auditoria["metricas"]["perdedores_final"] == 0:
        return "vazio", "sem_perdedores_na_ata"
    return "final", ""


def _motivo_descarte(linha):
    texto = _normalizar(
        " ".join(
            str(linha.get(campo, ""))
            for campo in ("modalidade_licitacao_nome", "title", "description", "situacao_nome")
        )
    )
    for termo in DESCARTE_DIRETO_TERMOS:
        if termo in texto:
            return f"contratacao_direta_ou_exclusividade:{termo}"
    return ""


def _normalizar(valor):
    sem_acento = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


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
        "item_url": linha.get("item_url", ""),
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
