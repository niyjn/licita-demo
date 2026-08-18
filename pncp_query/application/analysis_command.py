"""Comando tipado criado a partir dos parâmetros persistidos de uma run."""

import json
from dataclasses import dataclass
from datetime import datetime

from pncp_query.config import AREAS, UFS


@dataclass(frozen=True)
class AnalysisCommand:
    run_id: str
    modo: str
    area: str | None
    termos: tuple[str, ...]
    data_inicial: str
    data_final: str
    uf: str
    limite: int

    @classmethod
    def from_run(cls, run):
        try:
            params = json.loads(run["params_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Parâmetros persistidos da análise são inválidos.") from exc
        if not isinstance(params, dict):
            raise ValueError("Parâmetros persistidos da análise são inválidos.")
        modo = params.get("modo")
        if modo not in {"fixo", "livre"}:
            raise ValueError("Modo de busca persistido é inválido.")
        area = params.get("area")
        termos = params.get("termos", [])
        if not isinstance(termos, list) or not all(
            isinstance(term, str) and len(term.strip()) >= 2 for term in termos
        ):
            raise ValueError("Termos persistidos são inválidos.")
        if modo == "livre" and (area is not None or not termos or len(termos) > 12):
            raise ValueError("Busca livre persistida requer termos e não aceita área.")
        if modo == "fixo" and (area not in AREAS or termos):
            raise ValueError("Busca por área persistida requer uma área e não aceita termos.")
        required = ("data_inicial", "data_final", "uf", "limite")
        if any(key not in params for key in required):
            raise ValueError("Parâmetros persistidos estão incompletos.")
        if not isinstance(params["limite"], int) or isinstance(params["limite"], bool):
            raise ValueError("Limite persistido é inválido.")
        if not 1 <= params["limite"] <= 100:
            raise ValueError("Limite persistido é inválido.")
        if not all(isinstance(params[key], str) and params[key] for key in ("data_inicial", "data_final", "uf")):
            raise ValueError("Parâmetros persistidos são inválidos.")
        if params["uf"] not in UFS:
            raise ValueError("UF persistida é inválida.")
        try:
            data_inicial = datetime.strptime(params["data_inicial"], "%Y-%m-%d")
            data_final = datetime.strptime(params["data_final"], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Datas persistidas são inválidas.") from exc
        if data_inicial > data_final:
            raise ValueError("Período persistido é inválido.")
        return cls(
            run_id=str(run["id"]),
            modo=modo,
            area=area,
            termos=tuple(termos),
            data_inicial=params["data_inicial"],
            data_final=params["data_final"],
            uf=params["uf"],
            limite=params["limite"],
        )
