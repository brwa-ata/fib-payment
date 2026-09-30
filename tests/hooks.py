"""Hooks the tests point FIB_ON_PAYMENT_COMPLETED / _FAILED / _REFUNDED at."""

calls = []
failed_calls = []
refunded_calls = []


def record(receipt, status_value):
    calls.append((receipt.pk, status_value))


def record_failed(receipt, status_value):
    failed_calls.append((receipt.pk, status_value))


def record_refunded(receipt, status_value):
    refunded_calls.append((receipt.pk, status_value))


def explode(receipt, status_value):
    raise RuntimeError('a bug in the project hook')
