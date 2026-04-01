import pytest
import tempfile
import os
from pathlib import Path

from s7pymon.config import S7MonitorConfig


class TestS7MonitorConfigFromYaml:
    def test_simple_config(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(
            "address: 192.168.1.100\n"
            "rack: 0\n"
            "slot: 2\n"
            "port: 102\n"
            "interval: 0.5\n"
            "write_mode: confirm\n"
            "variables:\n"
            "  - DB210.Byte0:heartbeat\n"
            "  - DB210.Byte1:status\n"
        )
        config = S7MonitorConfig.from_yaml(cfg_file)
        assert config.address == "192.168.1.100"
        assert config.rack == 0
        assert config.slot == 2
        assert config.port == 102
        assert config.interval == 0.5
        assert config.write_mode == "confirm"
        assert config.variables == ["DB210.Byte0:heartbeat", "DB210.Byte1:status"]

    def test_minimal_config(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text("address: 10.0.0.1\n")
        config = S7MonitorConfig.from_yaml(cfg_file)
        assert config.address == "10.0.0.1"
        assert config.rack is None
        assert config.variables == []

    def test_db_range_config(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text("address: 10.0.0.1\ndb: 210\nstart: 0\nsize: 18\n")
        config = S7MonitorConfig.from_yaml(cfg_file)
        assert config.db == 210
        assert config.start == 0
        assert config.size == 18

    def test_empty_file(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text("")
        config = S7MonitorConfig.from_yaml(cfg_file)
        assert config.address is None

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            S7MonitorConfig.from_yaml("/nonexistent/path.yaml")

    def test_invalid_yaml_type(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            S7MonitorConfig.from_yaml(cfg_file)

    def test_log_config(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(
            "address: 10.0.0.1\n"
            "log_file: session.csv\n"
            "log_format: csv\n"
        )
        config = S7MonitorConfig.from_yaml(cfg_file)
        assert config.log_file == "session.csv"
        assert config.log_format == "csv"


class TestS7MonitorConfigMergeCli:
    def test_cli_overrides_config(self):
        config = S7MonitorConfig(address="10.0.0.1", rack=0, slot=2, interval=1.0)
        merged = config.merge_cli(address="192.168.1.1", interval=0.25)
        assert merged.address == "192.168.1.1"
        assert merged.interval == 0.25
        # Preserved from config
        assert merged.rack == 0
        assert merged.slot == 2

    def test_cli_variables_override(self):
        config = S7MonitorConfig(variables=["DB210.Byte0"])
        merged = config.merge_cli(variables=("DB100.Byte0", "DB100.Byte1"))
        assert merged.variables == ["DB100.Byte0", "DB100.Byte1"]

    def test_empty_cli_preserves_config(self):
        config = S7MonitorConfig(address="10.0.0.1", write_mode="confirm")
        merged = config.merge_cli()
        assert merged.address == "10.0.0.1"
        assert merged.write_mode == "confirm"

    def test_cli_write_mode_overrides(self):
        config = S7MonitorConfig(write_mode="disabled")
        merged = config.merge_cli(write_mode="allowed")
        assert merged.write_mode == "allowed"
