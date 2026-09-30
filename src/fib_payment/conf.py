"""Configuration bridge between Django settings and the FIB client.

A project must set the three credentials (``FIB_BASE_URL``, ``FIB_CLIENT_ID``,
``FIB_CLIENT_SECRET``) and ``FIB_RECEIPT_MODEL``, the model that records its
payments. Everything else has a default.

The receipt binding is what makes the app reusable: point
``FIB_RECEIPT_MODEL`` / ``FIB_RECEIPT_REF_FIELD`` / ``FIB_RECEIPT_STATUS_FIELD``
at whatever model and fields a project uses.
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
    receipt_model: str = ''
    ref_field: str = 'ref_no'
    status_field: str = 'is_completed'
    # status handling
    paid_statuses: tuple = ('PAID',)
    failed_statuses: tuple = ('DECLINED',)
    refunded_statuses: tuple = ('REFUNDED',)
    # optional dotted paths to callables(receipt, status_value)
    on_completed: str = ''
    on_failed: str = ''
    on_refunded: str = ''
    # optional file the package writes its log to, and from which level
    log_file: str = ''
    log_level: str = 'INFO'
    # who may call the payments/ endpoints (the callback is always public)
    view_permission_classes: tuple = ('rest_framework.permissions.IsAdminUser',)
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
            receipt_model=getattr(settings, 'FIB_RECEIPT_MODEL', ''),
            ref_field=getattr(settings, 'FIB_RECEIPT_REF_FIELD', 'ref_no'),
            status_field=getattr(settings, 'FIB_RECEIPT_STATUS_FIELD', 'is_completed'),
            paid_statuses=tuple(getattr(settings, 'FIB_PAID_STATUSES', ('PAID',))),
            failed_statuses=tuple(
                getattr(settings, 'FIB_FAILED_STATUSES', ('DECLINED',))
            ),
            refunded_statuses=tuple(
                getattr(settings, 'FIB_REFUNDED_STATUSES', ('REFUNDED',))
            ),
            on_completed=getattr(settings, 'FIB_ON_PAYMENT_COMPLETED', ''),
            on_failed=getattr(settings, 'FIB_ON_PAYMENT_FAILED', ''),
            on_refunded=getattr(settings, 'FIB_ON_PAYMENT_REFUNDED', ''),
            log_file=str(getattr(settings, 'FIB_LOG_FILE', '') or ''),
            log_level=getattr(settings, 'FIB_LOG_LEVEL', 'INFO'),
            view_permission_classes=tuple(
                getattr(
                    settings,
                    'FIB_VIEW_PERMISSION_CLASSES',
                    ('rest_framework.permissions.IsAdminUser',),
                )
            ),
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
