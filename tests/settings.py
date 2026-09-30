"""The smallest Django project that can host the app, for the test suite."""

SECRET_KEY = 'fib-payment-tests'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'fib_payment',
    'tests.testapp',
]

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}

ROOT_URLCONF = 'tests.urls'

FIB_BASE_URL = 'https://fib.example'
FIB_CLIENT_ID = 'client-id'
FIB_CLIENT_SECRET = 'client-secret'
FIB_RECEIPT_MODEL = 'testapp.Receipt'
FIB_ON_PAYMENT_COMPLETED = 'tests.hooks.record'
FIB_ON_PAYMENT_FAILED = 'tests.hooks.record_failed'
