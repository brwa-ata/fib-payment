"""Configuration bridge between Django settings and the FIB client.

Every value has a sensible default so a new project only needs to set the
three credentials (``FIB_BASE_URL``, ``FIB_CLIENT_ID``, ``FIB_CLIENT_SECRET``).

The receipt binding is fully swappable, which is what makes this app reusable:
point ``FIB_RECEIPT_MODEL`` / ``FIB_RECEIPT_REF_FIELD`` /
``FIB_RECEIPT_STATUS_FIELD`` at whatever model+fields a project uses.
"""

from dataclasses import dataclass, field

from django.conf import settings

from .client import FIBPaymentClient


@dataclass
class FIBConf:
    base_url: str
    client_id: str
    client_secret: str
    realm: str = 'fib-online-shop'
    currency: str = 'IQD'
    refundable_for: str = 'P7D'
    timeout: int = 30
    callback_url: str = ''
    # receipt binding
    receipt_model: str = 'api.Receipt'
    ref_field: str = 'ref_no'
    status_field: str = 'is_completed'
    # status handling
    paid_statuses: tuple = ('PAID',)
    failed_statuses: tuple = ('DECLINED',)
    # optional dotted paths to callables(receipt, status_value)
    on_completed: str = ''
    on_failed: str = ''
    _client: FIBPaymentClient = field(default=None, repr=False, compare=False)


_conf = None


def get_conf():
    """Build (once) and return the FIB configuration from Django settings."""
    global _conf
    if _conf is None:
        _conf = FIBConf(
            base_url=getattr(settings, 'FIB_BASE_URL', ''),
            client_id=getattr(settings, 'FIB_CLIENT_ID', ''),
            client_secret=getattr(settings, 'FIB_CLIENT_SECRET', ''),
            realm=getattr(settings, 'FIB_REALM', 'fib-online-shop'),
            currency=getattr(settings, 'FIB_CURRENCY', 'IQD'),
            refundable_for=getattr(settings, 'FIB_REFUNDABLE_FOR', 'P7D'),
            timeout=int(getattr(settings, 'FIB_TIMEOUT', 30)),
            callback_url=getattr(settings, 'FIB_CALLBACK_URL', ''),
            receipt_model=getattr(settings, 'FIB_RECEIPT_MODEL', 'api.Receipt'),
            ref_field=getattr(settings, 'FIB_RECEIPT_REF_FIELD', 'ref_no'),
            status_field=getattr(settings, 'FIB_RECEIPT_STATUS_FIELD', 'is_completed'),
            paid_statuses=tuple(getattr(settings, 'FIB_PAID_STATUSES', ('PAID',))),
            failed_statuses=tuple(
                getattr(settings, 'FIB_FAILED_STATUSES', ('DECLINED',))
            ),
            on_completed=getattr(settings, 'FIB_ON_PAYMENT_COMPLETED', ''),
            on_failed=getattr(settings, 'FIB_ON_PAYMENT_FAILED', ''),
        )
    return _conf


def get_client():
    """Return a process-wide FIB client (token cache is shared)."""
    conf = get_conf()
    if conf._client is None:
        conf._client = FIBPaymentClient(
            base_url=conf.base_url,
            client_id=conf.client_id,
            client_secret=conf.client_secret,
            realm=conf.realm,
            currency=conf.currency,
            refundable_for=conf.refundable_for,
            timeout=conf.timeout,
        )
    return conf._client


def reset():
    """Drop cached config/client. Useful in tests and after settings override."""
    global _conf
    _conf = None
