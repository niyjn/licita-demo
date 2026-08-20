import json
import re
import unicodedata
from math import ceil
from uuid import uuid4

from psycopg2 import IntegrityError
from flask import Flask, jsonify, make_response, redirect, render_template, request, url_for

from pncp_query.config import AREAS, DATABASE_URL, UFS, janela_padrao, require_database_url
from pncp_query.services.storage import Storage

AREA_LABELS = {
    "TI": "TI",
    "ENGENHARIA": "Engenharia",
    "SAUDE": "Saúde",
}

DISPOSITION_LABELS = {
    "candidato_inconclusivo": "Candidato inconclusivo",
    "perdedor_final": "Perdedor final",
    "removido_invalido": "Removido por dígito inválido",
    "removido_orgao": "Removido por órgão comprador",
    "removido_vencedor": "Removido por vencedor",
    "vencedor_inferido": "Vencedor inferido",
    "vencedor": "Vencedor",
}

REASON_LABELS = {
    "cnpj_valido_da_ata": "CNPJ válido da ata",
    "coincidente_com_vencedor": "Coincide com vencedor",
    "digito_verificador_invalido": "Dígito verificador inválido",
    "evidencia_conflitante": "Evidência conflitante no documento",
    "evidencia_explicita_participacao": "Participação explícita no documento",
    "contratada_inferida_da_ata": "Contratada inferida da ata",
    "orgao_comprador": "Órgão comprador",
    "resultado_pncp_estruturado": "Resultado estruturado do PNCP",
    "sem_contexto_explicito": "CNPJ sem contexto explícito de participação",
    "sem_perdedores_na_ata": "Sem perdedores na ata",
    "sem_vencedor_estruturado": "Sem vencedor estruturado",
    "vencedores_indisponiveis": "Vencedores estruturados indisponíveis",
}

STATUS_LABELS = {
    "descartado": "Descartado",
    "final": "Final",
    "vazio": "Vazio",
}

RUN_STATUS_LABELS = {
    "queued": "Na fila",
    "running": "Em execução",
    "done": "Concluída",
    "error": "Erro",
}

RUN_STATUS_TONES = {
    "queued": "amber",
    "running": "amber",
    "done": "green",
    "error": "red",
}

_MOTIVO_CURTO = {
    "contratacao_direta_ou_exclusividade:dispensa": "Dispensa",
    "contratacao_direta_ou_exclusividade:inexigibilidade": "Inexigibilidade",
    "contratacao_direta_ou_exclusividade:contratacao direta": "Contratação direta",
    "contratacao_direta_ou_exclusividade:exclusividade": "Exclusividade",
    "contratacao_direta_ou_exclusividade:notoria especializacao": "Notória especialização",
    "contratacao_direta_ou_exclusividade:inviabilidade de competicao": "Inviabilidade de competição",
    "contratacao_direta_ou_exclusividade:fornecedor exclusivo": "Fornecedor exclusivo",
    "sem_perdedores_na_ata": "Sem ata publicada",
    "vencedores_indisponiveis": "Resultado indisponível",
}


