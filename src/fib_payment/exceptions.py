"""Exceptions raised by the FIB payment client.

Kept dependency-free so ``client.py`` can be copied into any project.
"""


class FIBError(Exception):
    """Base class for every FIB payment error."""


class FIBAuthError(FIBError):
    """Raised when obtaining an access token from FIB fails."""


class FIBAPIError(FIBError):
    """Raised when a FIB payment API call returns an error response."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
