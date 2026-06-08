import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pncp_query.services.common import nome_seguro


@dataclass(frozen=True)
class FileInspection:
    sha256: str
    file_size_bytes: int
    content_type: str
    magic_type: str


class FileInspectionService:
    def inspect(self, path: Path):
        path = Path(path)
        with path.open("rb") as arquivo:
            prefix = arquivo.read(16)
        magic_type, content_type = self._detect(prefix)
        return FileInspection(
            sha256=self.sha256(path),
            file_size_bytes=path.stat().st_size,
            content_type=content_type,
            magic_type=magic_type,
        )

    def sha256(self, path: Path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                digest.update(bloco)
        return digest.hexdigest()

    def extract_zip_pdfs(self, zip_path: Path, output_dir: Path):
        zip_path = Path(zip_path)
        output_dir = Path(output_dir) / "extracted" / nome_seguro(zip_path.stem)
        output_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if not info.filename.lower().endswith(".pdf"):
                    continue
                destino = output_dir / nome_seguro(Path(info.filename).name)
                if destino.suffix.lower() != ".pdf":
                    destino = destino.with_suffix(".pdf")
                with archive.open(info) as origem, destino.open("wb") as arquivo_destino:
                    shutil.copyfileobj(origem, arquivo_destino)
                extracted.append(destino)
        return extracted

    def _detect(self, prefix: bytes):
        normalizado = prefix.lstrip().lower()
        if prefix.startswith(b"%PDF"):
            return "PDF", "application/pdf"
        if prefix.startswith(b"PK"):
            return "ZIP", "application/zip"
        if normalizado.startswith(b"<html") or normalizado.startswith(b"<!doctype"):
            return "HTML", "text/html"
        if normalizado.startswith(b"{") or normalizado.startswith(b"["):
            return "JSON", "application/json"
        return "UNKNOWN", "application/octet-stream"
