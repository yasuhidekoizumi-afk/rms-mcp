"""RMS API HTTP client with ESA Base64 auth."""
import base64

import httpx

REST_BASE = "https://api.rms.rakuten.co.jp/es/2.0"


class RMSClient:
    """HTTP client for Rakuten RMS REST API.

    Auth is ESA Base64(serviceSecret:licenseKey) - NOT HMAC.
    """

    def __init__(self, service_secret: str, license_key: str) -> None:
        self.service_secret = service_secret
        self.license_key = license_key
        creds = f"{service_secret}:{license_key}"
        auth_bytes = base64.b64encode(creds.encode("ascii"))
        self._auth = f"ESA {auth_bytes.decode()}"
        self._client = httpx.Client(
            base_url=REST_BASE,
            timeout=30.0,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        kw.setdefault("headers", {})
        kw["headers"]["Authorization"] = self._auth
        r = self._client.request(method, path, **kw)
        if not r.is_success:
            body = r.text[:2000]
            raise RuntimeError(
                f"RMS API {r.status_code} for {method} {path}\n"
                f"Response: {body}"
            )
        return r

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def close(self) -> None:
        self._client.close()
