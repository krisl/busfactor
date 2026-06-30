"""Output rules for automatic assembly management.

Rules run between the read and write phases of each poll cycle:

* **Follow** — copies an input value to an output variable every cycle (supports ``inverted`` for bits and ``delay_ms`` throttling).
* **Toggle** — alternates a bit every N cycles (heartbeat / watchdog).
* **Pulse** — sets a bit high for N cycles when explicitly triggered.

Rules are protocol-agnostic: source and target can be S7 DBs, EIP assemblies,
or mixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from .protocols import Connection
from .variable import DataType, S7Variable


@dataclass(frozen=True)
class OutputRule:
    target: str


@dataclass(frozen=True)
class FollowRule(OutputRule):
    source: str
    inverted: bool = False
    delay_ms: int = 0


@dataclass(frozen=True)
class ToggleRule(OutputRule):
    period: int = 1


@dataclass(frozen=True)
class PulseRule(OutputRule):
    duration: int = 1


_RuleKey = int


class RulesEngine:
    def __init__(self, rules: Sequence[OutputRule]):
        self._rules = rules
        self._counters: dict[_RuleKey, int] = {}
        self._toggle_state: dict[_RuleKey, bool] = {}
        self._pulse_remaining: dict[_RuleKey, int] = {}
        self._pending_follow: dict[int, tuple[float, Any, int, bytearray]] = {}
        self._verbose: bool = False

    def set_verbose(self, v: bool) -> None:
        self._verbose = v

    def _debug(self, msg: str) -> None:
        if self._verbose:
            import sys
            print(f"[rules] {msg}", file=sys.stderr, flush=True)

    @property
    def rules(self) -> list[OutputRule]:
        return list(self._rules)

    def trigger_pulse(self, target: str) -> None:
        for rule in self._rules:
            if isinstance(rule, PulseRule) and rule.target == target:
                self._pulse_remaining[id(rule)] = rule.duration
                return
        raise KeyError(f"No pulse rule for {target!r}")

    def _flush_pending(self, connection: Connection) -> None:
        import time

        now = time.monotonic()
        ready = [k for k, (at, *_) in self._pending_follow.items() if at <= now]
        for key in ready:
            _, source, offset, encoded = self._pending_follow.pop(key)
            connection.write_source(source, offset, encoded)

    def apply(
        self,
        connection: Connection,
        current_values: dict[str, str],
        buffers: dict[str, tuple[bytearray, int]] | None = None,
    ) -> None:
        self._flush_pending(connection)
        self._debug(f"apply() with {len(self._rules)} rules, {len(current_values)} values")
        for rule in self._rules:
            if isinstance(rule, FollowRule):
                self._apply_follow(rule, connection, current_values)
            elif isinstance(rule, ToggleRule):
                self._apply_toggle(rule, connection, buffers)
            elif isinstance(rule, PulseRule):
                self._apply_pulse(rule, connection)

    def _encode_follow(
        self,
        rule: FollowRule,
        connection: Connection,
        parsed: bool | int | float | str,
    ) -> bytearray | None:
        target_var = S7Variable.parse(rule.target)
        if target_var.type == DataType.BIT:
            if not isinstance(parsed, bool):
                return None
            current = connection.read_source(
                target_var.source, target_var.offset, 1
            )
            return target_var.encode_bit(current.data[0], parsed)
        return target_var.encode(parsed)

    def _apply_follow(
        self,
        rule: FollowRule,
        connection: Connection,
        current_values: dict[str, str],
    ) -> None:
        formatted = current_values.get(rule.source)
        if formatted is None:
            self._debug(f"follow {rule.target} <- {rule.source}: source not in current_values, skipping")
            return
        self._debug(f"follow {rule.target} <- {rule.source}: value={formatted}")
        target_var = S7Variable.parse(rule.target)
        parsed = target_var.parse_input(formatted)
        if rule.inverted:
            if target_var.type == DataType.BIT:
                parsed = not parsed
        if rule.delay_ms > 0:
            import time
            encoded = self._encode_follow(rule, connection, parsed)
            if encoded is not None:
                key = id(rule)
                self._pending_follow[key] = (
                    time.monotonic() + rule.delay_ms / 1000,
                    target_var.source,
                    target_var.offset,
                    encoded,
                )
        else:
            encoded = self._encode_follow(rule, connection, parsed)
            if encoded is not None:
                connection.write_source(target_var.source, target_var.offset, encoded)

    def _apply_toggle(self, rule: ToggleRule, connection: Connection, buffers: dict[str, tuple[bytearray, int]] | None = None) -> None:
        key = id(rule)
        counter = self._counters.get(key, 0) + 1
        target_var = S7Variable.parse(rule.target)
        self._debug(f"toggle {rule.target} period={rule.period} counter={counter}/{rule.period}")

        if counter >= rule.period:
            self._counters[key] = 0
            state = self._toggle_state.get(key, False)
            self._toggle_state[key] = not state
            self._debug(f"toggle {rule.target} -> firing, new_state={not state}")
            self._write_toggle_state(connection, target_var, not state, buffers)
        else:
            self._counters[key] = counter

    def _write_toggle_state(
        self,
        connection: Connection,
        var: Any,
        state: bool,
        buffers: dict[str, tuple[bytearray, int]] | None = None,
    ) -> None:
        if var.type == DataType.BIT:
            current_byte = None
            if buffers is not None:
                entry = buffers.get(str(var.source))
                if entry is not None:
                    data, data_start = entry
                    current_byte = data[var.offset - data_start]
            if current_byte is None:
                current = connection.read_source(var.source, var.offset, 1)
                current_byte = current.data[0]
            encoded = var.encode_bit(current_byte, state)
        else:
            encoded = var.encode(1 if state else 0)
        connection.write_source(var.source, var.offset, encoded)

    def _apply_pulse(self, rule: PulseRule, connection: Connection) -> None:
        key = id(rule)
        remaining = self._pulse_remaining.get(key, 0)
        target_var = S7Variable.parse(rule.target)

        if remaining > 0:
            self._pulse_remaining[key] = remaining - 1
            self._write_toggle_state(connection, target_var, True, None)
        else:
            self._write_toggle_state(connection, target_var, False, None)
