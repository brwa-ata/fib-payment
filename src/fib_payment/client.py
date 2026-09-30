"""Standalone client for the FIB (First Iraqi Bank) Online Payment API.

This module has **no Django dependency** on purpose: it only needs ``requests``.
Copy it into any project and drive it with plain config values.

Contract (verified against fib.stage.fib.iq and the official FIB SDKs):

* Auth (Keycloak, client-credentials, tokens are short lived ~60s)::

      POST {base_url}/auth/realms/{realm}/protocol/openid-connect/token

* Create payment::

      POST {base_url}/protected/v1/payments

* Check status / cancel / refund::

      GET  {base_url}/protected/v1/payments/{payment_id}/status
      POST {base_url}/protected/v1/payments/{payment_id}/cancel
      POST {base_url}/protected/v1/payments/{payment_id}/refund

Payment status values: ``PAID | UNPAID | DECLINED | REFUND_REQUESTED | REFUNDED``.
"""

import threading
import time

import requests

from .exceptions import FIBAPIError, FIBAuthError

DEFAULT_REALM = 'fib-online-shop'
DEFAULT_CURRENCY = 'IQD'
DEFAULT_TIMEOUT = 30
# refresh a little before the token actually expires
TOKEN_EXPIRY_SKEW = 5


class FIBPaymentClient:
    """Thin, reusable wrapper around the FIB payment endpoints.

    The access token is cached in-memory and refreshed automatically based on
    the ``expires_in`` returned by FIB (and once more on any ``401``).
    """

    def __init__(
        self,
        base_url,
        client_id,
        client_secret,
        *,
        realm=DEFAULT_REALM,
        currency=DEFAULT_CURRENCY,
        refundable_for='P7D',
        grant_type='client_credentials',
        timeout=DEFAULT_TIMEOUT,
    ):
        if not base_url or not client_id or not client_secret:
            raise FIBAuthError(
                'FIB client requires base_url, client_id and client_secret.'
            )
        self.base_url = base_url.rstrip('/')
        self.client_id = client_id
        self.client_secret = client_secret
        self.realm = realm
        self.currency = currency
        self.refundable_for = refundable_for
        self.grant_type = grant_type
        self.timeout = timeout

        self._token = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    # -- urls ---------------------------------------------------------------
    @property
    def token_url(self):
        return f'{self.base_url}/auth/realms/{self.realm}/protocol/openid-connect/token'

    def _payment_url(self, *parts):
        path = '/'.join(str(p).strip('/') for p in parts)
        base = f'{self.base_url}/protected/v1/payments'
        return f'{base}/{path}' if path else base

    # -- auth ---------------------------------------------------------------
    def _fetch_token(self):
        try:
            resp = requests.post(
                self.token_url,
                data={
                    'grant_type': self.grant_type,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FIBAuthError(f'FIB token request failed: {exc}') from exc

        if resp.status_code != 200:
            raise FIBAuthError(
                f'FIB token request returned {resp.status_code}: {resp.text}'
            )

        data = resp.json()
        token = data.get('access_token')
        if not token:
            raise FIBAuthError('FIB token response did not contain access_token.')

        self._token = token
        self._token_expiry = time.monotonic() + int(data.get('expires_in', 60))
        return token

    def _get_token(self, force=False):
        with self._lock:
            valid = (
                self._token
                and time.monotonic() < self._token_expiry - TOKEN_EXPIRY_SKEW
            )
            if force or not valid:
                return self._fetch_token()
            return self._token

    # -- requests -----------------------------------------------------------
    def _request(self, method, url, *, json=None):
        headers = {'Authorization': f'Bearer {self._get_token()}'}
        if json is not None:
            headers['Content-Type'] = 'application/json'

        try:
            resp = requests.request(
                method, url, json=json, headers=headers, timeout=self.timeout
            )
            # token may have expired between calls -> refresh once and retry
            if resp.status_code == 401:
                headers['Authorization'] = f'Bearer {self._get_token(force=True)}'
                resp = requests.request(
                    method, url, json=json, headers=headers, timeout=self.timeout
                )
        except requests.RequestException as exc:
            raise FIBAPIError(f'FIB request to {url} failed: {exc}') from exc

        if resp.status_code >= 400:
            raise FIBAPIError(
                f'FIB API {method} {url} returned {resp.status_code}: {resp.text}',
                status_code=resp.status_code,
                payload=_safe_json(resp),
            )

        return _safe_json(resp)

    # -- public api ---------------------------------------------------------
    def create_payment(
        self,
        amount,
        callback_url,
        description='',
        *,
        currency=None,
        expires_in=None,
        refundable_for=None,
        category=None,
    ):
        """Create a payment and return FIB's response.

        Response keys include: ``paymentId``, ``readableCode``, ``qrCode``,
        ``validUntil``, ``personalAppLink``, ``businessAppLink``,
        ``corporateAppLink``.
        """
        body = {
            'monetaryValue': {
                'amount': _format_amount(amount, currency or self.currency),
                'currency': currency or self.currency,
            },
            'statusCallbackUrl': callback_url,
            'description': description or '',
        }
        if expires_in:
            body['expiresIn'] = expires_in
        refundable = (
            refundable_for if refundable_for is not None else self.refundable_for
        )
        if refundable:
            body['refundableFor'] = refundable
        if category:
            body['category'] = category
        return self._request('POST', self._payment_url(), json=body)

    def get_status(self, payment_id):
        """Return the payment status payload (``status`` key holds the state)."""
        return self._request('GET', self._payment_url(payment_id, 'status'))

    def cancel(self, payment_id):
        """Cancel an unpaid payment."""
        return self._request('POST', self._payment_url(payment_id, 'cancel'))

    def refund(self, payment_id):
        """Request a refund for a paid payment."""
        return self._request('POST', self._payment_url(payment_id, 'refund'))


def _format_amount(amount, currency):
    """FIB wants the amount as a string. IQD carries no minor units."""
    if str(currency).upper() == 'IQD':
        return str(int(round(float(amount))))  # noqa: RUF046
    return f'{float(amount):.2f}'


def _safe_json(resp):
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {'raw': resp.text}
