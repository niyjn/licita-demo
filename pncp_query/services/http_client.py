import random
import time

import requests

from pncp_query.config import (
    HTTP_BACKOFF_BASE_SECONDS,
    HTTP_BACKOFF_MAX_SECONDS,
    HTTP_JITTER_SECONDS,
    HTTP_MAX_RETRIES,
)

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class HttpClient:
    def __init__(self, session=None, metrics_callback=None):
        self.session = session or requests.Session()
        self.metrics_callback = metrics_callback

    def get(self, url, params=None, timeout=60, **kwargs):
        return self.request("GET", url, params=params, timeout=timeout, **kwargs)

    def post(self, url, timeout=60, **kwargs):
        return self.request("POST", url, timeout=timeout, **kwargs)

    def request(self, method, url, timeout=60, retries=HTTP_MAX_RETRIES, **kwargs):
        ultimo_erro = None
        for tentativa in range(1, retries + 1):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise requests.exceptions.HTTPError(f"{response.status_code} retryable", response=response)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as exc:
                ultimo_erro = exc
                if tentativa >= retries:
                    break
                espera = self._calcular_espera(tentativa, exc)
                self._emitir_retry(exc, espera)
                time.sleep(espera)
        raise ultimo_erro

    def _calcular_espera(self, tentativa, exc):
        retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
        if retry_after:
            try:
                return min(HTTP_BACKOFF_MAX_SECONDS, float(retry_after))
            except ValueError:
                pass
        base = HTTP_BACKOFF_BASE_SECONDS * (2 ** (tentativa - 1))
        jitter = random.uniform(0, HTTP_JITTER_SECONDS) if HTTP_JITTER_SECONDS > 0 else 0
        return min(HTTP_BACKOFF_MAX_SECONDS, base + jitter)

    def _emitir_retry(self, exc, espera):
        if not self.metrics_callback:
            return
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        self.metrics_callback(
            {
                "event": "http_retry",
                "status_code": status_code,
                "wait_seconds": espera,
                "error": str(exc),
            }
        )
