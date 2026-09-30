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
| `logs.py` | The `fib_payment` logger, and the optional log file |
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
FIB_RECEIPT_MODEL = 'shop.Receipt'        # required
FIB_RECEIPT_REF_FIELD = 'ref_no'          # default
FIB_RECEIPT_STATUS_FIELD = 'is_completed' # default

# Optional: run once, on the pending -> completed transition only
FIB_ON_PAYMENT_COMPLETED = 'shop.payments.on_fib_paid'
# Optional: run whenever FIB reports a failed status (DECLINED by default)
FIB_ON_PAYMENT_FAILED = 'shop.payments.on_fib_failed'
# Optional: run whenever FIB reports a refunded status (REFUNDED by default)
FIB_ON_PAYMENT_REFUNDED = 'shop.payments.on_fib_refunded'
```

A hook is `fn(receipt, status_value)`. An exception inside a hook is logged
and swallowed: it never undoes a payment FIB has already confirmed. Only the
completion hook is guaranteed to run once; the failed and refunded hooks run
each time that status is read (a callback and a poll can both see it), so
make them idempotent. The package never un-completes a refunded receipt:
what a refund undoes (a balance, an order) is the project's to decide, in its
refunded hook.

### 5. Choose where the log goes (optional)

```python
FIB_LOG_FILE = BASE_DIR / 'logs' / 'fib_payment.log'   # or 'fib_payment.log'
```

See [Logging](#logging); without it the package writes no file.

### 6. Write the "start payment" and "status" endpoints

Creating the receipt stays in the project, because only the project knows who
pays, how much, and what the receipt means. A customer-facing pair looks like
this:

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
| `FIB_RECEIPT_MODEL` | *(none, required)* | `app_label.Model` holding payments |
| `FIB_RECEIPT_REF_FIELD` | `ref_no` | Field that stores FIB's `paymentId` |
| `FIB_RECEIPT_STATUS_FIELD` | `is_completed` | Boolean completed by a `PAID` status |
| `FIB_PAID_STATUSES` | `('PAID',)` | Statuses that complete a receipt |
| `FIB_FAILED_STATUSES` | `('DECLINED',)` | Statuses that run the failed hook |
| `FIB_REFUNDED_STATUSES` | `('REFUNDED',)` | Statuses that run the refunded hook |
| `FIB_ON_PAYMENT_COMPLETED` | *(blank)* | Dotted path to `fn(receipt, status)`, run once on completion |
| `FIB_ON_PAYMENT_FAILED` | *(blank)* | Dotted path to `fn(receipt, status)`, run on a failed status |
| `FIB_ON_PAYMENT_REFUNDED` | *(blank)* | Dotted path to `fn(receipt, status)`, run on a refunded status |
| `FIB_LOG_FILE` | *(blank)* | File the package writes its log to; relative paths start at `BASE_DIR`. Blank: no file |
| `FIB_LOG_LEVEL` | `INFO` | Lowest level written to `FIB_LOG_FILE` |
| `FIB_VIEW_PERMISSION_CLASSES` | `['rest_framework.permissions.IsAdminUser']` | Dotted paths of the DRF permissions guarding the two `payments/` endpoints |

Settings are read once per process (`conf.get_conf()`), so restart the app
server and every worker after changing them. In tests, call
`fib_payment.conf.reset()` after overriding one.

## HTTP endpoints

Mounted under whatever prefix the project gives `fib_payment.urls`
(`api/fib/` below).

| Endpoint | Who | What |
| --- | --- | --- |
| `POST api/fib/callback/` | FIB (public) | Body `{"id": "<paymentId>", "status": "..."}` (`paymentId` is accepted too). Re-reads the status from FIB and applies it. `200` done, `400` no id, `503` FIB could not be reached, so FIB retries |
| `POST api/fib/payments/` | Staff | `{"receipt_id": ...}`: starts a payment for an existing pending receipt. Answers FIB's `paymentId`, `readableCode`, `qrCode`, `validUntil` and the three app links; `422` without `receipt_id`, `502` if FIB refuses |
| `GET api/fib/payments/<receipt pk>/status/` | Staff | Re-syncs and answers `{status, is_completed, payment}` (the payer's name and IBAN included); `502` if FIB refuses |

The two `payments/` endpoints find a receipt by its id alone, so they are for
staff: by default only `is_staff` users may call them. Set
`FIB_VIEW_PERMISSION_CLASSES` to change who may, and give customers endpoints
of your own that scope the receipt to the caller (step 6 above). There is no
endpoint for cancel or refund, since who may refund is the project's call; use
`service.cancel_payment()` / `service.refund_payment()` from a view of yours.

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

# Cancel an unpaid payment, or refund a paid one; both return FIB's answer.
service.cancel_payment(receipt)
service.refund_payment(receipt)
```

Every FIB failure raises a `FIBError` (below), including an answer to
create-payment that carries no `paymentId`. Calling `sync_status`,
`cancel_payment` or `refund_payment` on a receipt with no reference raises
`ValueError`: that is a bug in the caller, not something FIB said.

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

Everything is logged to the **`fib_payment`** logger: payments created,
completed, failed, refunded or cancelled, callbacks, and hook failures. Pick
one of:

- **Let the package write a file**, wherever the project keeps its logs:

  ```python
  FIB_LOG_FILE = BASE_DIR / 'logs' / 'fib_payment.log'   # a logs/ folder
  FIB_LOG_FILE = 'fib_payment.log'                        # the project root
  FIB_LOG_LEVEL = 'INFO'                                  # default
  ```

  A relative path starts at `BASE_DIR`, a missing folder is created, and the
  file is only created once something is logged. Those records then go to
  that file only, not to the root logger.

- **Route the logger yourself** in `LOGGING`, e.g. into a file the project
  already has:

  ```python
  LOGGING['loggers']['fib_payment'] = {
      'handlers': ['file'], 'level': 'INFO', 'propagate': False,
  }
  ```

- **Neither**: no file is written. Python drops `INFO` records and prints
  warnings and errors to stderr, as for any logger nobody configured.

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

- The status is read from FIB on every poll and callback; the package keeps
  no history of a payment's statuses. The status answer (`sync_status`'s
  return value) is the place to read `paidAt` and `paidBy` from.
- `REFUNDED` does not un-complete a receipt; the refunded hook is where a
  project undoes what the payment did.
- Amounts are sent in the currency set by `FIB_CURRENCY`; one project, one
  currency.
