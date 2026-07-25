"""Payriff (payriff.com) adapter — Azerbaijani card-acquiring gateway.

⚠️  NEEDS VERIFICATION AGAINST LIVE PAYRIFF DOCS/SANDBOX BEFORE PRODUCTION.
See the ``PayriffProvider`` docstring below for exactly what is/isn't
confirmed and why the defensive webhook pattern in ``apps.payments.service``
exists to limit the blast radius of anything wrong here.
"""
import hashlib
import logging
import time
import uuid

import requests
from django.conf import settings

from apps.payments.models import Payment

from .base import PaymentProvider, ProviderOrder

logger = logging.getLogger(__name__)

_BASE_URL = 'https://api.payriff.com/api/v1/'
_REQUEST_TIMEOUT = 10

# Payriff v1 response envelope codes, as documented in the source below.
_CODE_SUCCESS = '00000'


class PayriffError(Exception):
    """Raised for any non-success Payriff response or an unparseable one.
    Deliberately fails closed: callers (service.py) must never interpret a
    PayriffError as "paid" or "failed" — only as "status unknown, don't
    touch the Payment row"."""


class PayriffProvider(PaymentProvider):
    """
    Adapter for Payriff API v1 (``https://api.payriff.com/api/v1/``).

    WHAT IS ACTUALLY VERIFIED (source: the open-source Laravel package
    ``NpaBackend/PayRiff``, github.com/NpaBackend/PayRiff, MIT — real code
    that exercises this API, not marketing copy):
      - Base URL ``https://api.payriff.com/api/v1/``.
      - Auth header ``Authorization: sha1(secretKey + encryptionToken)`` — a
        signature, not a bearer token.
      - ``POST createOrder`` request body:
        ``{"body": {"amount", "approveURL", "cancelURL", "declineURL",
        "currencyType", "description", "language"}, "encryptionToken",
        "merchant"}``.
      - ``createOrder`` response: ``{"code", "payload": {"sessionId",
        "transactionId", "paymentUrl"}}`` with ``code == "00000"`` on success
        (also documented: ``"01000"``=warning, ``"15000"``=error,
        ``"15400"``=invalid params, ``"14010"``=unauthorized,
        ``"14013"``=token not present, ``"14014"``=invalid token).
      - Flow: server creates the order → gets a hosted ``paymentUrl`` → the
        client opens that page in an external browser for card entry →
        Payriff redirects the browser to approveURL/cancelURL/declineURL.

    WHAT IS **NOT** VERIFIED (best-effort guesses only, flagged inline):
      - ``check_status`` below — no status-check endpoint could be confirmed
        from any source while writing this integration. The
        ``GET {base}orders/{transactionId}`` shape used here, signed the
        same way as ``createOrder``, is a plausible guess, nothing more.
      - The webhook payload's shape and, critically, its signature/auth
        scheme — could not be confirmed at all. This is exactly why
        ``apps.payments.service.handle_webhook_ping`` never trusts a webhook
        body's claimed status: it only uses the webhook as a nudge to call
        ``check_status`` (this same authenticated method) and updates
        ``Payment`` from THAT response. A wrong guess about the webhook
        signature therefore cannot fabricate a false "paid" state.
      - Open-search fragments hint at a possibly newer API version (v3?)
        with different field names entirely (``cardUuid``, ``callbackUrl``
        instead of approve/cancel/declineURL, ``orderId``+``paymentStatus``
        instead of ``transactionId``, a ``{code, message, route,
        internalMessage, responseId, payload}`` response envelope) — none of
        that could be confirmed either, so it is NOT implemented here.

    BEFORE GOING LIVE: register a sandbox/test merchant account at
    payriff.com (or use merchant credentials the user provides) and re-verify
    every field/endpoint above against the documentation Payriff exposes in
    the merchant dashboard after registration — that dashboard is where
    Payriff is reported to publish full, current API docs, and it could not
    be reached while writing this integration (docs.payriff.com is a
    JS-rendered site that wasn't scrapeable at the time).
    """

    def __init__(self, merchant_id=None, secret_key=None):
        self.merchant_id = merchant_id if merchant_id is not None else settings.PAYRIFF_MERCHANT_ID
        self.secret_key = secret_key if secret_key is not None else settings.PAYRIFF_SECRET_KEY

    def _encryption_token(self):
        # "time+random", per the source SDK — the exact composition rule
        # isn't confirmed beyond "unique per request", which this satisfies.
        return f'{int(time.time())}-{uuid.uuid4().hex}'

    def _signature(self, encryption_token):
        return hashlib.sha1(f'{self.secret_key}{encryption_token}'.encode('utf-8')).hexdigest()

    def _headers(self, encryption_token):
        return {
            'Authorization': self._signature(encryption_token),
            'Content-Type': 'application/json',
        }

    def create_order(self, *, amount, currency, description, approve_url, cancel_url, decline_url):
        encryption_token = self._encryption_token()
        body = {
            'body': {
                'amount': float(amount),
                'approveURL': approve_url,
                'cancelURL': cancel_url,
                'declineURL': decline_url,
                'currencyType': currency,
                'description': description,
                'language': 'AZ',
            },
            'encryptionToken': encryption_token,
            'merchant': self.merchant_id,
        }
        try:
            response = requests.post(
                f'{_BASE_URL}createOrder', json=body,
                headers=self._headers(encryption_token), timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PayriffError(f'Payriff createOrder request failed: {exc}') from exc

        if data.get('code') != _CODE_SUCCESS:
            raise PayriffError(f"Payriff createOrder returned code {data.get('code')!r}")

        payload = data.get('payload') or {}
        try:
            return ProviderOrder(
                order_id=payload['transactionId'],
                payment_url=payload['paymentUrl'],
                session_id=payload.get('sessionId', ''),
            )
        except KeyError as exc:
            raise PayriffError(f'Payriff createOrder response missing field: {exc}') from exc

    def check_status(self, provider_order_id):
        """
        ⚠️  UNVERIFIED — see the class docstring. No status-check endpoint
        for Payriff could be confirmed from any available source. This is a
        best-effort guess (``GET {base}orders/{transactionId}``, signed the
        same way as ``createOrder``, expecting a ``payload.orderStatus``-ish
        field) and is the single most likely piece of this integration to
        need rewriting once real docs/sandbox access are available.

        This is safe to have wrong: callers only ever use this method as a
        trusted re-check of the webhook's claim (see
        ``apps.payments.service.handle_webhook_ping``), and on any error or
        unrecognized response this raises ``PayriffError`` rather than
        guessing — the caller then leaves the ``Payment`` row untouched
        instead of ever marking it paid/failed on shaky information.
        """
        encryption_token = self._encryption_token()
        try:
            response = requests.get(
                f'{_BASE_URL}orders/{provider_order_id}',
                headers=self._headers(encryption_token), timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PayriffError(f'Payriff check_status request failed: {exc}') from exc

        if data.get('code') != _CODE_SUCCESS:
            raise PayriffError(f"Payriff check_status returned code {data.get('code')!r}")

        payload = data.get('payload') or {}
        raw_status = str(payload.get('orderStatus') or payload.get('status') or '').upper()

        if raw_status in ('APPROVED', 'SUCCESS', 'PAID', 'FULLY_PAID'):
            return Payment.STATUS_PAID
        if raw_status in ('DECLINED', 'FAILED', 'ERROR'):
            return Payment.STATUS_FAILED
        if raw_status in ('CANCELLED', 'CANCELED'):
            return Payment.STATUS_CANCELLED
        if raw_status in ('CREATED', 'PENDING', 'IN_PROGRESS', ''):
            return Payment.STATUS_PENDING

        logger.warning(
            'Payriff check_status: unrecognized orderStatus %r for order %s',
            raw_status, provider_order_id,
        )
        raise PayriffError(f'Unrecognized Payriff order status: {raw_status!r}')
