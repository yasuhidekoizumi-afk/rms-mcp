"""Logiless OAuth2 API クライアント.

LogilessはOAuth2認証を使用。CLIENT_ID + CLIENT_SECRETでトークンを取得し、
アクセストークンでAPIを叩く。トークンは自動リフレッシュ対応。

環境変数:
  LOGILESS_CLIENT_ID: OAuth2クライアントID
  LOGILESS_CLIENT_SECRET: OAuth2クライアントシークレット
  LOGILESS_MERCHANT_ID: マーチャントID（数値）
  LOGILESS_TOKENS_PATH: tokens.jsonのパス（省略時は環境変数からトークン直接読み込み）
"""
import json
import os
import time
import pathlib
from typing import Any

import httpx

API_BASE = "https://app2.logiless.com/api/v1"
TOKEN_URL = "https://app2.logiless.com/oauth2/token"

# デフォルトのtokens.jsonパス（MCPサーバーと共有）
DEFAULT_TOKENS_PATH = str(pathlib.Path.home() / "mcp-servers" / "logiless" / "tokens.json")


class LogilessClient:
    """Logiless API OAuth2 クライアント（自動トークンリフレッシュ付き）."""

    def __init__(self, client_id: str | None = None,
                 client_secret: str | None = None,
                 merchant_id: str | None = None,
                 tokens_path: str | None = None):
        self.client_id = client_id or os.environ.get("LOGILESS_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("LOGILESS_CLIENT_SECRET", "")
        self.merchant_id = merchant_id or os.environ.get("LOGILESS_MERCHANT_ID", "")
        self.tokens_path = tokens_path or os.environ.get("LOGILESS_TOKENS_PATH", DEFAULT_TOKENS_PATH)
        self._tokens: dict | None = None
        self._client = httpx.Client(timeout=30.0)

    def _load_tokens(self) -> dict:
        """tokens.jsonからトークンを読み込み."""
        if self._tokens:
            return self._tokens

        # 環境変数からアクセストークン + リフレッシュトークンが指定されている場合
        env_access = os.environ.get("LOGILESS_ACCESS_TOKEN", "")
        env_refresh = os.environ.get("LOGILESS_REFRESH_TOKEN", "")
        if env_access:
            # 有効期限が不明なので1時間後に設定（リフレッシュを試みる）
            self._tokens = {
                "access_token": env_access,
                "refresh_token": env_refresh,
                "expires_at": (time.time() + 3600) * 1000,
                "merchant_id": self.merchant_id,
            }
            return self._tokens

        # tokens.jsonから読み込み
        p = pathlib.Path(self.tokens_path)
        if p.exists():
            self._tokens = json.loads(p.read_text())
            if self.merchant_id and "merchant_id" not in self._tokens:
                self._tokens["merchant_id"] = self.merchant_id
            if "merchant_id" in self._tokens:
                self.merchant_id = self._tokens["merchant_id"]
            return self._tokens

        raise RuntimeError(
            f"Logiless tokens not found. Set LOGILESS_ACCESS_TOKEN env var "
            f"or ensure tokens.json exists at {self.tokens_path}"
        )

    def _refresh_token(self) -> str:
        """リフレッシュトークンでアクセストークンを更新."""
        tokens = self._load_tokens()
        refresh_token = tokens.get("refresh_token", "")
        if not refresh_token:
            raise RuntimeError("No refresh_token available. Re-authentication required.")

        r = httpx.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=15.0)

        if r.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {r.status_code} {r.text[:200]}")

        data = r.json()
        new_tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_at": int(time.time() * 1000) + data.get("expires_in", 3600) * 1000,
            "merchant_id": self.merchant_id,
        }

        # tokens.jsonに保存
        p = pathlib.Path(self.tokens_path)
        if p.parent.exists():
            p.write_text(json.dumps(new_tokens, indent=2))

        self._tokens = new_tokens
        return new_tokens["access_token"]

    def _get_valid_token(self) -> str:
        """有効なアクセストークンを取得（期限切れなら自動リフレッシュ）."""
        tokens = self._load_tokens()
        expires_at = tokens.get("expires_at", 0)

        # 期限切れチェック（1分の余裕）
        if time.time() * 1000 < expires_at - 60_000:
            return tokens["access_token"]

        return self._refresh_token()

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        """APIリクエスト（自動認証付き）."""
        token = self._get_valid_token()
        mid = self.merchant_id or self._tokens.get("merchant_id", "")

        url = f"{API_BASE}/merchant/{mid}{path}"
        kw.setdefault("headers", {})
        kw["headers"]["Authorization"] = f"Bearer {token}"
        kw["headers"]["Accept"] = "application/json"

        return self._client.request(method, url, **kw)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def close(self):
        self._client.close()
