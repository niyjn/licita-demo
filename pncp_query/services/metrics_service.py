import json
from datetime import datetime
from pathlib import Path

from pncp_query.config import OUTPUT_DIR


class MetricsService:
    def __init__(self, caminho: Path | None = None):
        self.caminho = caminho or (OUTPUT_DIR / "relatorio_execucao.json")
        self.dados = self._carregar()

    def increment(self, chave, valor=1):
        self.dados[chave] = int(self.dados.get(chave, 0)) + valor
        self.salvar()

    def set(self, chave, valor):
        self.dados[chave] = valor
        self.salvar()

    def reset(self):
        self.dados = {"iniciado_em": datetime.now().isoformat(timespec="seconds")}
        self.salvar()

    def add_error(self, etapa, mensagem):
        erros = self.dados.setdefault("erros", [])
        erros.append({"etapa": etapa, "mensagem": str(mensagem), "ts": datetime.now().isoformat(timespec="seconds")})
        self.salvar()

    def salvar(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.dados["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
        with self.caminho.open("w", encoding="utf-8") as arquivo:
            json.dump(self.dados, arquivo, ensure_ascii=False, indent=2)

    def _carregar(self):
        if not self.caminho.exists():
            return {"iniciado_em": datetime.now().isoformat(timespec="seconds")}
        try:
            with self.caminho.open("r", encoding="utf-8") as arquivo:
                return json.load(arquivo)
        except json.JSONDecodeError:
            return {"iniciado_em": datetime.now().isoformat(timespec="seconds")}
