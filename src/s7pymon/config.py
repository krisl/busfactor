"""YAML config file support for s7pymon.

Config files allow storing connection settings and variable definitions
so long command lines don't need to be repeated.

Example config file (monitor.yaml):

    address: 192.168.1.100
    rack: 0
    slot: 2
    port: 102
    interval: 0.5
    write_mode: confirm
    variables:
      - DB210.Byte0:heartbeat
      - DB210.Byte1:status
      - DB210.Bit1.0:e_stop
      - EB.Byte0:input0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class S7MonitorConfig:
    """Parsed configuration for s7pymon."""

    address: str | None = None
    rack: int | None = None
    slot: int | None = None
    port: int | None = None
    timeout: int | None = None
    interval: float | None = None
    write_mode: str | None = None
    db: int | None = None
    start: int | None = None
    size: int | None = None
    variables: list[str] = field(default_factory=list)
    log_file: str | None = None
    log_format: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> S7MonitorConfig:
        """Load config from a YAML file.

        Raises FileNotFoundError if the file doesn't exist.
        Raises ValueError on invalid config content.
        """
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

        return cls(
            address=raw.get("address"),
            rack=raw.get("rack"),
            slot=raw.get("slot"),
            port=raw.get("port"),
            timeout=raw.get("timeout"),
            interval=raw.get("interval"),
            write_mode=raw.get("write_mode"),
            db=raw.get("db"),
            start=raw.get("start"),
            size=raw.get("size"),
            variables=[str(v) for v in raw["variables"]] if "variables" in raw else [],
            log_file=raw.get("log_file"),
            log_format=raw.get("log_format"),
        )

    def merge_cli(
        self,
        address: str | None = None,
        rack: int | None = None,
        slot: int | None = None,
        port: int | None = None,
        timeout: int | None = None,
        interval: float | None = None,
        write_mode: str | None = None,
        db_number: int | None = None,
        db_start: int | None = None,
        db_size: int | None = None,
        variables: tuple[str, ...] = (),
        log_file: str | None = None,
        log_format: str | None = None,
    ) -> S7MonitorConfig:
        """Return a new config with CLI args overriding file values.

        CLI values override config file values when explicitly provided.
        For click options, we pass None to indicate "not specified".
        """
        return S7MonitorConfig(
            address=address or self.address,
            rack=rack if rack is not None else self.rack,
            slot=slot if slot is not None else self.slot,
            port=port if port is not None else self.port,
            timeout=timeout if timeout is not None else self.timeout,
            interval=interval if interval is not None else self.interval,
            write_mode=write_mode or self.write_mode,
            db=db_number if db_number is not None else self.db,
            start=db_start if db_start is not None else self.start,
            size=db_size if db_size is not None else self.size,
            variables=list(variables) if variables else self.variables,
            log_file=log_file or self.log_file,
            log_format=log_format or self.log_format,
        )
