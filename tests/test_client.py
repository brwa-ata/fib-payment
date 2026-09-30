"""The raw client: the token, what is sent to FIB, and reading its answers."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from fib_payment.client import FIBPaymentClient
from fib_payment.exceptions import FIBAPIError, FIBAuthError

TOKEN_URL = (
    'https://fib.example/auth/realms/fib-online-shop/protocol/openid-connect/token'
)
PAYMENTS_URL = 'https://fib.example/protected/v1/payments'


def make_client(token='T', **kwargs):
    client = FIBPaymentClient('https://fib.example/', 'client-id', 'secret', **kwargs)
    if token:
        client._token = token
        client._token_expiry = 10**12
    return client


def answer(status_code=200, **data):
    response = MagicMock(status_code=status_code, content=b'x', text='x')
    response.json.return_value = data
    return response


def test_the_token_is_a_client_credentials_grant_on_the_realm():
    token = answer(access_token='T1', expires_in=60)
    with (
        patch('fib_payment.client.requests.post', return_value=token) as post,
        patch('fib_payment.client.requests.request', return_value=answer()),
    ):
        make_client(token=None).get_status('PAY-1')

    assert post.call_args.args == (TOKEN_URL,)
    assert post.call_args.kwargs['data'] == {
        'grant_type': 'client_credentials',
        'client_id': 'client-id',
        'client_secret': 'secret',
    }


def test_the_token_is_reused_until_it_expires():
    token = answer(access_token='T1', expires_in=60)
    with (
        patch('fib_payment.client.requests.post', return_value=token) as post,
        patch('fib_payment.client.requests.request', return_value=answer()) as req,
    ):
        client = make_client(token=None)
        client.get_status('PAY-1')
        client.get_status('PAY-1')

    assert post.call_count == 1
    assert req.call_args.kwargs['headers']['Authorization'] == 'Bearer T1'


def test_an_expired_token_is_fetched_again():
    with (
        patch(
            'fib_payment.client.requests.post',
            return_value=answer(access_token='T2', expires_in=60),
        ) as post,
        patch('fib_payment.client.requests.request', return_value=answer()),
    ):
        client = make_client(token='OLD')
        client._token_expiry = 0
        client.get_status('PAY-1')

    assert post.call_count == 1
    assert client._token == 'T2'


def test_a_401_refreshes_the_token_once_and_retries():
    with (
        patch(
            'fib_payment.client.requests.post',
            return_value=answer(access_token='FRESH', expires_in=60),
        ),
        patch(
            'fib_payment.client.requests.request',
            side_effect=[answer(401), answer(status='PAID')],
        ) as req,
    ):
        result = make_client(token='STALE').get_status('PAY-1')

    assert result == {'status': 'PAID'}
    assert req.call_count == 2
    assert req.call_args.kwargs['headers']['Authorization'] == 'Bearer FRESH'


def test_a_payment_is_created_with_fibs_body():
    with patch(
        'fib_payment.client.requests.request',
        return_value=answer(paymentId='PAY-1'),
    ) as req:
        result = make_client().create_payment(
            15000.4, 'https://shop.example/api/fib/callback/', 'Payment RV7'
        )

    assert result == {'paymentId': 'PAY-1'}
    assert req.call_args.args == ('POST', PAYMENTS_URL)
    assert req.call_args.kwargs['json'] == {
        # IQD has no minor units, and FIB wants the amount as text
        'monetaryValue': {'amount': '15000', 'currency': 'IQD'},
        'statusCallbackUrl': 'https://shop.example/api/fib/callback/',
        'description': 'Payment RV7',
        'refundableFor': 'P7D',
    }


def test_optional_fields_are_sent_only_when_given():
    with patch('fib_payment.client.requests.request', return_value=answer()) as req:
        make_client().create_payment(
            10.5,
            'cb',
            currency='USD',
            expires_in='PT15M',
            refundable_for='',
            category='ECOMMERCE',
        )

    body = req.call_args.kwargs['json']
    assert body['monetaryValue'] == {'amount': '10.50', 'currency': 'USD'}
    assert body['expiresIn'] == 'PT15M'
    assert body['category'] == 'ECOMMERCE'
    assert 'refundableFor' not in body


@pytest.mark.parametrize(
    ('call', 'method', 'url'),
    [
        ('get_status', 'GET', f'{PAYMENTS_URL}/PAY-1/status'),
        ('cancel', 'POST', f'{PAYMENTS_URL}/PAY-1/cancel'),
        ('refund', 'POST', f'{PAYMENTS_URL}/PAY-1/refund'),
    ],
)
def test_each_payment_call_goes_to_its_endpoint(call, method, url):
    with patch('fib_payment.client.requests.request', return_value=answer()) as req:
        getattr(make_client(), call)('PAY-1')

    assert req.call_args.args == (method, url)


def test_an_error_answer_raises_with_its_status_and_body():
    with (
        patch(
            'fib_payment.client.requests.request',
            return_value=answer(404, title='payment not found'),
        ),
        pytest.raises(FIBAPIError) as raised,
    ):
        make_client().get_status('NOPE')

    assert raised.value.status_code == 404
    assert raised.value.payload == {'title': 'payment not found'}


def test_a_network_failure_is_a_fib_error():
    with (
        patch(
            'fib_payment.client.requests.request',
            side_effect=requests.ConnectionError('down'),
        ),
        pytest.raises(FIBAPIError),
    ):
        make_client().get_status('PAY-1')


@pytest.mark.parametrize(
    'token_answer',
    [
        answer(401, error='invalid_client'),
        answer(200, expires_in=60),
    ],
    ids=['refused', 'no access_token'],
)
def test_a_bad_token_answer_is_an_auth_error(token_answer):
    with (
        patch('fib_payment.client.requests.post', return_value=token_answer),
        pytest.raises(FIBAuthError),
    ):
        make_client(token=None).get_status('PAY-1')


def test_a_token_network_failure_is_an_auth_error():
    with (
        patch(
            'fib_payment.client.requests.post',
            side_effect=requests.ConnectionError('down'),
        ),
        pytest.raises(FIBAuthError),
    ):
        make_client(token=None).get_status('PAY-1')


def test_credentials_are_required():
    with pytest.raises(FIBAuthError):
        FIBPaymentClient('https://fib.example', '', 'secret')
