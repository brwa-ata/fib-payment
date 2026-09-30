"""FIB (First Iraqi Bank) Online Payment for Django: create a payment, let the
customer pay by QR, code or app link, and complete a receipt once FIB confirms
it. See the README for setup."""

from importlib.metadata import version

# ignored by Django >= 4.1, which finds FibPaymentConfig on its own; kept so
# the app loads the same way it did before it became a package
default_app_config = 'fib_payment.apps.FibPaymentConfig'

__version__ = version('fib-payment')
