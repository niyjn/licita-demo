from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Licitacao:
    termo_busca: str
    status_busca: str
    tipo_documento_busca: str
    orgao_cnpj: str
    ano: str
    numero_sequencial: str
    numero_controle_pncp: str = ""
    orgao_nome: str = ""
    uf: str = ""
    municipio_nome: str = ""
    modalidade_licitacao_nome: str = ""
    situacao_nome: str = ""
    valor_global: str = ""
    data_publicacao_pncp: str = ""
    data_atualizacao_pncp: str = ""
    title: str = ""
    description: str = ""
    item_url: str = ""


@dataclass(frozen=True)
class ArquivoPNCP:
    titulo: str
    url: str
    destino: Path
    prioridade: str = "priority"
    sequencial: str = ""


@dataclass
class LoteArquivosPNCP:
    prioritarios: list[ArquivoPNCP] = field(default_factory=list)
    fallback: list[ArquivoPNCP] = field(default_factory=list)
    ignorados: int = 0


@dataclass
class ResultadoPDF:
    arquivo: str
    cnpjs_total: list[str] = field(default_factory=list)
    evidencias: list["EvidenciaCNPJ"] = field(default_factory=list)
    origem_texto: str = "pdfplumber"
    erro: str = ""
    ocr_attempted: bool = False
    ocr_success: bool = False
    ocr_error: str = ""
    page_count: int = 0
    parse_duration_ms: int = 0


@dataclass(frozen=True)
class EvidenciaCNPJ:
    cnpj: str
    pagina: int
    trecho: str
    categoria: str
    sinal: str
