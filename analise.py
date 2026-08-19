import unicodedata
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from pathlib import Path

from pncp_query.config import AREAS, DB_PATH, PDF_DIR, PDF_FALLBACK_MAX_FILES
from pncp_query.models import LoteArquivosPNCP
from pncp_query.services.candidate_filter import cnpj_valido
from pncp_query.services.common import somente_digitos
from pncp_query.services.downloader_service import DocumentoInvalidoError, DownloaderService
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


def analisar(area, data_inicial, data_final, uf, limite, db_path=DB_PATH, run_id=None, progress=None, termos=None):
    if callable(run_id) and progress is None:
        progress = run_id
        run_id = None

    if not termos and area not in AREAS:
        raise ValueError(f"Área desconhecida: {area}")
    palavras_chave = list(termos) if termos else AREAS[area]

    db_path = Path(db_path)
    storage = Storage(db_path)
    search = PNCPSearchService()
    downloader = DownloaderService()
    parser = PDFParserService()
    resultados = ResultadoService()
    enrichment = EnrichmentService()

    rotulo_busca = ", ".join(palavras_chave) if termos else area
    _emit(progress, "busca", f"Buscando compras em {uf} para {rotulo_busca}.")
    compras = _buscar_compras(search, palavras_chave, data_inicial, data_final, uf, limite, progress)

    contratos_salvos = 0
    participantes_salvos = 0
    total_compras = len(compras)

    for indice, compra in enumerate(compras, start=1):
        linha = _linha(compra)
        controle = linha.get("numero_controle_pncp") or _chave_linha(linha)
        _emit(progress, "contrato", f"Analisando {controle}.", indice, total_compras)

        motivo_descarte = _motivo_descarte(linha)
        if motivo_descarte:
            chaves = _chaves_local(linha)
            contrato = _montar_contrato(linha, chaves)
            contrato["run_id"] = run_id
            contrato["status"], contrato["motivo_status"] = "descartado", motivo_descarte
            contrato_id = storage.salvar_contrato(contrato, [])
            if run_id:
                storage.salvar_metricas_funil(contrato_id, run_id, _metricas_vazias())
            contratos_salvos += 1
            _emit(progress, "ignorado", f"Compra descartada por regra direta: {controle}.", indice, total_compras)
            continue

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

        evidencias = []
        metricas_documentos = {
            "atas_lidas": 0,
            "atas_falhas": 0,
            "documentos_listados": 0,
            "documentos_prioritarios_lidos": 0,
            "documentos_fallback_lidos": 0,
            "documentos_ignorados": 0,
            "documentos_duplicados": 0,
        }
        try:
            lote = downloader.listar_arquivos_candidatos(linha, PDF_DIR, chaves)
            metricas_documentos["documentos_listados"] = (
                len(lote.prioritarios) + len(lote.fallback) + lote.ignorados
            )
            metricas_documentos["documentos_ignorados"] = lote.ignorados
        except Exception as exc:
            _emit(progress, "erro", f"Falha ao listar arquivos de {controle}: {exc}.", indice, total_compras)
            lote = LoteArquivosPNCP()

        hashes_processados = set()
        _processar_documentos(
            lote.prioritarios,
            "priority",
            downloader,
            parser,
            evidencias,
            metricas_documentos,
            hashes_processados,
            progress,
            indice,
            total_compras,
        )
        if not _ha_perdedor_confirmavel(adjudicatarios, evidencias, linha.get("orgao_cnpj")):
            _processar_documentos(
                lote.fallback[:PDF_FALLBACK_MAX_FILES],
                "fallback",
                downloader,
                parser,
                evidencias,
                metricas_documentos,
                hashes_processados,
                progress,
                indice,
                total_compras,
            )

        cnpjs_origem = {}
        for evidencia in evidencias:
            cnpjs_origem.setdefault(evidencia["cnpj"], set()).add(evidencia["origin_file"])

        auditoria = _montar_auditoria(
            adjudicatarios,
            set(cnpjs_origem),
            linha.get("orgao_cnpj"),
            enrichment,
            cnpjs_origem,
            metricas_documentos["atas_lidas"],
            metricas_documentos["atas_falhas"],
            evidencias=evidencias,
        )
        auditoria["metricas"].update(metricas_documentos)
        contrato = _montar_contrato(linha, chaves)
        contrato["run_id"] = run_id
        contrato["status"], contrato["motivo_status"] = _status_contrato(auditoria)
        contrato_id = storage.salvar_contrato(contrato, auditoria["participantes"])
        if run_id:
            storage.salvar_cnpjs_auditoria(contrato_id, run_id, auditoria["registros"])
            storage.salvar_evidencias_cnpj(contrato_id, run_id, evidencias)
            storage.salvar_metricas_funil(contrato_id, run_id, auditoria["metricas"])

        contratos_salvos += 1
        participantes_salvos += len(auditoria["participantes"])

        # Limpeza efêmera do disco local em produção (quando S3 estiver ativo)
        from pncp_query.config import S3_BUCKET_NAME
        if S3_BUCKET_NAME:
            for arquivo in (lote.prioritarios + lote.fallback):
                if hasattr(arquivo, "destino") and isinstance(arquivo.destino, Path):
                    try:
                        arquivo.destino.unlink(missing_ok=True)
                    except Exception:
                        pass

    resumo = {"contratos": contratos_salvos, "participantes": participantes_salvos}
    if run_id:
        resumo.update(storage.somar_metricas_run(run_id))
    _emit(progress, "concluido", "Análise concluída.", total_compras, total_compras)
    return resumo


