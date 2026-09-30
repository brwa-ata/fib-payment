# Changelog

All notable changes to this package. Projects pin a tag, so read every entry
between your tag and the new one before upgrading, and do what its
**Upgrade notes** say. How to release and how to upgrade: [UPDATING.md](UPDATING.md).

## v0.2.0

The package no longer assumes anything about the project it runs in.

- Changed: `FIB_RECEIPT_MODEL` is required; it used to default to
  `api.Receipt`.
- Changed: logs go to the `fib_payment` logger instead of `custom.logger`.
  New `FIB_LOG_FILE` (and `FIB_LOG_LEVEL`) write them to a file of the
  project's choosing, e.g. `BASE_DIR / 'logs' / 'fib_payment.log'`.
- Changed: the `payments/` endpoints are for staff by default (`IsAdminUser`);
  they used to accept any logged-in user. `FIB_VIEW_PERMISSION_CLASSES`
  changes who may call them.
- Fixed: an answer to create-payment without a `paymentId` raises
  `FIBAPIError` (a `FIBError`) instead of `ValueError`, so callers catching
  `FIBError` refuse it instead of answering a 500.
- Added: `service.cancel_payment(receipt)` and `service.refund_payment(receipt)`.
- Added: `FIB_ON_PAYMENT_REFUNDED` hook and `FIB_REFUNDED_STATUSES`.
- Removed: `default_app_config`, which Django 4.1+ ignores.

Upgrade notes:

- Set `FIB_RECEIPT_MODEL = '<app_label>.<Model>'` if the project relied on
  the old `api.Receipt` default.
- If the project read FIB's lines from `custom.logger`, set `FIB_LOG_FILE`
  or route the `fib_payment` logger in `LOGGING`.
- If anything calls `api/fib/payments/` as a non-staff user, set
  `FIB_VIEW_PERMISSION_CLASSES` to allow it.

## v0.1.0

First release, extracted from the Lavender system's `fib_payment` app with no
change in behaviour: the modules are the same code (only reformatted), so a
project that used the copied app gets exactly what it had.

- Create a FIB payment for a pending receipt and return its QR code, readable
  code and app links.
- Public `statusCallbackUrl` callback that re-reads every status from FIB.
- Receipt model, reference field and status field set by settings; optional
  hooks on completion and on failure.
- Client for FIB's token, create, status, cancel and refund calls.

Upgrade notes (moving from the copied `fib_payment` app):

- Delete the project's `fib_payment/` folder and install the package. The
  import name, `INSTALLED_APPS` entry, URLs and `FIB_*` settings are all
  unchanged.
