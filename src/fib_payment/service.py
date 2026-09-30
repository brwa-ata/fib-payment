"""Bridge between the FIB client and a project's receipt model.

The only assumption about the receipt model is that it has:

* a *reference* field to store FIB's ``paymentId`` (default ``ref_no``)
* a boolean *status* field that gates whether the receipt counts
  (default ``is_completed``) — ``False`` until FIB confirms the payment.

Both field names and the model itself are configurable (see :mod:`fib_payment.conf`),
so this file never imports the project's model directly.
"""

import logging

from django.apps import apps
from django.db import transaction
from django.utils.module_loading import import_string

from .conf import get_client, get_conf

logger = logging.getLogger('custom.logger')


def get_receipt_model():
    return apps.get_model(get_conf().receipt_model)


def _get_ref(receipt):
    return getattr(receipt, get_conf().ref_field)


def start_payment(receipt, *, callback_url, description=None, amount=None):
    """Create a FIB payment for ``receipt`` and store its ``paymentId``.

    Marks the receipt pending (status field -> ``False``) and returns FIB's
    create-payment response (``paymentId``, ``qrCode``, ``readableCode``,
    ``personalAppLink``, ``validUntil``, ...).
    """
    conf = get_conf()
    client = get_client()

    response = client.create_payment(
        amount=amount if amount is not None else receipt.amount,
        callback_url=callback_url,
        description=description or _default_description(receipt),
    )

    payment_id = response.get('paymentId')
    if not payment_id:
        # surface the raw response so callers can see what FIB returned
        raise ValueError(f'FIB create-payment returned no paymentId: {response}')

    setattr(receipt, conf.ref_field, payment_id)
    setattr(receipt, conf.status_field, False)
    receipt.save(update_fields=[conf.ref_field, conf.status_field])
    logger.info('FIB payment %s created for receipt %s', payment_id, receipt.pk)
    return response


def sync_status(receipt):
    """Re-fetch the payment status from FIB and apply it to ``receipt``.

    Returns the FIB status payload.
    """
    payment_id = _get_ref(receipt)
    if not payment_id:
        raise ValueError('Receipt has no FIB payment reference to sync.')
    status_payload = get_client().get_status(payment_id)
    apply_status(receipt, status_payload.get('status'))
    return status_payload


@transaction.atomic
def apply_status(receipt, status_value):
    """Flip the receipt's status field based on a FIB payment status.

    A completed payment sets the status field to ``True`` (idempotent). Hooks,
    if configured, run only on the transition so side effects fire once.
    """
    conf = get_conf()
    already_completed = getattr(receipt, conf.status_field)

    if status_value in conf.paid_statuses:
        if not already_completed:
            setattr(receipt, conf.status_field, True)
            receipt.save(update_fields=[conf.status_field])
            _run_hook(conf.on_completed, receipt, status_value)
            logger.info('FIB payment for receipt %s marked completed', receipt.pk)
    elif status_value in conf.failed_statuses:
        _run_hook(conf.on_failed, receipt, status_value)
        logger.info('FIB payment for receipt %s reported %s', receipt.pk, status_value)
    return receipt


def handle_callback(payment_id, claimed_status=None):
    """Handle FIB's status callback for ``payment_id``.

    FIB's callback body is not trusted for the amount/status — the status is
    always re-verified against FIB before the receipt is updated. Returns the
    receipt, or ``None`` when no matching receipt exists.
    """
    conf = get_conf()
    receipt = get_receipt_model().objects.filter(**{conf.ref_field: payment_id}).first()
    if receipt is None:
        logger.warning('FIB callback for unknown payment %s', payment_id)
        return None

    status_payload = get_client().get_status(payment_id)
    verified_status = status_payload.get('status')
    logger.info(
        'FIB callback payment=%s claimed=%s verified=%s',
        payment_id,
        claimed_status,
        verified_status,
    )
    apply_status(receipt, verified_status)
    return receipt


def _default_description(receipt):
    invoice = getattr(receipt, 'invoice_no', None)
    return f'Payment {invoice}' if invoice else f'Payment #{receipt.pk}'


def _run_hook(dotted_path, receipt, status_value):
    if not dotted_path:
        return
    try:
        import_string(dotted_path)(receipt, status_value)
    except Exception:  # a hook must never break payment flow
        logger.exception('FIB payment hook %s failed', dotted_path)
