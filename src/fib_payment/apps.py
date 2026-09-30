from django.apps import AppConfig


class FibPaymentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fib_payment'
    verbose_name = 'FIB Payment'

    def ready(self):
        from . import logs

        logs.configure()
