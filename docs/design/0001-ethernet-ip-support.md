# Ethernet/IP Scanner Mode + Output Rules

## Status

Draft — pending implementation.

## Summary

Add Ethernet/IP passive (scanner) mode to s7pymon, enabling it to act as an
EtherNet/IP scanner that connects to an adapter (PLC, drive, I/O block), reads
its input assemblies, and writes its output assemblies.  Along the way we
introduce an **output-rules** engine that automates common patterns (follow,
toggle, pulse) without needing a real PLC program.

The architecture refactoring is deliberately minimal — the existing pipeline
(read → decode → display) stays untouched; we add a parallel (apply rules →
send outputs) step behind the same connection abstraction.

---

## 1. Protocol Connection Abstraction

### Current state

`S7Connection` (connection.py) is a concrete class with no abstract base.
`MonitorEngine` (engine.py) takes an `S7Connection` directly.  `DemoConnection`
(demo.py) proves duck-typing works, but there is no interface to implement.

### Target state

A `Connection` ABC in a new `protocols.py` that both `S7Connection` and
`EIPConnection` implement:

```python
class Connection(ABC):
    protocol: ClassVar[str]  # "s7" | "eip"

    @property
    def state(self) -> ConnectionState: ...
    @property
    def connected(self) -> bool: ...
    @property
    def config(self) -> ConnectionConfig: ...

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def read_groups(self, groups: list[ReadGroup]) -> dict[str, tuple[bytearray, int]]: ...
    def write_assembly(self, target: str, data: bytearray) -> None: ...
```

`write_assembly` is the key new method — EIP writes always send a full output
assembly image.  `read_groups` replaces per-call `area_read` for efficiency
(EIP can read input + config in one call or separate calls depending on the
library; the grouping is an implementation detail).

### ConnectionConfig

Made generic with a `protocol` discriminator:

```python
@dataclass
class ConnectionConfig:
    protocol: str = "s7"
    address: str = ""
    tcp_port: int = 102       # S7 default
    timeout_ms: int = 3000
    # EIP-specific
    eip_port: int = 44818
    input_assembly: int = 101
    output_assembly: int = 100
    config_assembly: int = 102
    rpi_ms: int = 50
```

---

## 2. Variable Spec — EIP Addressing

### Format

```
EIP.<Assembly>.<Type><Offset>[.<Extra>][:Label]
```

Where `<Assembly>` is one of `Input`, `Output`, `Config`, or a numeric assembly
instance number that maps to `Input`/`Output`/`Config` via the config mapping.

### Examples

```yaml
variables:
  - EIP.Input.Byte0:heartbeat       # assembly 101 (input), byte 0
  - EIP.Input.Int4:temperature       # assembly 101, signed int at offset 4
  - EIP.Output.DWord8:setpoint       # assembly 100 (output), dword at offset 8
  - EIP.Input.Bit0.3:limit_switch    # assembly 101, bit 3 of byte 0
  - EIP.Input.String100.32:message   # assembly 101, string at offset 100
  - EIP.Config.Byte0:config_val      # assembly 102 (configuration)
```

Numeric aliases also work when the config defines the mapping:

```
EIP.101.Byte0                       # explicitly assembly 101 byte 0
```

### Parsing

A new regex in `variable.py` alongside the existing S7 patterns:

```python
_EIP_VAR_PATTERN = re.compile(
    r"^EIP\.(Input|Output|Config|\d+)\.(Byte|Int|DInt|Word|DWord|Real|Bit|String)"
    r"(\d+)(?:\.(\d+))?$",
    re.IGNORECASE,
)
```

### Internal representation

A new `Variable` dataclass that replaces `S7Variable` as the universal type,
with `S7Variable` becoming an alias or subclass:

```python
@dataclass(frozen=True)
class Variable:
    protocol: str          # "s7" | "eip"
    area: str              # "DB" | "EB" | "EIP.Input" | "EIP.101" | …
    db: int                # 0 for non-DB (S7) / assembly number decode
    type: DataType         # Byte | Int | DInt | Word | DWord | Real | Bit | String
    offset: int
    extra: int | None      # bit number or string max length
    label: str | None
```

`S7Type` is renamed to `DataType` — the types are universal across industrial
protocols and don't need an S7 prefix.

---

## 3. Output Rules

### Semantics

Every write to an output assembly goes through a rule engine.  Rules are
evaluated **after** each read and **before** shipping the output assembly.
Rules have **exclusive ownership** of their target — if a rule governs
`Output.Bit2.0`, manual writes to that spec are blocked with a clear error.

### Rule types

| Type | Behaviour | Config |
|------|-----------|--------|
| **follow** | Copy a source variable's value to the target. | `source` spec, `invert`, `scale` |
| **toggle** | Invert the target value at a regular interval. | `interval_ms` |
| **pulse** | When a source bit transitions, set the target high for N ms. | `source` spec, `duration_ms`, `edge` |

### Config format

```yaml
output_rules:
  - target: EIP.Output.Bit2.0
    follow: EIP.Input.Bit3.1
    invert: true

  - target: EIP.Output.Bit2.1
    toggle_ms: 500

  - target: EIP.Output.Bit2.0
    pulse: EIP.Input.Bit3.2
    duration_ms: 200
    edge: rising
```

### Internal representation

