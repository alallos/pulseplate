"""Sentry helpers: safe no-ops when the SDK is not initialized."""

from __future__ import annotations

import logging
log = logging.getLogger(__name__)


def capture_exception(exc: BaseException, **tags: str | None) -> None:
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for key, val in tags.items():
                if val is not None:
                    scope.set_tag(key, val)
            sentry_sdk.capture_exception(exc)
    except Exception:
        log.debug("sentry capture_exception failed", exc_info=True)


def capture_message(message: str, level: str = "error", **tags: str | None) -> None:
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for key, val in tags.items():
                if val is not None:
                    scope.set_tag(key, val)
            sentry_sdk.capture_message(message, level=level)
    except Exception:
        log.debug("sentry capture_message failed", exc_info=True)
