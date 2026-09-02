"""Generic exponential-backoff retry decorator for flaky HTTP calls."""

import functools
import time

import requests


def with_backoff(max_retries=4, base_delay=1.0, max_delay=20.0, retry_statuses=(429, 500, 502, 503, 504)):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    last_exc = exc
                    if status not in retry_statuses or attempt == max_retries:
                        raise
                except requests.ConnectionError as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        raise
                time.sleep(min(delay, max_delay))
                delay *= 2
            raise last_exc

        return wrapper

    return decorator
