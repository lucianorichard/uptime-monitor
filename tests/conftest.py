import pytest

from uptime_monitor.config import parse_config
from uptime_monitor.storage import Storage


def make_settings(*targets, interval=30, timeout=2):
    return parse_config(
        {
            "monitor": {
                "interval_seconds": interval,
                "timeout_seconds": timeout,
                "database": ":memory:",
            },
            "targets": list(targets) or [{"name": "site", "url": "https://site.test/"}],
        }
    )


@pytest.fixture
def storage():
    store = Storage(":memory:")
    yield store
    store.close()
