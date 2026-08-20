"""Worker PostgreSQL: ``python -m pncp_query.worker [--once]``."""

import argparse
import os
import signal
import socket
import time
from uuid import uuid4

from pncp_query.application.analysis_command import AnalysisCommand
from pncp_query.application.analysis_executor import AnalysisExecutor
from pncp_query.config import require_database_url
from pncp_query.services.storage import Storage


def worker_id():
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def run_once(storage, executor, worker_name):
    run = storage.claim_next_run(worker_name)
    if run is None:
        return False
    try:
        command = AnalysisCommand.from_run(run)
    except ValueError as exc:
        storage.fail_claimed_run(run["id"], worker_name, str(exc))
        return True
    executor.execute(command, worker_name)
    return True


def run_forever(storage, executor, worker_name, poll_interval=2.0):
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        storage.limpar_runs_travadas()
        while not stopping:
            if not run_once(storage, executor, worker_name):
                storage.limpar_runs_travadas()
                time.sleep(poll_interval)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def main(argv=None, storage=None, executor=None):
    parser = argparse.ArgumentParser(description="Processa análises pendentes.")
    parser.add_argument("--once", action="store_true", help="Processa no máximo uma análise e encerra.")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    owns_storage = storage is None
    storage = storage if storage is not None else Storage(require_database_url())
    executor = executor if executor is not None else AnalysisExecutor(storage)
    try:
        if args.once:
            storage.limpar_runs_travadas()
            run_once(storage, executor, worker_id())
        else:
            run_forever(storage, executor, worker_id(), args.poll_interval)
    finally:
        if owns_storage:
            storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
