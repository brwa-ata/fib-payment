"""The service: a receipt goes pending, and only FIB's answer completes it."""

from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured

from fib_payment import conf, service
from fib_payment.exceptions import FIBAPIError, FIBError

from . import hooks
from .testapp.models import Receipt

pytestmark = pytest.mark.django_db

CALLBACK_URL = 'https://shop.example/api/fib/callback/'


def completed(receipt):
    receipt.refresh_from_db()
    return receipt.is_completed


def test_starting_a_payment_leaves_the_receipt_pending(fib):
    receipt = Receipt.objects.create(
        amount=Decimal(25000), invoice_no='RV7', is_completed=True
    )

    payment = service.start_payment(receipt, callback_url=CALLBACK_URL)

    receipt.refresh_from_db()
    assert payment['qrCode'] == 'data:image/png;base64,QR'
    assert receipt.ref_no == 'PAY-1'
    assert receipt.is_completed is False
    fib.create_payment.assert_called_once_with(
        amount=Decimal(25000),
        callback_url=CALLBACK_URL,
        description='Payment RV7',
    )


def test_the_amount_and_description_can_differ_from_the_receipt(fib):
    """E.g. the receipt's amount plus a fee the customer pays on top."""
    receipt = Receipt.objects.create(amount=Decimal(10000))

    service.start_payment(
        receipt, callback_url=CALLBACK_URL, amount=Decimal(10250), description='x'
    )

    kwargs = fib.create_payment.call_args.kwargs
    assert kwargs['amount'] == Decimal(10250)
    assert kwargs['description'] == 'x'


def test_a_receipt_without_an_invoice_is_described_by_its_id(fib):
    receipt = Receipt.objects.create(amount=1)

    service.start_payment(receipt, callback_url=CALLBACK_URL)

    assert fib.create_payment.call_args.kwargs['description'] == (
        f'Payment #{receipt.pk}'
    )


def test_an_answer_without_a_payment_id_is_a_fib_error(fib):
    """Callers catch FIBError; a malformed answer must not escape as a 500."""
    fib.create_payment.return_value = {'unexpected': True}
    receipt = Receipt.objects.create(amount=1, is_completed=True)

    with pytest.raises(FIBError) as raised:
        service.start_payment(receipt, callback_url=CALLBACK_URL)

    assert isinstance(raised.value, FIBAPIError)
    assert raised.value.payload == {'unexpected': True}

    receipt.refresh_from_db()
    assert receipt.ref_no is None
    assert receipt.is_completed is True


def test_paid_completes_the_receipt_and_runs_the_hook_once(fib):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')
    fib.get_status.return_value = {'status': 'PAID'}

    service.sync_status(receipt)
    service.sync_status(receipt)

    assert completed(receipt)
    assert hooks.calls == [(receipt.pk, 'PAID')]


def test_unpaid_leaves_the_receipt_pending(fib):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')

    payload = service.sync_status(receipt)

    assert payload['status'] == 'UNPAID'
    assert not completed(receipt)
    assert hooks.calls == []
    assert hooks.failed_calls == []


def test_declined_runs_the_failed_hook_and_stays_pending(fib):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')
    fib.get_status.return_value = {'status': 'DECLINED'}

    service.sync_status(receipt)

    assert not completed(receipt)
    assert hooks.failed_calls == [(receipt.pk, 'DECLINED')]


def test_a_receipt_without_a_reference_cannot_be_synced(fib):
    with pytest.raises(ValueError):
        service.sync_status(Receipt.objects.create(amount=1))


def test_a_failing_hook_does_not_undo_the_payment(fib, settings):
    settings.FIB_ON_PAYMENT_COMPLETED = 'tests.hooks.explode'
    conf.reset()
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')

    service.apply_status(receipt, 'PAID')

    assert completed(receipt)


def test_the_paid_statuses_are_a_setting(fib, settings):
    settings.FIB_PAID_STATUSES = ['PAID', 'REFUND_REQUESTED']
    conf.reset()
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')

    service.apply_status(receipt, 'REFUND_REQUESTED')

    assert completed(receipt)


def test_the_callback_trusts_fib_not_the_body(fib):
    """A forged "paid" callback does nothing while FIB says unpaid."""
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')

    service.handle_callback('PAY-1', claimed_status='PAID')

    fib.get_status.assert_called_once_with('PAY-1')
    assert not completed(receipt)


def test_the_callback_completes_what_fib_confirms(fib):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')
    fib.get_status.return_value = {'status': 'PAID'}

    assert service.handle_callback('PAY-1', 'PAID') == receipt
    assert completed(receipt)


def test_a_callback_for_an_unknown_payment_is_ignored(fib):
    assert service.handle_callback('NOPE') is None
    fib.get_status.assert_not_called()


def test_the_receipt_model_and_fields_come_from_settings():
    assert service.get_receipt_model() is Receipt
    assert conf.get_conf().ref_field == 'ref_no'
    assert conf.get_conf().status_field == 'is_completed'


def test_the_receipt_model_must_be_set(settings):
    settings.FIB_RECEIPT_MODEL = ''
    conf.reset()

    with pytest.raises(ImproperlyConfigured):
        service.get_receipt_model()


def test_refunded_runs_the_refunded_hook_and_leaves_the_receipt(fib):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1', is_completed=True)
    fib.get_status.return_value = {'status': 'REFUNDED'}

    service.sync_status(receipt)

    assert completed(receipt)
    assert hooks.refunded_calls == [(receipt.pk, 'REFUNDED')]
    assert hooks.failed_calls == []


@pytest.mark.parametrize(
    ('call', 'client_call'),
    [
        ('cancel_payment', 'cancel'),
        ('refund_payment', 'refund'),
    ],
)
def test_cancel_and_refund_go_to_fib_with_the_receipts_payment(fib, call, client_call):
    receipt = Receipt.objects.create(amount=1, ref_no='PAY-1')
    getattr(fib, client_call).return_value = {'ok': True}

    assert getattr(service, call)(receipt) == {'ok': True}
    getattr(fib, client_call).assert_called_once_with('PAY-1')


@pytest.mark.parametrize('call', ['cancel_payment', 'refund_payment'])
def test_cancel_and_refund_need_a_payment(fib, call):
    with pytest.raises(ValueError):
        getattr(service, call)(Receipt.objects.create(amount=1))
