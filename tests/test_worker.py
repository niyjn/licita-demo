import json
import os
import subprocess
import sys
import threading

import pytest

from pncp_query.application.analysis_command import AnalysisCommand
from pncp_query.application.analysis_executor import AnalysisExecutor
from pncp_query.services.storage import Storage
from pncp_query.worker import main, run_once


def params(**overrides):
    values = {
        "modo": "fixo", "area": "TI", "termos": [], "data_inicial": "2026-01-01",
        "data_final": "2026-01-31", "uf": "SP", "limite": 1,
    }
    values.update(overrides)
    return json.dumps(values)


def test_claim_persiste_e_apenas_um_worker_reivindica_concorrente(storage):
    storage.criar_run("old", params())
    claimed = []

    def claim(name):
        other = Storage(storage.database_url)
        run = other.claim_next_run(name)
        other.close()
        if run:
            claimed.append(run)

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [run["id"] for run in claimed] == ["old"]
    assert claimed[0]["status"] == "running" and claimed[0]["attempt_count"] == 1


def test_tres_workers_reivindicam_tres_runs_diferentes(storage):
    for run_id in ("first", "second", "third"):
        storage.criar_run(run_id, params())
    claimed = []

    def claim(worker):
        instance = Storage(storage.database_url)
        try:
            run = instance.claim_next_run(worker)
            if run:
                claimed.append(run)
        finally:
            instance.close()

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {run["id"] for run in claimed} == {"first", "second", "third"}
    assert {run["worker_id"] for run in claimed} == {"worker-0", "worker-1", "worker-2"}


def test_executor_sucesso_progresso_e_erro(storage):
    storage.criar_run("success", params())
    run = storage.claim_next_run("worker")

    def successful(*args, **kwargs):
        kwargs["progress"]({"mensagem": "metade", "atual": 1, "total": 2})

    assert AnalysisExecutor(storage, successful).execute(AnalysisCommand.from_run(run), "worker")
    completed = storage.obter_run("success")
    assert (completed["status"], completed["progress"], completed["message"]) == ("done", 100.0, "Análise concluída.")

    storage.criar_run("error", params())
    run = storage.claim_next_run("worker")
    def failing(*_args, **_kwargs):
        raise RuntimeError("boom")

    assert not AnalysisExecutor(storage, failing).execute(AnalysisCommand.from_run(run), "worker")
    assert storage.obter_run("error")["error"] == "boom"


def test_worker_once_processa_run_e_payload_invalido_vira_erro(storage):
    storage.criar_run("valid", params())
    called = []
    executor = AnalysisExecutor(storage, lambda *args, **kwargs: called.append(True))
    assert run_once(storage, executor, "worker") is True
    assert called == [True]
    assert storage.obter_run("valid")["status"] == "done"
    assert run_once(storage, executor, "worker") is False

    storage.criar_run("invalid", "not-json")
    assert run_once(storage, executor, "worker") is True
    invalid = storage.obter_run("invalid")
    assert invalid["status"] == "error"
    assert "inválidos" in invalid["error"]


def test_worker_main_once_processa_uma_run(storage):
    storage.criar_run("queued", params())
    called = []
    executor = AnalysisExecutor(storage, lambda *args, **kwargs: called.append(kwargs["run_id"]))

    assert main(["--once"], storage=storage, executor=executor) == 0
    assert called == ["queued"]
    assert storage.obter_run("queued")["status"] == "done"


@pytest.mark.parametrize(
    "overrides",
    [
        {"area": "DESCONHECIDA"},
        {"uf": "XX"},
        {"limite": 101},
        {"data_inicial": "2026-02-01", "data_final": "2026-01-01"},
    ],
)
def test_analysis_command_rejeita_parametros_fora_das_regras(overrides):
    run = {"id": "invalid", "params_json": params(**overrides)}

    with pytest.raises(ValueError):
        AnalysisCommand.from_run(run)


def test_python_module_worker_once_reivindica_fila_persistida(storage):
    storage.criar_run("invalid", "not-json")
    env = os.environ.copy()
    env["DATABASE_URL"] = storage.database_url

    result = subprocess.run(
        [sys.executable, "-m", "pncp_query.worker", "--once"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    run = storage.obter_run("invalid")
    assert run["status"] == "error"
    assert run["attempt_count"] == 1


def test_stale_running_vira_erro_sem_voltar_para_fila(storage):
    storage.criar_run("stale", params())
    storage.claim_next_run("worker")
    with storage.connect() as cursor:
        cursor.execute("UPDATE runs SET heartbeat_at = now() - interval '2 hours' WHERE id = %s", ("stale",))
    storage.limpar_runs_travadas(timeout_segundos=1)
    assert storage.obter_run("stale")["status"] == "error"
