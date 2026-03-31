#!/usr/bin/env python3
"""CLI entry point for the S7 Monitor TUI.

Usage:
    s7mon <ip> [variables...] [OPTIONS]

Examples:
    # Monitor specific variables
    s7mon 192.168.1.100 DB210.Byte0 DB210.Byte1 DB210.Int4

    # Monitor a raw DB range
    s7mon 192.168.1.100 --db 210 --start 0 --size 18

    # With named variables
    s7mon 192.168.1.100 DB210.Byte0:heartbeat DB210.Byte1:status DB210.Bit1.0:e_stop

    # Custom connection settings
    s7mon 192.168.1.100 --rack 0 --slot 2 --port 1102 DB210.Byte0

    # Fast polling
    s7mon 192.168.1.100 --interval 0.25 DB210.Byte0 DB210.Byte1
"""

import sys

import click

from .connection import ConnectionConfig, S7Connection
from .variable import S7Type, S7Variable, compute_read_range


def parse_variable_arg(arg: str) -> S7Variable:
    """Parse a CLI variable argument, supporting optional label syntax.

    Formats:
        DB200.Byte0           -> variable with no label
        DB200.Byte0:heartbeat -> variable with label "heartbeat"
    """
    if ":" in arg:
        spec, label = arg.split(":", 1)
        return S7Variable.parse(spec, label=label)
    return S7Variable.parse(arg)


def build_default_variables(db: int, start: int, size: int) -> list[S7Variable]:
    """Build a default set of Byte variables covering the entire DB range."""
    return [
        S7Variable(db=db, type=S7Type.BYTE, offset=start + i, label=f"byte_{i}")
        for i in range(size)
    ]


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("address")
@click.argument("variables", nargs=-1)
@click.option("-r", "--rack", default=0, help="Rack number of S7 instance.")
@click.option("-s", "--slot", default=2, help="Slot number of S7 instance.")
@click.option("-p", "--port", default=102, help="TCP port for S7 communication.")
@click.option("-t", "--timeout", default=3000, help="Connection timeout in ms.")
@click.option("-i", "--interval", default=1.0, help="Poll interval in seconds.")
@click.option("--db", "db_number", default=None, type=int, help="DB number for raw range mode.")
@click.option("--start", "db_start", default=0, type=int, help="Start offset for raw range mode.")
@click.option("--size", "db_size", default=None, type=int, help="Number of bytes for raw range mode.")
def main(
    address: str,
    variables: tuple[str, ...],
    rack: int,
    slot: int,
    port: int,
    timeout: int,
    interval: float,
    db_number: int | None,
    db_start: int,
    db_size: int | None,
) -> None:
    """S7 Monitor — Live PLC data block monitor.

    ADDRESS is the IP address of the S7 PLC.

    VARIABLES are optional variable specs like DB210.Byte0 or DB210.Bit1.0:e_stop.
    Append :label to name a variable (e.g. DB210.Byte0:heartbeat).

    \b
    Supported types:
      Byte, Int, DInt, Word, DWord, Real, Bit, String

    \b
    Keyboard shortcuts in the TUI:
      e       Edit selected variable
      Space   Toggle bit variable
      :       Command bar (write/set/read)
      r       Force refresh
      p       Pause/resume polling
      c       Reconnect
      q       Quit
    """
    # Lazy import to keep CLI startup fast
    from .app import S7MonitorApp

    config = ConnectionConfig(
        address=address,
        rack=rack,
        slot=slot,
        tcp_port=port,
        timeout_ms=timeout,
    )
    connection = S7Connection(config)

    if variables:
        # Parse variable specs from command line
        parsed_vars = []
        for v in variables:
            try:
                parsed_vars.append(parse_variable_arg(v))
            except ValueError as e:
                click.echo(f"Error parsing variable '{v}': {e}", err=True)
                sys.exit(1)

        # All variables must be in the same DB
        dbs = {v.db for v in parsed_vars}
        if len(dbs) > 1:
            click.echo(f"Error: All variables must be in the same DB. Found: {dbs}", err=True)
            sys.exit(1)

        db = next(iter(dbs))
        start, size = compute_read_range(parsed_vars)

        # If explicit --db/--size given, use those to extend the read range
        if db_number is not None and db_number != db:
            click.echo(f"Error: --db {db_number} conflicts with variable DB{db}", err=True)
            sys.exit(1)
        if db_size is not None:
            size = max(size, db_size)
            start = min(start, db_start)

    elif db_number is not None and db_size is not None:
        # Raw range mode
        db = db_number
        start = db_start
        size = db_size
        parsed_vars = build_default_variables(db, start, size)
    else:
        click.echo("Error: Provide variable specs or --db and --size for raw range mode.", err=True)
        click.echo("Try: s7mon --help", err=True)
        sys.exit(1)

    app = S7MonitorApp(
        connection=connection,
        variables=parsed_vars,
        db=db,
        start=start,
        size=size,
        poll_interval=interval,
    )
    app.run()


def cli():
    """Entry point wrapper for setuptools console_scripts."""
    main()


if __name__ == "__main__":
    cli()