def create_app(config=None):
    from markupsafe import Markup, escape

    app = Flask(
        __name__,
        static_folder="design-system",
        static_url_path="/design-system",
        template_folder="templates",
    )
    app.config.update(DATABASE_URL=DATABASE_URL)
    if config:
        app.config.update(config)
    storage = app.config.pop("STORAGE", None) or Storage(app.config.get("DATABASE_URL") or require_database_url())
    app.extensions["storage"] = storage

    @app.template_filter("highlight_signal")
    def highlight_signal_filter(excerpt, signal):
        """Wraps the first occurrence of *signal* in the excerpt with <mark>."""
        if not signal or not excerpt:
            return Markup(escape(excerpt or ""))
        lower_excerpt = excerpt.lower()
        lower_signal = signal.lower()
        idx = lower_excerpt.find(lower_signal)
        if idx < 0:
            return Markup(escape(excerpt))
        before = excerpt[:idx]
        match = excerpt[idx : idx + len(signal)]
        after = excerpt[idx + len(signal) :]
        return Markup(
            str(escape(before))
            + '<mark style="background:var(--warning);color:var(--warning-text);padding:0 2px;">'
            + str(escape(match))
            + "</mark>"
            + str(escape(after))
        )

    @app.get("/")
    def index():
        run = storage.ultima_run()
        if run:
            return _render_run(run["id"])
        return _render_dashboard(run=None)

    @app.get("/analises/<run_id>")
    def ver_analise(run_id):
        return _render_run(run_id)

    @app.get("/runs")
    def listar_runs():
        try:
            pagina = max(1, int(request.args.get("page", 1)))
        except ValueError:
            pagina = 1
        status = request.args.get("status") or None
        if status not in {None, *RUN_STATUS_LABELS}:
            return jsonify({"error": "invalid_status"}), 400

        por_pagina = 20
        total = storage.contar_runs(status=status)
        total_paginas = max(1, ceil(total / por_pagina))
        pagina = min(pagina, total_paginas)
        runs = storage.listar_runs(
            limit=por_pagina,
            offset=(pagina - 1) * por_pagina,
            status=status,
        )
        for run in runs:
            run["status_label"] = RUN_STATUS_LABELS.get(run["status"], run["status"])
            run["status_tone"] = RUN_STATUS_TONES.get(run["status"], "muted")
            run["params"] = _json_objeto(run.get("params_json"))
            run["titulo"] = _titulo_run(run["params"])
        return render_template(
            "runs.html",
            runs=runs,
            pagina=pagina,
            total_paginas=total_paginas,
            total=total,
            status=status,
        )

    @app.post("/analises/<run_id>/excluir")
    def excluir_analise(run_id):
        run = storage.obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        if run["status"] in {"queued", "running"}:
            return jsonify({"error": "run_active", "message": "Uma análise ativa não pode ser excluída."}), 409
        storage.excluir_run(run_id)
        return redirect(url_for("listar_runs"))

    @app.get("/analises/<run_id>/cnpjs")
    def listar_cnpjs(run_id):
        disposition = request.args.get("disposition")
        registros = storage.listar_cnpjs_auditoria(run_id, disposition=disposition)
        evidencias = {}
        for evidencia in storage.listar_evidencias_cnpj(run_id):
            evidencias.setdefault(evidencia["cnpj"], []).append(evidencia)
        for registro in registros:
            registro["evidencias"] = evidencias.get(registro["cnpj"], [])
        return jsonify({"run_id": run_id, "disposition": disposition, "cnpjs": registros})

    def _render_run(run_id):
        run = storage.obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        return _render_dashboard(run)

    def _render_dashboard(run):
        run_params = None
        if run and run.get("params_json"):
            try:
                run_params = json.loads(run["params_json"])
            except Exception:
                pass

        uf = request.args.get("uf")
        if not uf and run_params:
            uf = run_params.get("uf")
        if not uf or uf not in UFS:
            uf = "SP"

        incluir_ocultos = request.args.get("mostrar") == "ocultos"
        todos = _listar_contratos(storage, uf, run_id=run["id"] if run else None, incluir_ocultos=True)
        contratos_descartados = [c for c in todos if c.get("status") == "descartado"]
        contratos = [
            c for c in todos
            if c.get("status") != "descartado" and (incluir_ocultos or c.get("status") == "final")
        ]
        resumo = _resumo_run(storage, run) if run else _resumo_contratos(contratos)
        documentos = _documentos_extraidos(storage, run["id"]) if run else []
        funil_contratos = _funil_contratos_run(storage, run["id"]) if run else None
        return render_template(
            "index.html",
            areas=[{"value": area, "label": AREA_LABELS.get(area, area)} for area in AREAS],
            ufs=UFS,
            uf_atual=uf,
            periodo_padrao=janela_padrao(),
            contratos=[_contrato_view(contrato) for contrato in contratos],
            contratos_descartados=[_contrato_view(contrato) for contrato in contratos_descartados],
            resumo=resumo,
            run=run,
            run_params=run_params,
            incluir_ocultos=incluir_ocultos,
            documentos=documentos,
            funil_contratos=funil_contratos,
            reason_labels=REASON_LABELS,
        )

    @app.post("/analises")
    def criar_analise():
        try:
            payload = _payload_analise()
        except ValueError as e:
            return jsonify({"error": "invalid_input", "message": str(e)}), 400
        run_id = uuid4().hex
        storage.limpar_runs_travadas(timeout_segundos=3600)
        storage.criar_run_se_disponivel(run_id, params_json=_params_json(payload))
        return jsonify({"run_id": run_id}), 202

    @app.get("/perfis")
    def listar_perfis():
        perfis = []
        for perfil in storage.listar_perfis():
            perfil["termos"] = _json_lista(perfil.pop("termos_json", "[]"))
            perfis.append(perfil)
        return jsonify({"perfis": perfis})

    @app.post("/perfis")
    def criar_perfil():
        data = request.get_json(silent=True) or request.form
        nome = " ".join((data.get("nome") or "").split())
        if not nome or len(nome) > 80:
            return jsonify({"error": "invalid_name", "message": "Informe um nome de até 80 caracteres."}), 400
        try:
            termos = _normalizar_termos(data.get("termos"))
        except ValueError as exc:
            return jsonify({"error": "invalid_terms", "message": str(exc)}), 400
        try:
            perfil_id = storage.salvar_perfil(nome, termos)
        except IntegrityError:
            return jsonify({"error": "duplicate_name", "message": "Já existe um modelo com esse nome."}), 409
        return jsonify({"id": perfil_id, "nome": nome, "termos": termos}), 201

    @app.delete("/perfis/<int:perfil_id>")
    def excluir_perfil(perfil_id):
        if not storage.excluir_perfil(perfil_id):
            return jsonify({"error": "profile_not_found"}), 404
        return "", 204

    @app.get("/analises/<run_id>/status")
    def status_analise(run_id):
        run = storage.obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        return jsonify(
            {
                "run_id": run["id"],
                "status": run["status"],
                "progress": run["progress"],
                "message": run["message"],
                "error": run["error"],
                "started_at": run["started_at"],
                "duration_seconds": run.get("duration_seconds", 0),
            }
        )

    @app.get("/analises/<run_id>/exportar")
    def exportar_analise(run_id):
        run = storage.obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        
        contratos = storage.listar_contratos(run_id=run_id, incluir_ocultos=True)
        auditorias = storage.listar_cnpjs_auditoria(run_id)
        
        # Mapear portal_url para os contratos na exportação também
        for c in contratos:
            cnpj_limpo = re.sub(r"\D", "", c.get("orgao_cnpj") or "")
            c["portal_url"] = f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{c.get('ano')}/{c.get('sequencial')}"
            
        dados = {
            "run": run,
            "contratos": contratos,
            "auditorias": auditorias
        }
        
        resposta = make_response(json.dumps(dados, indent=2, ensure_ascii=False))
        resposta.headers["Content-Disposition"] = f"attachment; filename=analise-pncp-{run_id}.json"
        resposta.headers["Content-Type"] = "application/json"
        return resposta

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/readyz")
    def readyz():
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            storage.ping()
            with storage.connect() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version LIMIT 1")
                revision = cursor.fetchone()
            alembic_config = Config(str(app.root_path + "/alembic.ini"))
            ready = revision is not None and revision["version_num"] == ScriptDirectory.from_config(alembic_config).get_current_head()
        except Exception:
            ready = False
        if not ready:
            return jsonify({"status": "not_ready"}), 503
        return jsonify({"status": "ready"})

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template(
            "erro.html",
            codigo=404,
            titulo="Página não encontrada",
            mensagem="O link que você seguiu pode estar quebrado ou a página foi removida.",
        ), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template(
            "erro.html",
            codigo=500,
            titulo="Erro interno do processador",
            mensagem="Ocorreu uma falha inesperada nos nossos servidores. Nossa equipe de análise foi notificada.",
        ), 500

    return app


