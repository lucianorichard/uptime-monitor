from pathlib import Path

import pytest

from uptime_monitor.config import ConfigError, load_config, parse_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def config(**target):
    return {"targets": [{"name": "site", "url": "https://site.test/", **target}]}


def test_defaults():
    s = parse_config(config())
    assert (s.interval_seconds, s.timeout_seconds, s.retention_days) == (60, 10, 30)
    t = s.targets[0]
    assert t.expected_label == "200-399" and t.follow_redirects is False
    assert t.timeout_seconds == 10


@pytest.mark.parametrize(
    "url",
    ["ftp://site.test/", "file:///etc/passwd", "site.test", "https://", "javascript:alert(1)", 42],
)
def test_only_http_urls_with_a_host(url):
    with pytest.raises(ConfigError):
        parse_config(config(url=url))


def test_credentials_in_urls_are_rejected():
    with pytest.raises(ConfigError, match="credentials"):
        parse_config(config(url="https://user:secret@site.test/"))


@pytest.mark.parametrize(
    "value, accepted, rejected",
    [
        (200, [200], [201]),
        ("200-299", [200, 299], [300]),
        ([200, "301-302"], [200, 301, 302], [303]),
    ],
)
def test_expected_status(value, accepted, rejected):
    t = parse_config(config(expected_status=value)).targets[0]
    assert all(t.accepts(c) for c in accepted)
    assert not any(t.accepts(c) for c in rejected)


@pytest.mark.parametrize("value", ["2xx", "300-200", 99, "600", [], True])
def test_invalid_expected_status(value):
    with pytest.raises(ConfigError):
        parse_config(config(expected_status=value))


@pytest.mark.parametrize(
    "data, message",
    [
        ({"targets": []}, "at least one"),
        (
            {
                "targets": [
                    {"name": "a", "url": "https://a.test/"},
                    {"name": "a", "url": "https://b.test/"},
                ]
            },
            "duplicate",
        ),
        (config(name="Bad Name"), "lowercase"),
        (config(colour="red"), "unknown key"),
        ({"monitor": {"interval": 5}, **config()}, "unknown key"),
        ({"extra": {}, **config()}, "unknown section"),
        ({"monitor": {"interval_seconds": 5}, **config()}, "interval_seconds"),
        (
            {"monitor": {"interval_seconds": 20, "timeout_seconds": 20}, **config()},
            "shorter than the interval",
        ),
        ({"monitor": {"interval_seconds": 120}, **config(timeout_seconds=90)}, "between"),
        (
            {"monitor": {"interval_seconds": 15}, **config(timeout_seconds=15)},
            "shorter than the interval",
        ),
        (config(follow_redirects="yes"), "true or false"),
    ],
)
def test_invalid_configs(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_bundled_example_configs_are_valid():
    for name in ("config.example.toml", "config.demo.toml"):
        assert load_config(str(EXAMPLES / name)).targets


def test_load_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(str(tmp_path / "missing.toml"))
    bad = tmp_path / "bad.toml"
    bad.write_text("[monitor\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(str(bad))
