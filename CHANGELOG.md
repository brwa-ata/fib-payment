# Changelog

All notable changes to this package. Projects pin a tag, so read every entry
between your tag and the new one before upgrading, and do what its
**Upgrade notes** say. How to release and how to upgrade: [UPDATING.md](UPDATING.md).

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
