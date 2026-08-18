"""Executa uma análise já reivindicada, sem depender de Flask."""

from analise import analisar

from pncp_query.application.analysis_command import AnalysisCommand


class AnalysisExecutor:
    def __init__(self, storage, analysis_func=analisar):
        self.storage = storage
        self.analysis_func = analysis_func

    def execute(self, command: AnalysisCommand, worker_id: str):
        def progress(event):
            current, total = event.get("atual"), event.get("total")
            percent = 0
            if current is not None and total:
                percent = min(99, int(current / total * 100))
            self.storage.heartbeat_run(command.run_id, worker_id, percent, event.get("mensagem", ""))

        try:
            kwargs = {"run_id": command.run_id, "progress": progress}
            if command.modo == "livre":
                kwargs["termos"] = list(command.termos)
            self.analysis_func(
                command.area, command.data_inicial, command.data_final, command.uf,
                command.limite, self.storage.db_path, **kwargs,
            )
        except Exception as exc:
            self.storage.fail_claimed_run(command.run_id, worker_id, str(exc))
            return False
        self.storage.complete_claimed_run(command.run_id, worker_id)
        return True
