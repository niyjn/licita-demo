import re
import unicodedata
from pathlib import Path
from threading import Thread
from uuid import uuid4

from flask import Flask, jsonify, render_template, request

from analise import analisar
from pncp_query.config import AREAS, DB_PATH, UFS, janela_padrao
from pncp_query.services.storage import Storage

AREA_LABELS = {
    "TI": "TI",
    "ENGENHARIA": "Engenharia",
    "SAUDE": "Saúde",
}

DISPOSITION_LABELS = {
    "perdedor_final": "Perdedor final",
    "removido_invalido": "Removido por dígito inválido",
    "removido_orgao": "Removido por órgão comprador",
    "removido_vencedor": "Removido por vencedor",
    "vencedor": "Vencedor",
}

REASON_LABELS = {
    "cnpj_valido_da_ata": "CNPJ válido da ata",
    "coincidente_com_vencedor": "Coincide com vencedor",
    "digito_verificador_invalido": "Dígito verificador inválido",
    "orgao_comprador": "Órgão comprador",
    "resultado_pncp_estruturado": "Resultado estruturado do PNCP",
    "sem_perdedores_na_ata": "Sem perdedores na ata",
    "sem_vencedor_estruturado": "Sem vencedor estruturado",
}

STATUS_LABELS = {
    "descartado": "Descartado",
    "final": "Final",
    "vazio": "Vazio",
}


def create_app(config=None):
    app = Flask(
        __name__,
        static_folder="design-system",
        static_url_path="/design-system",
        template_folder="templates",
    )
    app.config.update(DB_PATH=DB_PATH, ANALYSIS_FUNC=analisar)
    if config:
        app.config.update(config)

    @app.get("/")
    def index():
        storage = Storage(app.config["DB_PATH"])
        run = storage.ultima_run()
        if run:
            return _render_run(app.config["DB_PATH"], run["id"])
        return _render_dashboard(app.config["DB_PATH"], run=None)

    @app.get("/analises/<run_id>")
    def ver_analise(run_id):
        return _render_run(app.config["DB_PATH"], run_id)

    @app.get("/analises/<run_id>/cnpjs")
    def listar_cnpjs(run_id):
        disposition = request.args.get("disposition")
        registros = Storage(app.config["DB_PATH"]).listar_cnpjs_auditoria(run_id, disposition=disposition)
        return jsonify({"run_id": run_id, "disposition": disposition, "cnpjs": registros})

    def _render_run(db_path, run_id):
        storage = Storage(db_path)
        run = storage.obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        return _render_dashboard(db_path, run)

    def _render_dashboard(db_path, run):
        import json
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
        contratos = _listar_contratos(db_path, uf, run_id=run["id"] if run else None, incluir_ocultos=incluir_ocultos)
        resumo = _resumo_run(db_path, run) if run else _resumo_contratos(contratos)
        documentos = _documentos_extraidos(db_path, run["id"]) if run else []
        return render_template(
            "index.html",
            areas=[{"value": area, "label": AREA_LABELS.get(area, area)} for area in AREAS],
            ufs=UFS,
            uf_atual=uf,
            periodo_padrao=janela_padrao(),
            contratos=[_contrato_view(contrato) for contrato in contratos],
            resumo=resumo,
            run=run,
            run_params=run_params,
            incluir_ocultos=incluir_ocultos,
            documentos=documentos,
        )

    @app.post("/analises")
    def criar_analise():
        try:
            payload = _payload_analise()
        except ValueError as e:
            return jsonify({"error": "invalid_input", "message": str(e)}), 400
        run_id = uuid4().hex
        storage = Storage(app.config["DB_PATH"])
        storage.criar_run(run_id, params_json=_params_json(payload))

        thread = Thread(
            target=_executar_analise_background,
            args=(run_id, payload, app.config["DB_PATH"], app.config["ANALYSIS_FUNC"]),
            daemon=True,
        )
        thread.start()
        return jsonify({"run_id": run_id}), 202

    @app.get("/analises/<run_id>/status")
    def status_analise(run_id):
        run = Storage(app.config["DB_PATH"]).obter_run(run_id)
        if not run:
            return jsonify({"error": "run_not_found"}), 404
        return jsonify(
            {
                "run_id": run["id"],
                "status": run["status"],
                "progress": run["progress"],
                "message": run["message"],
                "error": run["error"],
            }
        )

    @app.get("/analises/<run_id>/exportar")
    def exportar_analise(run_id):
        storage = Storage(app.config["DB_PATH"])
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
        
        from flask import make_response
        import json
        resposta = make_response(json.dumps(dados, indent=2, ensure_ascii=False))
        resposta.headers["Content-Disposition"] = f"attachment; filename=analise-pncp-{run_id}.json"
        resposta.headers["Content-Type"] = "application/json"
        return resposta

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app


def _listar_contratos(db_path, uf, run_id=None, incluir_ocultos=True):
    if not Path(db_path).exists():
        return []
    return Storage(db_path).listar_contratos(uf, run_id=run_id, incluir_ocultos=incluir_ocultos)


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


def _resumo_run(db_path, run):
    metricas = Storage(db_path).somar_metricas_run(run["id"])
    return {
        "contratos": len(Storage(db_path).listar_contratos(run_id=run["id"], incluir_ocultos=True)),
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
        
    return contrato


def _documentos_extraidos(db_path, run_id):
    documentos = {}
    for registro in Storage(db_path).listar_cnpjs_auditoria(run_id):
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
    
    area = data.get("area") or "TI"
    if area not in AREAS:
        raise ValueError(f"Área inválida: {area}. Opções: {list(AREAS.keys())}")
        
    uf = (data.get("uf") or "SP").upper()
    if uf not in UFS:
        raise ValueError(f"UF inválido: {uf}")
        
    try:
        limite = int(data.get("limite") or 10)
        if limite <= 0 or limite > 100:
            raise ValueError("O limite deve ser entre 1 e 100.")
    except ValueError:
        raise ValueError("O limite deve ser um número inteiro válido entre 1 e 100.")
        
    data_inicial = data.get("data_inicial") or inicio
    data_final = data.get("data_final") or fim
    
    try:
        dt_inicio = datetime.strptime(data_inicial, "%Y-%m-%d")
        dt_fim = datetime.strptime(data_final, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Formato de data inválido. Use YYYY-MM-DD.")
        
    if dt_inicio > dt_fim:
        raise ValueError("A data inicial não pode ser posterior à data final.")
        
    return {
        "area": area,
        "data_inicial": data_inicial,
        "data_final": data_final,
        "uf": uf,
        "limite": limite,
    }


def _params_json(payload):
    import json

    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _executar_analise_background(run_id, payload, db_path, analysis_func):
    storage = Storage(db_path)
    try:
        storage.atualizar_run(run_id, status="running", progress=0, message="Análise iniciada.")

        def progress(evento):
            atual = evento.get("atual")
            total = evento.get("total")
            progresso = 0
            if atual is not None and total:
                progresso = min(99, int((atual / total) * 100))
            storage.atualizar_run(run_id, status="running", progress=progresso, message=evento["mensagem"])

        analysis_func(
            payload["area"],
            payload["data_inicial"],
            payload["data_final"],
            payload["uf"],
            payload["limite"],
            db_path,
            run_id=run_id,
            progress=progress,
        )
    except Exception as exc:
        storage.atualizar_run(run_id, status="error", progress=100, message="Análise falhou.", error=str(exc))
    finally:
        run = storage.obter_run(run_id)
        if run and run["status"] != "error":
            storage.atualizar_run(run_id, status="done", progress=100, message="Análise concluída.", error="")


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
