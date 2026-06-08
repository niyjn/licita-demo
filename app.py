import re
import unicodedata
from pathlib import Path
from threading import Thread
from uuid import uuid4

from flask import Flask, jsonify, render_template, request

from analise import analisar
from pncp_query.config import AREAS, DB_PATH, UFS, janela_padrao
from pncp_query.services.storage import Storage


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
        uf = request.args.get("uf") or "SP"
        if uf not in UFS:
            uf = "SP"
        incluir_ocultos = request.args.get("mostrar") == "ocultos"
        contratos = _listar_contratos(db_path, uf, run_id=run["id"] if run else None, incluir_ocultos=incluir_ocultos)
        resumo = _resumo_run(db_path, run) if run else _resumo_contratos(contratos)
        documentos = _documentos_extraidos(db_path, run["id"]) if run else []
        return render_template(
            "index.html",
            areas=AREAS,
            ufs=UFS,
            uf_atual=uf,
            periodo_padrao=janela_padrao(),
            contratos=[_contrato_view(contrato) for contrato in contratos],
            resumo=resumo,
            run=run,
            incluir_ocultos=incluir_ocultos,
            documentos=documentos,
        )

    @app.post("/analises")
    def criar_analise():
        payload = _payload_analise()
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
    return contrato


def _documentos_extraidos(db_path, run_id):
    documentos = {}
    for registro in Storage(db_path).listar_cnpjs_auditoria(run_id):
        if registro.get("source") != "ata":
            continue
        arquivos = [item.strip() for item in (registro.get("origin_file") or "Ata sem nome").split(",") if item.strip()]
        for arquivo in arquivos:
            documento = documentos.setdefault(arquivo, {"arquivo": arquivo, "registros": []})
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
        "municipio": "município",
        "orgao": "órgão",
        "publica": "pública",
        "publico": "público",
        "servicos": "serviços",
        "tecnico": "técnico",
    }
    for origem, destino in termos.items():
        texto = re.sub(rf"\b{origem}\b", destino, texto, flags=re.IGNORECASE)
    return texto


def _payload_analise():
    data = request.get_json(silent=True) or request.form
    inicio, fim = janela_padrao()
    area = data.get("area") or "TI"
    uf = data.get("uf") or "SP"
    return {
        "area": area if area in AREAS else "TI",
        "data_inicial": data.get("data_inicial") or inicio,
        "data_final": data.get("data_final") or fim,
        "uf": uf if uf in UFS else "SP",
        "limite": int(data.get("limite") or 10),
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
