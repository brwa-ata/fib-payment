from unittest.mock import MagicMock, patch

import pytest

from fib_payment import conf

from . import hooks


@pytest.fixture(autouse=True)
def fresh_state():
    """Settings are cached per process; every test starts from its own."""
    conf.reset()
    hooks.calls.clear()
    hooks.failed_calls.clear()
    yield
    conf.reset()


@pytest.fixture
def fib():
    """FIB as the service sees it."""
    client = MagicMock()
    client.create_payment.return_value = {
        'paymentId': 'PAY-1',
        'readableCode': 'ABCD-1234',
        'qrCode': 'data:image/png;base64,QR',
        'validUntil': '2026-09-30T12:15:00Z',
        'personalAppLink': 'https://personal.fib.example/pay/PAY-1',
        'businessAppLink': 'https://business.fib.example/pay/PAY-1',
        'corporateAppLink': 'https://corporate.fib.example/pay/PAY-1',
        'somethingElse': 'not for the client',
    }
    client.get_status.return_value = {'paymentId': 'PAY-1', 'status': 'UNPAID'}
    with patch('fib_payment.service.get_client', return_value=client):
        yield client
