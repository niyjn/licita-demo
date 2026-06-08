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
        uf = request.args.get("uf") or "SP"
        if uf not in UFS:
            uf = "SP"
        contratos = _listar_contratos(app.config["DB_PATH"], uf)
        return render_template(
            "index.html",
            areas=AREAS,
            ufs=UFS,
            uf_atual=uf,
            periodo_padrao=janela_padrao(),
            contratos=contratos,
            resumo=_resumo_contratos(contratos),
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


def _listar_contratos(db_path, uf):
    if not Path(db_path).exists():
        return []
    return Storage(db_path).listar_contratos(uf)


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
        storage.atualizar_run(run_id, status="running", progress=0, message="Analise iniciada.")

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
            progress=progress,
        )
    except Exception as exc:
        storage.atualizar_run(run_id, status="error", progress=100, message="Analise falhou.", error=str(exc))
    finally:
        run = storage.obter_run(run_id)
        if run and run["status"] != "error":
            storage.atualizar_run(run_id, status="done", progress=100, message="Analise concluida.", error="")


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
