# -*- coding: utf-8 -*-
"""
QPay API client with token handling, retries, and defensive JSON parsing.
"""

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_logger = logging.getLogger(__name__)

_RETRY_POLICY = Retry(
    total=0,
    connect=3,
    read=3,
    backoff_factor=0.5,
    raise_on_status=False,
)

RETRY_DELAYS = (1, 2, 4)


class QPayApiError(Exception):
    """General QPay API error."""

    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}

    def __str__(self):
        base = super().__str__()
        if self.status_code:
            return f"[HTTP {self.status_code}] {base}"
        return base


class QPayAuthError(QPayApiError):
    """Authentication-specific QPay error."""


class QPayClient:
    SANDBOX_BASE_URL = "https://merchant-sandbox.qpay.mn"
    PRODUCTION_BASE_URL = "https://merchant.qpay.mn"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        invoice_code: str,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        if not base_url:
            raise ValueError("QPay base_url must not be empty")
        if not username or not password:
            raise ValueError("QPay username and password are required")

        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.invoice_code = invoice_code
        self.timeout = timeout
        self.max_retries = max_retries

        self._access_token = None
        self._token_expires_at = None
        self._refresh_token = None

        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY_POLICY)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _basic_auth_header(self) -> str:
        raw = f"{self.username}:{self.password}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    def _bearer_header(self) -> str:
        return f"Bearer {self._access_token}"

    def _token_is_valid(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return False
        return datetime.now(tz=timezone.utc) < self._token_expires_at - timedelta(seconds=60)

    def authenticate(self) -> dict:
        url = f"{self.base_url}/v2/auth/token"
        headers = {
            "Authorization": self._basic_auth_header(),
            "Content-Type": "application/json",
        }
        _logger.info("QPay auth: requesting token from %s (user=%s)", url, self.username)

        for attempt, delay in enumerate(RETRY_DELAYS[: self.max_retries], start=1):
            try:
                resp = self._session.post(url, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                _logger.warning(
                    "QPay auth network error attempt %d/%d: %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                raise QPayApiError(
                    f"QPay auth network failure after {self.max_retries} attempts: {exc}"
                ) from exc

            if resp.status_code == 200:
                data = self._parse_json(resp)
                self._access_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))
                self._token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                    seconds=expires_in
                )
                self._refresh_token = data.get("refresh_token")
                _logger.info(
                    "QPay auth: token acquired, expires_in=%ds, expires_at=%s",
                    expires_in,
                    self._token_expires_at.isoformat(),
                )
                return data

            if resp.status_code in (401, 403):
                raise QPayAuthError(
                    f"QPay authentication rejected: {resp.text}",
                    status_code=resp.status_code,
                    response_data=self._safe_parse_json(resp.text),
                )

            if resp.status_code >= 500 and attempt < self.max_retries:
                _logger.warning(
                    "QPay auth HTTP %d attempt %d/%d, retrying in %ds",
                    resp.status_code,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            raise QPayApiError(
                f"QPay auth HTTP {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                response_data=self._safe_parse_json(resp.text),
            )

        raise QPayApiError(f"QPay auth failed after {self.max_retries} attempts")

    def _ensure_authenticated(self):
        if not self._token_is_valid():
            self.authenticate()

    def _make_request(
        self,
        method: str,
        path: str,
        json_body: dict = None,
        params: dict = None,
    ) -> dict:
        self._ensure_authenticated()
        url = f"{self.base_url}{path}"

        for attempt, delay in enumerate(RETRY_DELAYS[: self.max_retries], start=1):
            headers = {
                "Authorization": self._bearer_header(),
                "Content-Type": "application/json",
            }
            _logger.debug("QPay %s %s attempt %d/%d", method, path, attempt, self.max_retries)

            try:
                resp = self._session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                _logger.warning(
                    "QPay %s %s network error attempt %d: %s",
                    method,
                    path,
                    attempt,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                raise QPayApiError(
                    f"QPay {method} {path} network failure after {self.max_retries} attempts: {exc}"
                ) from exc

            if resp.status_code in (200, 201):
                data = self._parse_json(resp)
                _logger.debug(
                    "QPay %s %s response body: %s",
                    method,
                    path,
                    json.dumps(data)[:500],
                )
                return data

            if resp.status_code == 204:
                return {}

            if resp.status_code == 401:
                _logger.info(
                    "QPay: token rejected (401) on %s %s, re-authenticating",
                    method,
                    path,
                )
                self._access_token = None
                self.authenticate()
                continue

            if resp.status_code >= 500 and attempt < self.max_retries:
                _logger.warning(
                    "QPay %s %s HTTP %d attempt %d/%d, retrying in %ds",
                    method,
                    path,
                    resp.status_code,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            _logger.error(
                "QPay %s %s HTTP %d: %s",
                method,
                path,
                resp.status_code,
                resp.text[:1000],
            )
            raise QPayApiError(
                f"QPay {method} {path} failed: {resp.text[:400]}",
                status_code=resp.status_code,
                response_data=self._safe_parse_json(resp.text),
            )

        raise QPayApiError(f"QPay {method} {path} failed after {self.max_retries} attempts")

    def create_invoice(self, payload: dict) -> dict:
        _logger.info(
            "QPay create_invoice: sender_invoice_no=%s amount=%s",
            payload.get("sender_invoice_no"),
            payload.get("amount"),
        )
        return self._make_request("POST", "/v2/invoice", json_body=payload)

    def check_payment(
        self,
        object_id: str,
        object_type: str = "INVOICE",
        page_number: int = 1,
        page_limit: int = 100,
    ) -> dict:
        _logger.info("QPay check_payment: object_type=%s object_id=%s", object_type, object_id)
        payload = {
            "object_type": object_type,
            "object_id": object_id,
            "offset": {
                "page_number": page_number,
                "page_limit": page_limit,
            },
        }
        return self._make_request("POST", "/v2/payment/check", json_body=payload)

    def get_payment(self, payment_id: str) -> dict:
        _logger.info("QPay get_payment: payment_id=%s", payment_id)
        return self._make_request("GET", f"/v2/payment/{payment_id}")

    def cancel_invoice(self, invoice_id: str) -> dict:
        _logger.info("QPay cancel_invoice: invoice_id=%s", invoice_id)
        return self._make_request("DELETE", f"/v2/invoice/{invoice_id}")

    @staticmethod
    def _parse_json(response: requests.Response) -> dict:
        try:
            return response.json()
        except ValueError as exc:
            raise QPayApiError(
                f"QPay returned non-JSON response (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}