def _listar_contratos(storage, uf, run_id=None, incluir_ocultos=True):
    return storage.listar_contratos(uf, run_id=run_id, incluir_ocultos=incluir_ocultos)


def _resumo_contratos(contratos):
    vencedores = 0
    perdedores = 0
    for contrato in contratos:
        for participante in contrato.get("participantes", []):
            if participante.get("papel") == "adjudicatario":
                vencedores += 1
            else:
                perdedores += 1
    return {
        "contratos": len(contratos),
        "vencedores": vencedores,
        "perdedores": perdedores,
        "resultado_final": vencedores + perdedores,
    }


def _funil_contratos_run(storage, run_id):
    rows = storage.contar_contratos_status(run_id)
    funil = {"total": 0, "final": 0, "vazio": [], "descartado": []}
    for row in rows:
        funil["total"] += row["total"]
        status = row["status"]
        motivo = row["motivo_status"] or ""
        entry = {"motivo": motivo, "label": _rotulo_motivo_curto(motivo), "total": row["total"]}
        if status == "final":
            funil["final"] += row["total"]
        elif status == "vazio":
            funil["vazio"].append(entry)
        elif status == "descartado":
            funil["descartado"].append(entry)
    funil["descartado_total"] = sum(e["total"] for e in funil["descartado"])
    funil["vazio_total"] = sum(e["total"] for e in funil["vazio"])
    return funil if funil["total"] > 0 else None


