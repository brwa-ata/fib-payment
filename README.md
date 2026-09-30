# fib-payment

FIB (First Iraqi Bank) Online Payment for Django. A project creates a pending
receipt, this package creates the FIB payment for it and hands back what the
customer pays with (a QR code, a readable code and app links), and the receipt
is completed only once FIB itself confirms the payment.

- FIB API documentation: <https://documenter.getpostman.com/view/30814842/2sB2j68V73>
- Import name: `fib_payment`. Distribution name: `fib-payment`.
- Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+, requests.

`client.py` has no Django dependency. If all you need is a raw FIB API
wrapper, that one file (plus `exceptions.py`) is enough.

## Contents

- [How a payment works](#how-a-payment-works)
- [What is in the package](#what-is-in-the-package)
- [Install into a project](#install-into-a-project)
- [Settings reference](#settings-reference)
- [HTTP endpoints](#http-endpoints)
- [Tracking the status](#tracking-the-status)
- [Python API](#python-api)
- [FIB API contract](#fib-api-contract)
- [Logging](#logging)
- [Testing](#testing)
- [Updating and releasing](#updating-and-releasing)
- [Known limits](#known-limits)

## How a payment works

```
1. The project creates a pending receipt           is_completed = False
2. service.start_payment(receipt, callback_url=…)  FIB create-payment
   -> FIB's paymentId is stored in receipt.ref_no
   -> returns qrCode, readableCode, personalAppLink, validUntil, …
3. The customer pays in the FIB app, by one of:
   - scanning the QR code
   - opening the app link (on a phone)
   - typing the readable code: FIB app -> QuickPay -> Manual code
4. FIB calls the callback  POST api/fib/callback/ {id, status}
   -> the status is re-read from FIB, never trusted from the body
   -> on PAID: is_completed = True, and the completion hook runs once
5. Meanwhile the client polls a status endpoint, which re-reads the status
   from FIB too, so the payment completes even if the callback never arrives
```

A payment is payable for **15 minutes** (`validUntil`); after that FIB
declines it on its own. Before it is paid it can be cancelled; once paid it
cannot be declined, only refunded.

## What is in the package

| File | What it does |
| --- | --- |
| `client.py` | `FIBPaymentClient`: token, create, status, cancel, refund. No Django |
| `conf.py` | Reads the `FIB_*` settings once and builds one shared client |
| `service.py` | Ties the client to your receipt model: start, sync, complete, callback |
| `views.py` / `urls.py` | The callback FIB calls, plus two staff endpoints (see [HTTP endpoints](#http-endpoints)) |
| `exceptions.py` | `FIBError`, `FIBAuthError`, `FIBAPIError` |

The package has no models and no migrations.

## Install into a project

### 1. Install the package

Projects pin an exact tag, so nothing changes until someone moves the pin.

```bash
# uv
uv add git+https://github.com/brwa-ata/fib-payment --tag v0.1.0

# pip (requirements.txt)
fib-payment @ git+https://github.com/brwa-ata/fib-payment@v0.1.0
```

The server that installs it needs `git` and outbound HTTPS to github.com.

### 2. Register it and route it

```python
# settings.py
INSTALLED_APPS += ['fib_payment']

# urls.py
path('api/fib/', include('fib_payment.urls')),
```

The callback must be reachable by FIB without a login; if the project has
middleware that refuses anonymous requests, let `api/fib/callback/` through.

### 3. Add the credentials

```bash
# .env
FIB_BASE_URL=https://fib-stage.fib.iq
FIB_CLIENT_ID=...
FIB_CLIENT_SECRET=...
# Optional. Blank = built from the request as <host>/api/fib/callback/
FIB_CALLBACK_URL=
```

```python
# settings.py (python-decouple shown; os.environ works the same)
FIB_BASE_URL = config('FIB_BASE_URL')
FIB_CLIENT_ID = config('FIB_CLIENT_ID')
FIB_CLIENT_SECRET = config('FIB_CLIENT_SECRET')
FIB_CALLBACK_URL = config('FIB_CALLBACK_URL', default='')
```

`https://fib-stage.fib.iq` is FIB's stage environment (the address verified
with this package). For production, use the base URL FIB gives you with the
production credentials; the two environments have separate credentials.

**Set `FIB_CALLBACK_URL` in production.** Built from the request, the URL is
only right if Django knows it is behind HTTPS (`SECURE_PROXY_SSL_HEADER`) and
sees the public host; otherwise FIB is given an `http://` or internal address.

### 4. Point the app at your receipt model

The package never imports your model. It needs a model with a field for FIB's
`paymentId` and a boolean that stays `False` until FIB confirms the payment:

```python
FIB_RECEIPT_MODEL = 'shop.Receipt'        # default 'api.Receipt', see Known limits
FIB_RECEIPT_REF_FIELD = 'ref_no'          # default
FIB_RECEIPT_STATUS_FIELD = 'is_completed' # default

# Optional: run once, on the pending -> completed transition only
FIB_ON_PAYMENT_COMPLETED = 'shop.payments.on_fib_paid'
# Optional: run whenever FIB reports a failed status (DECLINED by default)
FIB_ON_PAYMENT_FAILED = 'shop.payments.on_fib_failed'
```

A hook is `fn(receipt, status_value)`. An exception inside a hook is logged
and swallowed: it never undoes a payment FIB has already confirmed.

### 5. Write the "start payment" and "status" endpoints

Creating the receipt stays in the project, because only the project knows who
pays, how much, and what the receipt means. A customer-facing pair looks like
this (Lavender's `basket/fib-payment/` and `basket/fib-status/` are the
reference):

```python
from django.db import transaction
from fib_payment import service as fib_service
from fib_payment.exceptions import FIBError

@transaction.atomic
def start(request):
    receipt = Receipt.objects.create(
        customer=request.user, amount=amount, is_completed=False, ...
    )
    try:
        payment = fib_service.start_payment(
            receipt,
            callback_url=settings.FIB_CALLBACK_URL
            or request.build_absolute_uri('/api/fib/callback/'),
        )
    except FIBError as exc:
        # a returned response does not roll back the atomic block by itself
        transaction.set_rollback(True)
        return Response({'message': f'FIB payment failed: {exc}'}, 422)

    return Response({
        'receipt_id': receipt.id,
        'ref_no': receipt.ref_no,                 # FIB paymentId
        'qr_code': payment.get('qrCode'),         # data: URL, use as <img src>
        'readable_code': payment.get('readableCode'),
        'personal_app_link': payment.get('personalAppLink'),
        'valid_until': payment.get('validUntil'),
    }, 201)

def status(request):
    # scope it to the caller, so nobody can read another customer's payment
    receipt = get_object_or_404(Receipt, pk=..., customer=request.user)
    if not receipt.is_completed:
        try:
            fib_service.sync_status(receipt)
        except FIBError:
            pass  # transient: keep it pending, the client polls again
    return Response({'is_completed': receipt.is_completed})
```

Pass `amount=` to `start_payment` to charge something other than
`receipt.amount`, e.g. the amount plus a fee the customer pays on top.

## Settings reference

| Setting | Default | Purpose |
| --- | --- | --- |
| `FIB_BASE_URL` | *(none, required)* | FIB environment base URL |
| `FIB_CLIENT_ID` / `FIB_CLIENT_SECRET` | *(none, required)* | Merchant credentials |
| `FIB_REALM` | `fib-online-shop` | Keycloak realm of the token endpoint |
| `FIB_CURRENCY` | `IQD` | Currency of every payment |
| `FIB_REFUNDABLE_FOR` | `P7D` | ISO-8601 window in which a payment can be refunded |
| `FIB_TIMEOUT` | `30` | HTTP timeout, seconds |
| `FIB_CALLBACK_URL` | *(blank)* | Absolute callback URL; blank builds `<host>/api/fib/callback/` from the request |
| `FIB_RECEIPT_MODEL` | `api.Receipt` | `app_label.Model` holding payments |
| `FIB_RECEIPT_REF_FIELD` | `ref_no` | Field that stores FIB's `paymentId` |
| `FIB_RECEIPT_STATUS_FIELD` | `is_completed` | Boolean completed by a `PAID` status |
| `FIB_PAID_STATUSES` | `('PAID',)` | Statuses that complete a receipt |
| `FIB_FAILED_STATUSES` | `('DECLINED',)` | Statuses that run the failed hook |
| `FIB_ON_PAYMENT_COMPLETED` | *(blank)* | Dotted path to `fn(receipt, status)`, run once on completion |
| `FIB_ON_PAYMENT_FAILED` | *(blank)* | Dotted path to `fn(receipt, status)`, run on a failed status |

Settings are read once per process (`conf.get_conf()`), so restart the app
server and every worker after changing them. In tests, call
`fib_payment.conf.reset()` after overriding one.

## HTTP endpoints

Mounted under whatever prefix the project gives `fib_payment.urls`
(`api/fib/` below).

| Endpoint | Who | What |
| --- | --- | --- |
| `POST api/fib/callback/` | FIB (public) | Body `{"id": "<paymentId>", "status": "..."}` (`paymentId` is accepted too). Re-reads the status from FIB and applies it. `200` done, `400` no id, `503` FIB could not be reached, so FIB retries |
| `POST api/fib/payments/` | Logged in | `{"receipt_id": ...}`: starts a payment for an existing pending receipt. Answers FIB's `paymentId`, `readableCode`, `qrCode`, `validUntil` and the three app links; `422` without `receipt_id`, `502` if FIB refuses |
| `GET api/fib/payments/<receipt pk>/status/` | Logged in | Re-syncs and answers `{status, is_completed, payment}`; `502` if FIB refuses |

The two `payments/` endpoints check only that the caller is logged in, not
that the receipt is theirs: keep them for staff, and give customers endpoints
of your own that scope the receipt to the caller (step 5 above).

## Tracking the status

FIB's recommended pattern, which combines the callback with polling:

1. Pass a callback URL when creating the payment (the package always does).
2. After creating it, give the callback about **15 seconds** to arrive.
3. If it has not, poll the status **every 5 seconds**.
4. Stop when the callback arrives, the payment is paid, or it expires
   (`validUntil`, 15 minutes after creation).

The callback alone is not enough: it can be late or never reach a server that
is not public (local development). Every status read goes back to FIB, so
polling and the callback can run at the same time; the receipt is completed
and the hook runs exactly once either way.

## Python API

### `fib_payment.service` (needs Django)

```python
from fib_payment import service

# Create a FIB payment for a pending receipt.
# Sets receipt.<ref field> = paymentId and receipt.<status field> = False.
payment = service.start_payment(
    receipt,
    callback_url='https://shop.example/api/fib/callback/',
    amount=None,        # default: receipt.amount
    description=None,   # default: "Payment <invoice_no>" or "Payment #<pk>"
)

# Re-read the status from FIB and apply it; returns FIB's status payload.
details = service.sync_status(receipt)
# details == {'paymentId': ..., 'status': 'PAID' | 'UNPAID' | ..., 'paidAt': ...,
#             'amount': {...}, 'paidBy': {'name': ..., 'iban': ...}, ...}

# Apply a status you already hold (completes on a paid status, idempotent).
service.apply_status(receipt, 'PAID')

# What the callback view calls; returns the receipt, or None if unknown.
service.handle_callback(payment_id, claimed_status=None)
```

`start_payment` raises `ValueError` if FIB answers without a `paymentId`, and
`sync_status` raises `ValueError` for a receipt with no reference; FIB
failures raise the exceptions below.

### `fib_payment.client.FIBPaymentClient` (no Django)

```python
from fib_payment.conf import get_client   # the shared client from settings
client = get_client()

# or standalone, anywhere:
from fib_payment.client import FIBPaymentClient
client = FIBPaymentClient('https://fib-stage.fib.iq', 'client-id', 'client-secret')

client.create_payment(15000, callback_url, 'Order 12', expires_in='PT15M')
client.get_status(payment_id)
client.cancel(payment_id)   # only while UNPAID
client.refund(payment_id)   # only once PAID; FIB errors otherwise
```

The token is cached for the `expires_in` FIB grants (about 60 seconds),
refreshed 5 seconds early, and fetched again once on any `401`. Fetching it is
guarded by a lock, so one client can be shared between threads.

### Exceptions

| Exception | Raised when |
| --- | --- |
| `FIBError` | Base class; catch this one |
| `FIBAuthError` | Missing credentials, or the token request failed or was refused |
| `FIBAPIError` | A payment call failed or FIB answered `4xx`/`5xx`; carries `status_code` and `payload` |

## FIB API contract

| Call | Request |
| --- | --- |
| Token | `POST {base}/auth/realms/{realm}/protocol/openid-connect/token`, form `grant_type=client_credentials`, `client_id`, `client_secret` |
| Create | `POST {base}/protected/v1/payments`, JSON `monetaryValue {amount, currency}`, `statusCallbackUrl`, `description`, optional `expiresIn`, `refundableFor`, `category` |
| Status | `GET {base}/protected/v1/payments/{paymentId}/status` |
| Cancel | `POST {base}/protected/v1/payments/{paymentId}/cancel` |
| Refund | `POST {base}/protected/v1/payments/{paymentId}/refund` |

- Every payment call carries `Authorization: Bearer <access_token>`.
- Amounts are text with no commas or points for IQD (`"15000"`).
- Create answers `paymentId`, `qrCode` (a `data:` image URL), `readableCode`,
  `validUntil`, `personalAppLink`, `businessAppLink`, `corporateAppLink`.
- Statuses: `UNPAID` (the default), `PAID`, `DECLINED`, `REFUND_REQUESTED`,
  `REFUNDED`. The status answer also carries `paidAt`, `amount` and `paidBy`
  (name, IBAN).
- A refund takes a few minutes on FIB's side and reaches the customer as a
  "Bank Operation".

## Logging

The package logs to the **`custom.logger`** logger (payments created and
completed, callbacks, hook failures). That name comes from the Lavender app
it was extracted from. Route it in `LOGGING`, or it propagates to the root
logger:

```python
LOGGING['loggers']['custom.logger'] = {
    'handlers': ['file'], 'level': 'INFO', 'propagate': False,
}
```

## Testing

### In your project

Mock FIB at the client the service uses:

```python
from unittest.mock import MagicMock, patch

fib = MagicMock()
fib.create_payment.return_value = {'paymentId': 'PAY-1', 'qrCode': '...'}
fib.get_status.return_value = {'status': 'PAID'}
with patch('fib_payment.service.get_client', return_value=fib):
    ...  # start / sync / callback
```

### The package's own tests

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests never call FIB. GitHub Actions runs them on Python 3.10 with the
oldest supported dependencies and on Python 3.13 with the newest.

### Against FIB's stage environment

From a project that has stage credentials in its `.env`:

```bash
uv run python manage.py shell -c "
from fib_payment.conf import get_client
c = get_client()
p = c.create_payment(1000, 'https://example.com/api/fib/callback/', 'package check')
print(p['paymentId'], c.get_status(p['paymentId'])['status'])  # ... UNPAID
c.cancel(p['paymentId'])
"
```

Add `--with-editable ../fib-payment` after `uv run` to try an unreleased
change (see [UPDATING.md](UPDATING.md)).

## Updating and releasing

How to release a new version and move a project onto it:
[UPDATING.md](UPDATING.md). Every release is listed in
[CHANGELOG.md](CHANGELOG.md).

## Known limits

These are kept from the Lavender app on purpose, so that moving to the
package changed nothing; a later minor release can clean them up (with
upgrade notes).

- `FIB_RECEIPT_MODEL` defaults to `api.Receipt`, Lavender's model. Other
  projects must set it.
- Logs go to `custom.logger` rather than a logger named after the package.
- If FIB answers create-payment without a `paymentId`, `start_payment` raises
  `ValueError`, which the `payments/` endpoint does not catch (a `500`).
- Cancel and refund exist on the client only; no endpoint exposes them, and a
  `REFUNDED` status does not un-complete a receipt.
- `__init__.py` still sets `default_app_config`, which Django 4.1+ ignores.
