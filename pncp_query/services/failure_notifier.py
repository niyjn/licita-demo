from datetime import datetime

import requests

from pncp_query.config import FAILURE_WEBHOOK_TIMEOUT_SECONDS, FAILURE_WEBHOOK_URL
from pncp_query.services.http_client import HttpClient


class FailureNotifier:
    def __init__(self, webhook_url=FAILURE_WEBHOOK_URL, timeout=FAILURE_WEBHOOK_TIMEOUT_SECONDS, http=None):
        self.webhook_url = (webhook_url or "").strip()
        self.timeout = timeout
        self.http = http or HttpClient()

    def enabled(self):
        return bool(self.webhook_url)

    def notify(self, event, run_id=None, stage=None, level="CRITICAL", message="", **payload):
        if not self.enabled():
            return False
        body = {
            "event": event,
            "run_id": run_id,
            "stage": stage,
            "level": level,
            "message": str(message),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        try:
            self.http.post(self.webhook_url, json=body, timeout=self.timeout, retries=1)
        except requests.exceptions.RequestException as exc:
            print(f"[AVISO] Falha ao enviar webhook de falha: {exc}")
            return False
        return True
