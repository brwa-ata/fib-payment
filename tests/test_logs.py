"""Logging: to the `fib_payment` logger, and to a file only when asked."""

import logging

import pytest

from fib_payment import conf, logs, service

from .testapp.models import Receipt


@pytest.fixture(autouse=True)
def clean_logger():
    """Leave the logger as the next test expects it."""
    before = list(logs.logger.handlers), logs.logger.propagate, logs.logger.level
    yield
    for handler in logs.logger.handlers:
        if handler not in before[0]:
            handler.close()
    logs.logger.handlers[:] = before[0]
    logs.logger.propagate, logs.logger.level = before[1], before[2]


def set_log_file(settings, value):
    settings.FIB_LOG_FILE = value
    conf.reset()


def test_without_a_file_nothing_is_attached(settings):
    set_log_file(settings, '')

    assert logs.configure() is None
    assert logs.logger.name == 'fib_payment'


@pytest.mark.django_db
def test_the_file_gets_the_packages_log(settings, tmp_path, fib):
    set_log_file(settings, tmp_path / 'logs' / 'fib.log')

    logs.configure()
    service.start_payment(
        Receipt.objects.create(amount=1), callback_url='https://x/cb/'
    )

    text = (tmp_path / 'logs' / 'fib.log').read_text()
    assert 'INFO fib_payment: FIB payment PAY-1 created for receipt' in text


def test_a_relative_file_is_under_base_dir(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    set_log_file(settings, 'fib.log')

    handler = logs.configure()

    assert handler.baseFilename == str((tmp_path / 'fib.log').resolve())
    assert logs.logger.propagate is False


def test_configuring_twice_adds_one_handler(settings, tmp_path):
    set_log_file(settings, tmp_path / 'fib.log')

    first = logs.configure()
    second = logs.configure()

    assert first is second
    assert logs.logger.handlers.count(first) == 1


def test_the_level_is_a_setting(settings, tmp_path):
    settings.FIB_LOG_LEVEL = 'WARNING'
    set_log_file(settings, tmp_path / 'fib.log')

    logs.configure()

    assert logs.logger.level == logging.WARNING
