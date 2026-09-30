"""HTTP surface for FIB payments.

* ``POST fib/payments/``            -> start a FIB payment for an existing receipt
* ``GET  fib/payments/<pk>/status`` -> re-sync + return the payment status
* ``POST fib/callback/``            -> FIB status callback (public, re-verified)

The payment endpoints operate on a receipt that the project has already
created (pending, ``is_completed=False``). Creating that receipt stays in the
project so this app never needs to know how receipts are built. They look a
receipt up by id alone, so they are for staff: ``FIB_VIEW_PERMISSION_CLASSES``
decides who may call them (``IsAdminUser`` unless the project says otherwise).
"""

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.module_loading import import_string
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import service
from .conf import get_conf
from .exceptions import FIBError
from .logs import logger

# fields from FIB's create-payment response worth returning to a client
_PAYMENT_FIELDS = (
    'paymentId',
    'readableCode',
    'qrCode',
    'validUntil',
    'personalAppLink',
    'businessAppLink',
    'corporateAppLink',
)


def _callback_url(request):
    conf = get_conf()
    if conf.callback_url:
        return conf.callback_url
    return request.build_absolute_uri(reverse('fib-callback'))


class ProjectPermissionsMixin:
    """Take the permission classes from ``FIB_VIEW_PERMISSION_CLASSES``."""

    def get_permissions(self):
        return [import_string(path)() for path in get_conf().view_permission_classes]


class FIBStartPaymentView(ProjectPermissionsMixin, APIView):
    """Start a FIB payment for an existing, pending receipt."""

    def post(self, request):
        receipt_id = request.data.get('receipt_id')
        if not receipt_id:
            return Response({'message': 'receipt_id is required'}, status=422)

        receipt = get_object_or_404(service.get_receipt_model(), pk=receipt_id)
        try:
            response = service.start_payment(
                receipt,
                callback_url=_callback_url(request),
                description=request.data.get('description'),
            )
        except FIBError as exc:
            logger.error('FIB start payment failed: %s', exc)
            return Response({'message': str(exc)}, status=502)

        return Response(
            {k: response[k] for k in _PAYMENT_FIELDS if k in response},
            status=status.HTTP_201_CREATED,
        )


class FIBPaymentStatusView(ProjectPermissionsMixin, APIView):
    """Re-sync a receipt's payment against FIB and return the status."""

    def get(self, request, pk):
        conf = get_conf()
        receipt = get_object_or_404(service.get_receipt_model(), pk=pk)
        try:
            payload = service.sync_status(receipt)
        except FIBError as exc:
            return Response({'message': str(exc)}, status=502)

        return Response(
            {
                'status': payload.get('status'),
                'is_completed': getattr(receipt, conf.status_field),
                'payment': payload,
            }
        )


class FIBCallbackView(APIView):
    """Public endpoint FIB calls when a payment's status changes."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        payment_id = request.data.get('id') or request.data.get('paymentId')
        if not payment_id:
            return Response({'message': 'missing payment id'}, status=400)

        try:
            service.handle_callback(payment_id, request.data.get('status'))
        except FIBError as exc:
            # tell FIB to retry later rather than swallow a transient failure
            logger.error('FIB callback processing failed: %s', exc)
            return Response({'message': str(exc)}, status=503)

        return Response({'message': 'ok'})
