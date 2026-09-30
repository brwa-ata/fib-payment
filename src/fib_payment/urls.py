from django.urls import path

from .views import FIBCallbackView, FIBPaymentStatusView, FIBStartPaymentView

urlpatterns = [
    path('payments/', FIBStartPaymentView.as_view(), name='fib-start-payment'),
    path(
        'payments/<int:pk>/status/',
        FIBPaymentStatusView.as_view(),
        name='fib-payment-status',
    ),
    path('callback/', FIBCallbackView.as_view(), name='fib-callback'),
]
