import shutil
import subprocess

from pncp_query.services.database_service import DatabaseService


def check_runtime():
    checks = {
        "database": DatabaseService().check(),
        "tesseract": _command_ok(["tesseract", "--version"]),
        "poppler": _command_ok(["pdftoppm", "-v"]),
        "tesseract_lang_por": _tesseract_has_por(),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(f"Runtime incompleto: {', '.join(failed)}")
    return checks


def _command_ok(command):
    if not shutil.which(command[0]):
        return False
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _tesseract_has_por():
    if not shutil.which("tesseract"):
        return False
    try:
        result = subprocess.run(["tesseract", "--list-langs"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return "por" in {line.strip() for line in result.stdout.splitlines()}


if __name__ == "__main__":
    resultado = check_runtime()
    for nome, ok in resultado.items():
        print(f"{nome}=ok" if ok else f"{nome}=fail")
