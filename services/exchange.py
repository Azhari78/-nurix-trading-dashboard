from __future__ import annotations

import threading
from contextlib import suppress
from typing import Any

import ccxt

from services.config import Settings


def create_exchange(settings: Settings) -> ccxt.Exchange:
    options: dict[str, Any] = {"enableRateLimit": True, "timeout": 30000}
    if settings.api_key and settings.api_secret:
        options["apiKey"] = settings.api_key
        options["secret"] = settings.api_secret

    if settings.exchange_name == "binance":
        client = ccxt.binance(options)
    else:
        client = ccxt.gateio(options)

    if settings.use_sandbox:
        with suppress(Exception):
            client.set_sandbox_mode(True)

    return client


class ExchangeGateway:
    def __init__(self, settings: Settings) -> None:
        self.client = create_exchange(settings)
        self._lock = threading.RLock()

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            fn = getattr(self.client, method)
            return fn(*args, **kwargs)

    def close(self) -> None:
        with self._lock:
            with suppress(Exception):
                close_fn = getattr(self.client, "close", None)
                if callable(close_fn):
                    close_fn()