def _rotulo_motivo_curto(motivo):
    if not motivo:
        return ""
    return _MOTIVO_CURTO.get(motivo) or _rotulo_motivo(motivo)


def _resumo_run(storage, run):
    metricas = storage.somar_metricas_run(run["id"])
    return {
        "contratos": len(storage.listar_contratos(run_id=run["id"], incluir_ocultos=True)),
        **metricas,
    }


def _contrato_view(contrato):
    contrato = dict(contrato)
    contrato["titulo_limpo"] = _titulo_limpo(contrato)
    contrato["vencedores"] = [p for p in contrato.get("participantes", []) if p.get("papel") == "adjudicatario"]
    contrato["perdedores"] = [p for p in contrato.get("participantes", []) if p.get("papel") != "adjudicatario"]
    contrato["status_label"] = STATUS_LABELS.get(contrato.get("status"), contrato.get("status", ""))
    contrato["motivo_status_label"] = _rotulo_motivo(contrato.get("motivo_status"))
    
    # URL oficial do portal do PNCP para o edital
    cnpj_limpo = re.sub(r"\D", "", contrato.get("orgao_cnpj") or "")
    contrato["portal_url"] = f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{contrato.get('ano')}/{contrato.get('sequencial')}"
    
    # Rótulos para os registros de auditoria do contrato
    for item in contrato.get("auditoria", []):
        item["disposition_label"] = DISPOSITION_LABELS.get(item.get("disposition"), item.get("disposition", ""))
        item["reason_label"] = _rotulo_motivo(item.get("reason"))
        item["evidencias"] = [
            evidencia
            for evidencia in contrato.get("evidencias", [])
            if evidencia.get("cnpj") == item.get("cnpj")
        ]

    return contrato


def _documentos_extraidos(storage, run_id):
    documentos = {}
    for registro in storage.listar_cnpjs_auditoria(run_id):
        if registro.get("source") != "ata":
            continue
        arquivos = [item.strip() for item in (registro.get("origin_file") or "Ata sem nome").split(",") if item.strip()]
        for arquivo in arquivos:
            documento = documentos.setdefault(arquivo, {"arquivo": arquivo, "registros": []})
            registro = dict(registro)
            registro["disposition_label"] = DISPOSITION_LABELS.get(
                registro.get("disposition"),
                registro.get("disposition", ""),
            )
            registro["reason_label"] = _rotulo_motivo(registro.get("reason"))
            documento["registros"].append(registro)
    return sorted(documentos.values(), key=lambda item: item["arquivo"])


def _titulo_limpo(contrato):
    objeto = contrato.get("objeto") or ""
    texto = _normalizar_titulo(objeto)
    prefixos = (
        "contratacao de empresa especializada para",
        "contratacao de empresa para",
        "contratacao de servicos de",
        "registro de precos para",
        "aquisicao de",
    )
    for prefixo in prefixos:
        if _sem_acento(texto).lower().startswith(prefixo):
            texto = texto[len(prefixo) :].strip(" .:-")
            break
    if texto:
        texto = texto[0].upper() + texto[1:]
    if not texto:
        orgao = contrato.get("orgao_nome") or contrato.get("orgao_cnpj") or "Órgão"
        local = " / ".join(parte for parte in (contrato.get("municipio"), contrato.get("uf")) if parte)
        texto = f"{orgao} {local} {contrato.get('numero_controle', '')}".strip()
    return texto[:157] + "..." if len(texto) > 160 else texto


def _normalizar_titulo(valor):
    texto = " ".join(str(valor or "").split())
    if texto.isupper():
        texto = texto.capitalize()
    return _acentuar_termos_comuns(texto)


