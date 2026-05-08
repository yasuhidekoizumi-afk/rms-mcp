"""RMS API HTTP client with HMAC-SHA256 ESA auth."""
import hashlib
import hmac
import time

import httpx

REST_BASE = "https://api.rms.rakuten.co.jp/es/2.0"


class RMSClient:
    def __init__(self, service_secret: str, license_key: str):
        self.service_secret = service_secret
        self.license_key = license_key
        self._client = httpx.Client(
            base_url=REST_BASE, timeout=30.0,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    def _sign(self) -> str:
        ts = str(int(time.time() * 1000))
        msg = f"{REST_BASE}\n{ts}"
        sig = hmac.new(self.license_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return f"ESA {self.service_secret}:{sig}:{ts}"

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        kw.setdefault("headers", {})
        kw["headers"]["Authorization"] = self._sign()
        r = self._client.request(method, path, **kw)
        r.raise_for_status()
        return r

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def close(self):
        self._client.close()