def _buscar_compras(search, palavras_chave, data_inicial, data_final, uf, limite, progress):
    vistas = set()
    compras = []
    for compra in search.buscar_iter(
        _data_str(data_inicial),
        _data_str(data_final),
        limite_por_combinacao=limite,
        pausa=0,
        logger=lambda mensagem: _emit(progress, "busca", mensagem),
        palavras_chave=palavras_chave,
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


def _montar_auditoria(
    adjudicatarios,
    cnpjs_ata,
    orgao_cnpj,
    enrichment,
    cnpjs_origem=None,
    atas_lidas=0,
    atas_falhas=0,
    evidencias=None,
):
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
    if evidencias is None:
        evidencias = [
            {"cnpj": cnpj, "category": "participante", "signal": "compatibilidade"}
            for cnpj in cnpjs_ata_unicos
        ]
    evidencias_por_cnpj = {}
    for evidencia in evidencias:
        evidencias_por_cnpj.setdefault(evidencia["cnpj"], []).append(evidencia)
    restantes = set(cnpjs_ata_unicos)
    removido_invalido = {cnpj for cnpj in restantes if not cnpj_valido(cnpj)}
    restantes -= removido_invalido

    comprador_raiz = somente_digitos(orgao_cnpj)[:8]
    removido_orgao = {cnpj for cnpj in restantes if comprador_raiz and cnpj[:8] == comprador_raiz}
    restantes -= removido_orgao

    removido_vencedor = restantes & cnpjs_vencedores
    restantes -= removido_vencedor
    cnpjs_vencedores_inferidos = set()
    if not cnpjs_vencedores:
        for cnpj in sorted(restantes):
            itens = evidencias_por_cnpj.get(cnpj, [])
            if any(item.get("category") == "vencedor" for item in itens):
                cnpjs_vencedores_inferidos.add(cnpj)
        restantes -= cnpjs_vencedores_inferidos
    perdedores_final = set()
    candidatos_inconclusivos = set()
    motivo_inconclusivo = {}
    for cnpj in restantes:
        itens = evidencias_por_cnpj.get(cnpj, [])
        tem_participacao = any(item.get("category") == "participante" for item in itens)
        tem_conflito = any(item.get("category") == "conflitante" for item in itens)
        if not cnpjs_vencedores and not cnpjs_vencedores_inferidos:
            candidatos_inconclusivos.add(cnpj)
            motivo_inconclusivo[cnpj] = "vencedores_indisponiveis"
        elif tem_conflito:
            candidatos_inconclusivos.add(cnpj)
            motivo_inconclusivo[cnpj] = "evidencia_conflitante"
        elif not tem_participacao:
            candidatos_inconclusivos.add(cnpj)
            motivo_inconclusivo[cnpj] = "sem_contexto_explicito"
        else:
            perdedores_final.add(cnpj)

    expected_sum = (
        len(removido_invalido)
        + len(removido_orgao)
        + len(removido_vencedor)
        + len(cnpjs_vencedores_inferidos)
        + len(candidatos_inconclusivos)
        + len(perdedores_final)
    )
    if len(cnpjs_ata_unicos) != expected_sum:
        raise ValueError(
            f"Erro de consistência no funil: total único {len(cnpjs_ata_unicos)} "
            f"não coincide com a soma das partes ({expected_sum})"
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

    for cnpj in sorted(cnpjs_vencedores_inferidos):
        dados_empresa = enrichment.consultar(cnpj)
        nome = dados_empresa.get("razao_social", "")
        situacao = dados_empresa.get("situacao_cadastral", "")
        participantes.append(
            {
                "cnpj": cnpj,
                "nome": nome,
                "papel": "adjudicatario",
                "valor_homologado": None,
                "situacao_cadastral": situacao,
            }
        )
        registro = _registro_ata(
            cnpj,
            "vencedor_inferido",
            "contratada_inferida_da_ata",
            cnpjs_origem,
            situacao_cadastral=situacao,
        )
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
        registro = _registro_ata(
            cnpj,
            "perdedor_final",
            "evidencia_explicita_participacao",
            cnpjs_origem,
            situacao_cadastral=situacao,
        )
        registro["nome"] = nome
        registros.append(registro)

    for cnpj in sorted(candidatos_inconclusivos):
        registro = _registro_ata(
            cnpj,
            "candidato_inconclusivo",
            motivo_inconclusivo[cnpj],
            cnpjs_origem,
        )
        registros.append(registro)

    metricas = {
        "atas_lidas": atas_lidas,
        "atas_falhas": atas_falhas,
        "cnpjs_ata_unicos": len(cnpjs_ata_unicos),
        "removido_invalido": len(removido_invalido),
        "removido_orgao": len(removido_orgao),
        "removido_vencedor": len(removido_vencedor),
        "candidatos_inconclusivos": len(candidatos_inconclusivos),
        "perdedores_final": len(perdedores_final),
        "vencedores": len(cnpjs_vencedores),
        "vencedores_inferidos": len(cnpjs_vencedores_inferidos),
        "resultado_final": len(perdedores_final) + len(cnpjs_vencedores) + len(cnpjs_vencedores_inferidos),
    }
    return {"participantes": participantes, "registros": registros, "metricas": metricas}


def _registro_ata(cnpj, disposition, reason, cnpjs_origem, situacao_cadastral=""):
    return {
        "cnpj": cnpj,
        "source": "ata",
        "disposition": disposition,
        "reason": reason,
        "origin_file": ",".join(sorted(cnpjs_origem.get(cnpj, []))),
        "situacao_cadastral": situacao_cadastral,
    }


def _status_contrato(auditoria):
    total_vencedores = auditoria["metricas"]["vencedores"] + auditoria["metricas"]["vencedores_inferidos"]
    if total_vencedores == 0:
        return "vazio", "vencedores_indisponiveis"
    if auditoria["metricas"]["perdedores_final"] == 0:
        return "vazio", "sem_perdedores_na_ata"
    return "final", ""


def _metricas_vazias():
    return {
        "atas_lidas": 0,
        "atas_falhas": 0,
        "cnpjs_ata_unicos": 0,
        "removido_invalido": 0,
        "removido_orgao": 0,
        "removido_vencedor": 0,
        "candidatos_inconclusivos": 0,
        "perdedores_final": 0,
        "vencedores": 0,
        "vencedores_inferidos": 0,
        "resultado_final": 0,
        "documentos_listados": 0,
        "documentos_prioritarios_lidos": 0,
        "documentos_fallback_lidos": 0,
        "documentos_ignorados": 0,
        "documentos_duplicados": 0,
    }


def _ha_perdedor_confirmavel(adjudicatarios, evidencias, orgao_cnpj):
    vencedores = {somente_digitos(item.get("cnpj")) for item in adjudicatarios}
    vencedores.discard("")
    if not vencedores:
        return False
    comprador_raiz = somente_digitos(orgao_cnpj)[:8]
    por_cnpj = {}
    for evidencia in evidencias:
        por_cnpj.setdefault(evidencia["cnpj"], []).append(evidencia)
    for cnpj, itens in por_cnpj.items():
        if not cnpj_valido(cnpj) or cnpj in vencedores:
            continue
        if comprador_raiz and cnpj[:8] == comprador_raiz:
            continue
        if any(item["category"] == "conflitante" for item in itens):
            continue
        if any(item["category"] == "participante" for item in itens):
            return True
    return False


def _processar_documentos(
    arquivos,
    scan_pass,
    downloader,
    parser,
    evidencias,
    metricas,
    hashes_processados,
    progress,
    indice,
    total_compras,
):
    for arquivo in arquivos:
        try:
            downloader.baixar(arquivo)
            if not arquivo.destino.exists():
                metricas["atas_falhas"] += 1
                continue
            digest = sha256(arquivo.destino.read_bytes()).hexdigest()
            if digest in hashes_processados:
                metricas["documentos_duplicados"] += 1
                continue
            hashes_processados.add(digest)
            resultado_pdf = parser.extrair_resultado(arquivo.destino)
            if resultado_pdf.erro:
                metricas["atas_falhas"] += 1
                continue
            metricas["atas_lidas"] += 1
            metricas[f"documentos_{'prioritarios' if scan_pass == 'priority' else 'fallback'}_lidos"] += 1
            for evidencia in resultado_pdf.evidencias:
                evidencias.append(
                    {
                        "cnpj": evidencia.cnpj,
                        "origin_file": arquivo.destino.name,
                        "scan_pass": scan_pass,
                        "page_number": evidencia.pagina,
                        "category": evidencia.categoria,
                        "signal": evidencia.sinal,
                        "excerpt": evidencia.trecho,
                    }
                )
        except DocumentoInvalidoError as exc:
            metricas["documentos_ignorados"] += 1
            _emit(progress, "ignorado", f"Documento ignorado: {exc}.", indice, total_compras)
        except Exception as exc:
            metricas["atas_falhas"] += 1
            _emit(progress, "erro", f"Falha ao processar {arquivo.titulo}: {exc}.", indice, total_compras)


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


def _chaves_local(linha):
    orgao_cnpj = somente_digitos(linha.get("orgao_cnpj"))
    ano = str(somente_digitos(linha.get("ano")))
    sequencial = str(int(somente_digitos(linha.get("numero_sequencial")) or "0"))
    return orgao_cnpj, ano, sequencial


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
