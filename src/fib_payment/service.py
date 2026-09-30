"""Bridge between the FIB client and a project's receipt model.

The only assumption about the receipt model is that it has:

* a *reference* field to store FIB's ``paymentId`` (default ``ref_no``)
* a boolean *status* field that gates whether the receipt counts
  (default ``is_completed``) — ``False`` until FIB confirms the payment.

Both field names and the model itself are configurable (see :mod:`fib_payment.conf`),
so this file never imports the project's model directly.
"""

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils.module_loading import import_string

from .conf import get_client, get_conf
from .exceptions import FIBAPIError
from .logs import logger


def get_receipt_model():
    model = get_conf().receipt_model
    if not model:
        raise ImproperlyConfigured(
            'Set FIB_RECEIPT_MODEL to the model that records payments, '
            "e.g. 'shop.Receipt'."
        )
    return apps.get_model(model)


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
        # surface the raw response so callers can see what FIB returned; a
        # FIBError, so the caller's own `except FIBError` refuses it cleanly
        raise FIBAPIError(
            f'FIB create-payment returned no paymentId: {response}',
            payload=response,
        )

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


def cancel_payment(receipt):
    """Cancel ``receipt``'s FIB payment while it is still unpaid.

    FIB declines it, and reports ``DECLINED`` from then on (which runs the
    failed hook on the next sync or callback). Once paid, a payment can only
    be refunded. Returns FIB's answer.
    """
    payment_id = _get_ref(receipt)
    if not payment_id:
        raise ValueError('Receipt has no FIB payment reference to cancel.')
    response = get_client().cancel(payment_id)
    logger.info('FIB payment %s cancelled for receipt %s', payment_id, receipt.pk)
    return response


def refund_payment(receipt):
    """Ask FIB to refund ``receipt``'s paid payment in full.

    FIB answers an error for a payment that is not ``PAID``. The status moves
    to ``REFUND_REQUESTED`` and then ``REFUNDED`` (which runs the refunded
    hook) once FIB has processed it, a few minutes later. Returns FIB's answer.
    """
    payment_id = _get_ref(receipt)
    if not payment_id:
        raise ValueError('Receipt has no FIB payment reference to refund.')
    response = get_client().refund(payment_id)
    logger.info(
        'FIB refund requested for payment %s, receipt %s', payment_id, receipt.pk
    )
    return response


@transaction.atomic
def apply_status(receipt, status_value):
    """Apply a FIB payment status to ``receipt``.

    * a paid status sets the status field to ``True`` and runs the completion
      hook, once: only on the pending -> completed transition;
    * a failed status runs the failed hook; a refunded one, the refunded hook.
      Neither touches the receipt, and both run every time the status is
      applied (a callback and a poll can report it twice), so write those
      hooks to be idempotent.
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
    elif status_value in conf.refunded_statuses:
        # the receipt is left as it is: what a refund undoes (a balance, an
        # order) is the project's to decide, in its refunded hook
        _run_hook(conf.on_refunded, receipt, status_value)
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
    except Exception:  # noqa: BLE001 - a hook must never break payment flow
        logger.exception('FIB payment hook %s failed', dotted_path)