```python
@dataclass
class OutputRule:
    target_spec: str   # e.g. "EIP.Output.Bit2.0"
    type: str          # "follow" | "toggle" | "pulse"
    source_spec: str | None = None
    invert: bool = False
    toggle_ms: int = 0
    duration_ms: int = 0
    edge: str = "rising"   # "rising" | "falling" | "both"
```

### Integration into the poll cycle

The engine's poll becomes:

```
1. read_groups()                    → raw assembly buffers
2. decode(readings_from_assemblies) → VariableReading[]
3. apply_output_rules(buffers)      → mutates output assembly buffer
4. write_assembly(output_buffer)    → sends output to adapter
5. return Snapshot(readings, ...)
```

`apply_output_rules` is a method on `MonitorEngine` that:
- Evaluates follow rules (read source value from buffer, transform, write target)
- Evaluates toggle rules (track last-toggle timestamp, flip bit when due)
- Evaluates pulse rules (track rising/falling edges on source, set/reset timer)

---

## 4. Pulse Behaviour

### Definition

A **pulse** sets a target bit high for a fixed duration, then resets it.
It triggers on an **edge** of a source bit (or optionally a manual trigger).

```yaml
output_rules:
  - target: EIP.Output.Bit2.0
    pulse: EIP.Input.Bit3.2
    duration_ms: 200
    edge: rising
```

- When `Input.Bit3.2` transitions from 0→1:
  - `Output.Bit2.0` is set to 1 immediately
  - A timer is started for 200 ms
  - When the timer fires, `Output.Bit2.0` is set back to 0
  - The rising edge is consumed; no new pulse until another 0→1

### Manual pulse (UI)

A keyboard/mouse action on any writable bit triggers a one-shot pulse:

```
pulse Output.Bit2.0 150ms
```

This is a command-bar command, not a config rule — it fires once and is not
persisted.  The implementation reuses the same timer mechanism.

### Pulse state tracking

The engine maintains a `PulseState` dict keyed by target spec:

```python
@dataclass
class _PulseState:
    active: bool = False
    expiry: float = 0.0       # time.monotonic() deadline
    edge_armed: bool = True   # ready to catch the next edge
```

---

## 5. Config & CLI Changes

### YAML config

```yaml
protocol: eip
address: 192.168.1.100
port: 44818
interval: 0.05              # 50 ms for fast I/O
write_mode: confirm
eip:
  input_assembly: 101
  output_assembly: 100
  config_assembly: 102
  rpi_ms: 50
variables:
  - EIP.Input.Byte0:heartbeat
  - EIP.Input.Int4:temperature
  - EIP.Output.DWord8:setpoint
output_rules:
  - target: EIP.Output.Bit2.0
    follow: EIP.Input.Bit3.1
    invert: true
  - target: EIP.Output.Bit2.1
    toggle_ms: 500
```

### CLI

```bash
# Explicit protocol flag
s7pymon --protocol eip --input-assembly 101 --output-assembly 100 \
  --rpi 50 192.168.1.100 EIP.Input.Byte0 EIP.Output.Int4

# Default is still S7 for backward compat
s7pymon 192.168.1.100 DB210.Byte0
```

### Proposed CLI flags (documented, future)

```
--output-rule "target=EIP.Output.Bit2.0 follow=EIP.Input.Bit3.1 invert"
--output-rule "target=EIP.Output.Bit2.1 toggle_ms=500"
--pulse-ms 150                       # default pulse duration for manual pulses
```

These are **not implemented in the first pass** — only config-file rules for now.
They are documented here so the spec format is settled.

---

## 6. Implementation Plan (commit order)

Each commit is a small, testable unit.

| # | Commit | Files | Tests |
|---|--------|-------|-------|
| 1 | Rename `S7Type` → `DataType`, extract shared type constants. | `variable.py` | existing pass |
| 2 | Add `Connection` ABC + `ConnectionConfig` to new `protocols.py`. | `protocols.py`, `connection.py` | `test_protocols.py` |
| 3 | Add EIP variable spec parsing. | `variable.py` | `test_variable.py` |
| 4 | Implement `EIPConnection` (scaffold + tests). | `eip.py`, `connection.py` | `test_eip.py` |
| 5 | Add `OutputRule` dataclass + rules engine. | `engine.py`, `rules.py` | `test_rules.py` |
| 6 | Wire rules into `MonitorEngine.poll()`. | `engine.py` | `test_engine.py` |
| 7 | Update `Config` / `CLI` for `--protocol` + EIP options. | `config.py`, `cli.py` | `test_config.py`, `test_cli.py` |
| 8 | Pulse command-bar command + UI bits. | `app.py`, `web.py` | manual |
| 9 | Update README / help text. | `README.md`, `cli.py` | — |

---

## 7. Open Questions

- **EIP library**: cpppo, pyepics, or a lightweight minimal implementation?
  The design assumes only `connect(address, port)`, `forward_open(...)`,
  `read_assembly(n)`, `write_assembly(n, data)`, `forward_close()` — a ~200
  line wrapper, not a full library.
- **RPI vs. polled reads**: If using UDP I/O, data arrives asynchronously.
  For simplicity, v1 can use polled explicit messaging (read assembly on
  demand) — 50ms poll gives similar behaviour without the async complexity.
  Native UDP I/O can be a future optimisation.
- **Multiple adapters**: Not in scope for v1. One adapter per process.
