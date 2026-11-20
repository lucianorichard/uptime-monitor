"""Configuration: the only source of monitored URLs.

Targets come exclusively from a local TOML file managed by the operator. No
HTTP endpoint can add a target or request an arbitrary URL, so the service
cannot be used as an open proxy or to probe other systems.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from urllib.parse import urlsplit

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MAX_TARGETS = 50
DEFAULT_EXPECTED = "200-399"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    timeout_seconds: float
    expected_status: tuple[tuple[int, int], ...]
    follow_redirects: bool = False

    def accepts(self, status_code: int) -> bool:
        return any(lo <= status_code <= hi for lo, hi in self.expected_status)

    @property
    def expected_label(self) -> str:
        return ", ".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in self.expected_status)


@dataclass(frozen=True)
class Settings:
    interval_seconds: int
    timeout_seconds: float
    retention_days: int
    database: str
    targets: tuple[Target, ...]

    def target(self, name: str) -> Target | None:
        return next((t for t in self.targets if t.name == name), None)


_MONITOR_KEYS = {"interval_seconds", "timeout_seconds", "retention_days", "database"}
_TARGET_KEYS = {"name", "url", "timeout_seconds", "expected_status", "follow_redirects"}


def _number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise ConfigError(f"{name} must be a number between {low:g} and {high:g}")
    return value


def _expected(value, where: str) -> tuple[tuple[int, int], ...]:
    items = value if isinstance(value, list) else [value]
    ranges = []
    for item in items:
        text = str(item).strip()
        match = re.fullmatch(r"(\d{3})(?:-(\d{3}))?", text)
        if isinstance(item, bool) or not match:
            raise ConfigError(
                f'{where}.expected_status: invalid entry "{item}" (use 200 or "200-299")'
            )
        lo, hi = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 100 <= lo <= hi <= 599:
            raise ConfigError(f'{where}.expected_status: "{item}" is not a valid status range')
        ranges.append((lo, hi))
    if not ranges:
        raise ConfigError(f"{where}.expected_status must not be empty")
    return tuple(ranges)


def _url(value, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where}.url must be a string")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ConfigError(f"{where}.url must use http or https")
    if not parts.hostname:
        raise ConfigError(f"{where}.url has no host")
    if parts.username or parts.password:
        # Credentials would end up in the database, logs and dashboard.
        raise ConfigError(f"{where}.url must not contain credentials")
    return value


def parse_config(data: dict) -> Settings:
    unknown = set(data) - {"monitor", "targets"}
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")
    monitor = data.get("monitor", {})
    if set(monitor) - _MONITOR_KEYS:
        raise ConfigError(
            f"monitor: unknown key(s) {', '.join(sorted(set(monitor) - _MONITOR_KEYS))}"
        )

    interval = int(
        _number(monitor.get("interval_seconds", 60), "monitor.interval_seconds", 10, 86400)
    )
    timeout = _number(monitor.get("timeout_seconds", 10), "monitor.timeout_seconds", 1, 60)
    retention = int(_number(monitor.get("retention_days", 30), "monitor.retention_days", 1, 365))
    database = monitor.get("database", "data/uptime.db")
    if not isinstance(database, str) or not database:
        raise ConfigError("monitor.database must be a file path")

    raw_targets = data.get("targets", [])
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError("at least one [[targets]] entry is required")
    if len(raw_targets) > MAX_TARGETS:
        raise ConfigError(f"at most {MAX_TARGETS} targets are supported")

    targets, seen = [], set()
    for i, raw in enumerate(raw_targets):
        where = f"targets[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} must be a table")
        if set(raw) - _TARGET_KEYS:
            raise ConfigError(
                f"{where}: unknown key(s) {', '.join(sorted(set(raw) - _TARGET_KEYS))}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ConfigError(f"{where}.name must be lowercase letters, digits, '-' or '_'")
        if name in seen:
            raise ConfigError(f'duplicate target name "{name}"')
        seen.add(name)
        target_timeout = _number(
            raw.get("timeout_seconds", timeout), f"{where}.timeout_seconds", 1, 60
        )
        if target_timeout >= interval:
            raise ConfigError(f"{where}.timeout_seconds must be shorter than the interval")
        follow = raw.get("follow_redirects", False)
        if not isinstance(follow, bool):
            raise ConfigError(f"{where}.follow_redirects must be true or false")
        targets.append(
            Target(
                name=name,
                url=_url(raw.get("url"), where),
                timeout_seconds=float(target_timeout),
                expected_status=_expected(raw.get("expected_status", DEFAULT_EXPECTED), where),
                follow_redirects=follow,
            )
        )
    if timeout >= interval:
        raise ConfigError("monitor.timeout_seconds must be shorter than the interval")
    return Settings(interval, float(timeout), retention, database, tuple(targets))


def load_config(path: str) -> Settings:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    return parse_config(data)
