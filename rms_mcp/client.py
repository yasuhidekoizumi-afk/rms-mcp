"""RMS API HTTP client with ESA Base64 auth."""
import base64
import time

import httpx

REST_BASE = "https://api.rms.rakuten.co.jp/es/2.0"

RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_MAX_ATTEMPTS = 5
RETRY_BACKOFF_BASE = 1.0  # seconds; doubles each attempt (1s, 2s, 4s, 8s)


class RMSClient:
    """HTTP client for Rakuten RMS REST API.

    Auth is ESA Base64(serviceSecret:licenseKey) - NOT HMAC.

    Retries 5xx and connection errors up to 3 times with exponential backoff.
    """

    def __init__(self, service_secret: str, license_key: str,
                 *, retry_attempts: int = RETRY_MAX_ATTEMPTS,
                 retry_backoff_base: float = RETRY_BACKOFF_BASE,
                 sleep=time.sleep) -> None:
        self.service_secret = service_secret
        self.license_key = license_key
        self._retry_attempts = retry_attempts
        self._retry_backoff_base = retry_backoff_base
        self._sleep = sleep
        creds = f"{service_secret}:{license_key}"
        auth_bytes = base64.b64encode(creds.encode("ascii"))
        self._auth = f"ESA {auth_bytes.decode()}"
        self._client = httpx.Client(
            base_url=REST_BASE,
            timeout=60.0,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        kw.setdefault("headers", {})
        kw["headers"]["Authorization"] = self._auth

        last_exc: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                r = self._client.request(method, path, **kw)
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError,
                    httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < self._retry_attempts:
                    self._sleep(self._retry_backoff_base * (2 ** (attempt - 1)))
                    continue
                raise RuntimeError(
                    f"RMS API connection failed after {attempt} attempts "
                    f"for {method} {path}: {exc}"
                ) from exc

            if r.is_success:
                return r

            if r.status_code in RETRY_STATUS:
                if attempt < self._retry_attempts:
                    self._sleep(self._retry_backoff_base * (2 ** (attempt - 1)))
                    continue
                body = r.text[:2000]
                raise RuntimeError(
                    f"RMS API {r.status_code} for {method} {path} "
                    f"(after {self._retry_attempts} attempts)\n"
                    f"Response: {body}"
                )

            body = r.text[:2000]
            raise RuntimeError(
                f"RMS API {r.status_code} for {method} {path}\n"
                f"Response: {body}"
            )

        # Defensive: loop ends only via return/raise above.
        raise RuntimeError(f"RMS API failed for {method} {path}: {last_exc}")

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)

    def close(self) -> None:
        self._client.close()