def _sem_acento(valor):
    return unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")


def _acentuar_termos_comuns(texto):
    termos = {
        "analise": "análise",
        "aquisicao": "aquisição",
        "contratacao": "contratação",
        "exibicao": "exibição",
        "gestao": "gestão",
        "inviabilidade de competicao": "inviabilidade de competição",
        "municipio": "município",
        "notoria especializacao": "notória especialização",
        "orgao": "órgão",
        "publica": "pública",
        "publico": "público",
        "servicos": "serviços",
        "tecnico": "técnico",
    }
    for origem, destino in termos.items():
        texto = re.sub(rf"\b{origem}\b", destino, texto, flags=re.IGNORECASE)
    return texto


def _rotulo_motivo(motivo):
    if not motivo:
        return ""
    if motivo in REASON_LABELS:
        return REASON_LABELS[motivo]
    if motivo.startswith("contratacao_direta_ou_exclusividade:"):
        termo = motivo.split(":", 1)[1].replace("_", " ")
        return f"Contratação direta ou exclusividade: {_acentuar_termos_comuns(termo)}"
    return _acentuar_termos_comuns(str(motivo).replace("_", " "))


def _payload_analise():
    from datetime import datetime
    data = request.get_json(silent=True) or request.form
    inicio, fim = janela_padrao()
    
    termos_brutos = data.get("termos")
    modo = (data.get("modo") or "").strip().lower()
    if not modo:
        modo = "livre" if str(termos_brutos or "").strip() else "fixo"
    if modo not in {"fixo", "livre"}:
        raise ValueError("Modo de busca inválido.")

    area = data.get("area")
    termos = []
    if modo == "livre":
        termos = _normalizar_termos(termos_brutos)
        area = None
    else:
        area = area or "TI"
        if area not in AREAS:
            raise ValueError(f"Área inválida: {area}. Opções: {list(AREAS.keys())}")
        
    uf = (data.get("uf") or "SP").upper()
    if uf not in UFS:
        raise ValueError(f"UF inválido: {uf}")
        
    try:
        limite = int(data.get("limite") or 10)
    except ValueError:
        raise ValueError("O limite deve ser um número inteiro válido entre 1 e 100.") from None
    if limite <= 0 or limite > 100:
        raise ValueError("O limite deve ser entre 1 e 100.")

    data_inicial = data.get("data_inicial") or inicio
    data_final = data.get("data_final") or fim

    try:
        dt_inicio = datetime.strptime(data_inicial, "%Y-%m-%d")
        dt_fim = datetime.strptime(data_final, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Formato de data inválido. Use YYYY-MM-DD.") from None
        
    if dt_inicio > dt_fim:
        raise ValueError("A data inicial não pode ser posterior à data final.")
        
    return {
        "modo": modo,
        "area": area,
        "termos": termos,
        "data_inicial": data_inicial,
        "data_final": data_final,
        "uf": uf,
        "limite": limite,
    }


def _params_json(payload):
    import json

    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _normalizar_termos(valor):
    if isinstance(valor, list):
        partes = valor
    else:
        partes = re.split(r"[,\r\n]+", str(valor or ""))
    termos = []
    vistos = set()
    for parte in partes:
        termo = " ".join(str(parte).split())
        if len(termo) < 2:
            continue
        chave = termo.casefold()
        if chave in vistos:
            continue
        vistos.add(chave)
        termos.append(termo)
    if not termos:
        raise ValueError("Informe ao menos um termo com 2 ou mais caracteres.")
    if len(termos) > 12:
        raise ValueError("A busca livre aceita no máximo 12 termos.")
    return termos


def _json_objeto(valor):
    try:
        resultado = json.loads(valor or "{}")
    except (TypeError, ValueError):
        return {}
    return resultado if isinstance(resultado, dict) else {}


def _json_lista(valor):
    try:
        resultado = json.loads(valor or "[]")
    except (TypeError, ValueError):
        return []
    return resultado if isinstance(resultado, list) else []


def _titulo_run(params):
    termos = params.get("termos") or []
    if termos:
        return ", ".join(termos)
    area = params.get("area")
    return AREA_LABELS.get(area, area or "Análise sem parâmetros")


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
