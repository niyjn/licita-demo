from pathlib import Path

from flask import Flask, jsonify, render_template, request

from pncp_query.config import AREAS, DB_PATH, UFS, janela_padrao
from pncp_query.services.storage import Storage


def create_app(config=None):
    app = Flask(
        __name__,
        static_folder="design-system",
        static_url_path="/design-system",
        template_folder="templates",
    )
    app.config.update(DB_PATH=DB_PATH)
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
        return (
            jsonify(
                {
                    "error": "analysis_jobs_not_implemented",
                    "message": "A fundacao Flask esta ativa; o job de analise entra no proximo commit.",
                }
            ),
            501,
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


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
