from django.urls import include, path

urlpatterns = [
    path('api/fib/', include('fib_payment.urls')),
]
