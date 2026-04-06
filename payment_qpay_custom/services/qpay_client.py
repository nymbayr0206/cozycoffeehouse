# -*- coding: utf-8 -*-
"""
QPay API Client — pure Python, no Odoo dependencies.

Responsibilities:
  - HTTP authentication (Basic auth → Bearer token)
  - In-process token caching with expiry awareness
  - Automatic token refresh on 401
  - Retry with exponential back-off for transient failures (5xx / network)
  - Structured logging for every API call
  - Defensive JSON parsing

Usage from Odoo models:
    from ..services.qpay_client import QPayClient, QPayApiError, QPayAuthError
    client = QPayClient(base_url=..., username=..., password=..., invoice_code=...)
    token_data = client.authenticate()          # optional — called lazily
    invoice   = client.create_invoice(payload)
    check_res = client.check_payment(invoice_id)
    payment   = client.get_payment(payment_id)
    client.cancel_invoice(invoice_id)
    ebarimt   = client.create_ebarimt(payment_id, receiver_type="CITIZEN")
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

# ---------------------------------------------------------------------------
# Retry policy for the underlying requests.Session
# Only retry on network-level errors; HTTP error retries are handled manually
# so we can inspect status codes and re-auth on 401.
# ---------------------------------------------------------------------------
_RETRY_POLICY = Retry(
    total=0,            # HTTP-level retries handled in _make_request()
    connect=3,
    read=3,
    backoff_factor=0.5,
    raise_on_status=False,
)

RETRY_DELAYS = (1, 2, 4)   # seconds between attempts (up to 3 attempts total)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class QPayApiError(Exception):
    """General QPay API error (HTTP 4xx / 5xx with body)."""

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
    """Raised when credentials are wrong or token cannot be obtained."""


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class QPayClient:
    """Thread-safe* QPay API client with automatic token lifecycle management.

    *Token state is per-instance. If you share one instance across threads,
    wrap token mutation in a lock. For typical Odoo usage (one instance per
    request/cron cycle) this is fine without locks.
    """

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

        # Token state
        self._access_token: str = None
        self._token_expires_at: datetime = None
        self._refresh_token: str = None

        # Build session with low-level retry on network errors
        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY_POLICY)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _basic_auth_header(self) -> str:
        raw = f"{self.username}:{self.password}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    def _bearer_header(self) -> str:
        return f"Bearer {self._access_token}"

    def _token_is_valid(self) -> bool:
        """Return True if current token will be valid for at least 60 s."""
        if not self._access_token or not self._token_expires_at:
            return False
        return datetime.now(tz=timezone.utc) < self._token_expires_at - timedelta(seconds=60)

    # ------------------------------------------------------------------
    # Public: authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> dict:
        """Obtain a new Bearer token via HTTP Basic auth.

        Stores token internally. Returns the full token response dict.
        Raises QPayAuthError on credential failure.
        Raises QPayApiError on other errors after exhausting retries.
        """
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
                _logger.warning("QPay auth network error attempt %d/%d: %s", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                raise QPayApiError(f"QPay auth network failure after {self.max_retries} attempts: {exc}") from exc

            _logger.debug("QPay auth response: HTTP %s", resp.status_code)

            if resp.status_code == 200:
                data = self._parse_json(resp)
                self._access_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))
                self._token_expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)
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

            # 5xx — retryable
            if resp.status_code >= 500 and attempt < self.max_retries:
                _logger.warning(
                    "QPay auth HTTP %d attempt %d/%d, retrying in %ds",
                    resp.status_code, attempt, self.max_retries, delay,
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

    # ------------------------------------------------------------------
    # Internal: generic HTTP
    # ------------------------------------------------------------------

    def _make_request(
        self,
        method: str,
        path: str,
        json_body: dict = None,
        params: dict = None,
    ) -> dict:
        """Execute an authenticated API request with retry and auto-reauth.

        Returns parsed JSON dict (empty dict for 204 No Content).
        Raises QPayApiError on unrecoverable error.
        """
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
                _logger.warning("QPay %s %s network error attempt %d: %s", method, path, attempt, exc)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                raise QPayApiError(
                    f"QPay {method} {path} network failure after {self.max_retries} attempts: {exc}"
                ) from exc

            _logger.debug("QPay %s %s -> HTTP %d", method, path, resp.status_code)

            # Success
            if resp.status_code in (200, 201):
                data = self._parse_json(resp)
                _logger.debug("QPay %s %s response body: %s", method, path, json.dumps(data)[:500])
                return data

            if resp.status_code == 204:
                return {}

            # Token expired mid-session — re-auth once and retry immediately
            if resp.status_code == 401:
                _logger.info("QPay: token rejected (401) on %s %s, re-authenticating", method, path)
                self._access_token = None
                self.authenticate()
                # Don't count as a retry attempt; loop will retry with fresh token
                continue

            # Server errors — retryable
            if resp.status_code >= 500 and attempt < self.max_retries:
                _logger.warning(
                    "QPay %s %s HTTP %d attempt %d/%d, retrying in %ds",
                    method, path, resp.status_code, attempt, self.max_retries, delay,
                )
                time.sleep(delay)
                continue

            # Non-retryable client error
            _logger.error(
                "QPay %s %s HTTP %d: %s",
                method, path, resp.status_code, resp.text[:1000],
            )
            raise QPayApiError(
                f"QPay {method} {path} failed: {resp.text[:400]}",
                status_code=resp.status_code,
                response_data=self._safe_parse_json(resp.text),
            )

        raise QPayApiError(f"QPay {method} {path} failed after {self.max_retries} attempts")

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def create_invoice(self, payload: dict) -> dict:
        """POST /v2/invoice — create a QPay payment invoice.

        Required payload keys (at minimum):
            invoice_code, sender_invoice_no, invoice_description, amount, callback_url
        Optional:
            sender_branch_code, sender_staff_code, invoice_receiver_code,
            invoice_receiver_data (register, name, email, phone),
            lines (list of line dicts)

        Returns dict with: invoice_id, qr_text, qPay_shortUrl, urls[], ...
        """
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
        """POST /v2/payment/check — query payments for a QPay invoice.

        Returns dict with: count, paid_amount, rows (list of payment records)
        Each row has: payment_id, payment_status, payment_amount, payment_date, ...
        """
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
        """GET /v2/payment/{payment_id} — fetch full payment detail."""
        _logger.info("QPay get_payment: payment_id=%s", payment_id)
        return self._make_request("GET", f"/v2/payment/{payment_id}")

    def cancel_invoice(self, invoice_id: str) -> dict:
        """DELETE /v2/invoice/{invoice_id} — cancel an unpaid invoice."""
        _logger.info("QPay cancel_invoice: invoice_id=%s", invoice_id)
        return self._make_request("DELETE", f"/v2/invoice/{invoice_id}")

    def create_ebarimt(self, payment_id: str, receiver_type: str = "CITIZEN") -> dict:
        """POST /v2/ebarimt/create — issue eBarimt (Mongolian e-receipt).

        Args:
            payment_id:    QPay payment_id from a confirmed payment
            receiver_type: "CITIZEN" or "COMPANY"
        """
        _logger.info("QPay create_ebarimt: payment_id=%s receiver_type=%s", payment_id, receiver_type)
        payload = {
            "payment_id": payment_id,
            "ebarimt_receiver_type": receiver_type,
        }
        return self._make_request("POST", "/v2/ebarimt/create", json_body=payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(response: requests.Response) -> dict:
        """Parse response JSON; raise QPayApiError on decode failure."""
        try:
            return response.json()
        except ValueError as exc:
            raise QPayApiError(
                f"QPay returned non-JSON response (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        """Parse JSON string without raising."""
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}
