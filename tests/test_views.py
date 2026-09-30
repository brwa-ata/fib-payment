"""The HTTP surface: the public callback, and the two payment endpoints."""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from fib_payment import conf
from fib_payment.exceptions import FIBAPIError

from .testapp.models import Receipt

pytestmark = pytest.mark.django_db

CALLBACK_URL = '/api/fib/callback/'
PAYMENTS_URL = '/api/fib/payments/'


@pytest.fixture
def receipt():
    return Receipt.objects.create(amount=1, ref_no='PAY-1')


@pytest.fixture
def staff():
    client = APIClient()
    client.force_authenticate(User.objects.create_user('staff'))
    return client


def completed(receipt):
    receipt.refresh_from_db()
    return receipt.is_completed


@pytest.mark.parametrize('key', ['id', 'paymentId'])
def test_the_callback_is_public_and_re_verified(client, fib, receipt, key):
    fib.get_status.return_value = {'status': 'PAID'}

    response = client.post(
        CALLBACK_URL, {key: 'PAY-1', 'status': 'PAID'}, content_type='application/json'
    )

    assert response.status_code == 200
    fib.get_status.assert_called_once_with('PAY-1')
    assert completed(receipt)


def test_a_callback_without_an_id_is_refused(client, fib):
    response = client.post(CALLBACK_URL, {}, content_type='application/json')

    assert response.status_code == 400


def test_a_callback_fib_cannot_confirm_asks_for_a_retry(client, fib, receipt):
    fib.get_status.side_effect = FIBAPIError('down')

    response = client.post(
        CALLBACK_URL, {'id': 'PAY-1'}, content_type='application/json'
    )

    assert response.status_code == 503
    assert not completed(receipt)


def test_the_payment_endpoints_need_a_login(client):
    assert client.post(PAYMENTS_URL, {'receipt_id': 1}).status_code in (401, 403)
    assert client.get(f'{PAYMENTS_URL}1/status/').status_code in (401, 403)


def test_starting_returns_only_the_fields_a_client_needs(staff, fib):
    receipt = Receipt.objects.create(amount=5000)

    response = staff.post(PAYMENTS_URL, {'receipt_id': receipt.pk}, format='json')

    assert response.status_code == 201
    assert response.json() == {
        'paymentId': 'PAY-1',
        'readableCode': 'ABCD-1234',
        'qrCode': 'data:image/png;base64,QR',
        'validUntil': '2026-09-30T12:15:00Z',
        'personalAppLink': 'https://personal.fib.example/pay/PAY-1',
        'businessAppLink': 'https://business.fib.example/pay/PAY-1',
        'corporateAppLink': 'https://corporate.fib.example/pay/PAY-1',
    }
    # built from the request when FIB_CALLBACK_URL is blank
    assert fib.create_payment.call_args.kwargs['callback_url'] == (
        'http://testserver/api/fib/callback/'
    )


def test_the_callback_url_setting_wins(staff, fib, settings):
    settings.FIB_CALLBACK_URL = 'https://shop.example/api/fib/callback/'
    conf.reset()
    receipt = Receipt.objects.create(amount=5000)

    staff.post(PAYMENTS_URL, {'receipt_id': receipt.pk}, format='json')

    assert fib.create_payment.call_args.kwargs['callback_url'] == (
        'https://shop.example/api/fib/callback/'
    )


def test_starting_needs_a_receipt(staff, fib):
    assert staff.post(PAYMENTS_URL, {}, format='json').status_code == 422
    assert (
        staff.post(PAYMENTS_URL, {'receipt_id': 999}, format='json').status_code == 404
    )


def test_a_failed_start_is_a_502(staff, fib):
    fib.create_payment.side_effect = FIBAPIError('down')
    receipt = Receipt.objects.create(amount=5000)

    response = staff.post(PAYMENTS_URL, {'receipt_id': receipt.pk}, format='json')

    assert response.status_code == 502


def test_the_status_endpoint_syncs_and_reports(staff, fib, receipt):
    fib.get_status.return_value = {'status': 'PAID', 'paidAt': 'now'}

    response = staff.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.status_code == 200
    assert response.json() == {
        'status': 'PAID',
        'is_completed': True,
        'payment': {'status': 'PAID', 'paidAt': 'now'},
    }


def test_a_failed_status_check_is_a_502(staff, fib, receipt):
    fib.get_status.side_effect = FIBAPIError('down')

    response = staff.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.status_code == 502
