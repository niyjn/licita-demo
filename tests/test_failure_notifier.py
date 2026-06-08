import requests

from pncp_query.services.failure_notifier import FailureNotifier


class FakeHttp:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc


def test_notifier_desabilitado_nao_envia():
    http = FakeHttp()

    assert not FailureNotifier(webhook_url="", http=http).notify("event", message="erro")
    assert http.calls == []


def test_notifier_envia_payload_json():
    http = FakeHttp()
    notifier = FailureNotifier(webhook_url="https://example.test/webhook", timeout=3, http=http)

    assert notifier.notify("pipeline_stage_failed", run_id="run-1", stage="download", message="erro")

    url, kwargs = http.calls[0]
    assert url == "https://example.test/webhook"
    assert kwargs["timeout"] == 3
    assert kwargs["retries"] == 1
    assert kwargs["json"]["event"] == "pipeline_stage_failed"
    assert kwargs["json"]["run_id"] == "run-1"
    assert kwargs["json"]["stage"] == "download"


def test_notifier_nao_quebra_quando_webhook_falha():
    http = FakeHttp(exc=requests.exceptions.Timeout("timeout"))
    notifier = FailureNotifier(webhook_url="https://example.test/webhook", http=http)

    assert not notifier.notify("pipeline_stage_failed", message="erro")
