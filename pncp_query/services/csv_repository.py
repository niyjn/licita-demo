import csv
import json
from dataclasses import asdict
from pathlib import Path


def salvar_json(caminho: Path, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)


def ler_json(caminho: Path):
    with caminho.open("r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def preparar_csv(caminho: Path, colunas):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas, delimiter=";")
        escritor.writeheader()


def append_csv_dict(caminho: Path, linha, colunas):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    novo = not caminho.exists() or caminho.stat().st_size == 0
    with caminho.open("a", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas, delimiter=";")
        if novo:
            escritor.writeheader()
        escritor.writerow({coluna: linha.get(coluna, "") for coluna in colunas})


def append_jsonl(caminho: Path, linha):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")


def salvar_csv_dataclasses(caminho: Path, registros, colunas):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linhas_unicas = []
    vistos = set()
    for registro in registros:
        linha = asdict(registro)
        chave = tuple(linha.get(coluna, "") for coluna in colunas)
        if chave not in vistos:
            vistos.add(chave)
            linhas_unicas.append(linha)

    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas, delimiter=";")
        escritor.writeheader()
        escritor.writerows({coluna: linha.get(coluna, "") for coluna in colunas} for linha in linhas_unicas)


def ler_csv(caminho: Path):
    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        yield from csv.DictReader(arquivo, delimiter=";")
