from pathlib import Path

from app.env_file import load_env_files, parse_env_text, REPO_ROOT


def test_parse_handles_comments_quotes_export_and_foreign_keys():
    text = """
# comment
PC1_A=1
export PC1_B = "two words"
PC1_C='x'   
PC1_D=4   # trailing comment
PC1_E=a#b
PATH=/evil
lowercase=1
PC1_NOEQUALS
"""
    assert parse_env_text(text) == {
        "PC1_A": "1",
        "PC1_B": "two words",
        "PC1_C": "x",
        "PC1_D": "4",
        "PC1_E": "a#b",
    }


def test_existing_environment_wins(tmp_path: Path):
    (tmp_path / "printer.env").write_text("PC1_A=file\nPC1_B=file\n")
    env = {"PC1_A": "real"}
    applied = load_env_files(tmp_path, environ=env)
    assert env == {"PC1_A": "real", "PC1_B": "file"}
    assert applied == {"PC1_B": "file"}


def test_local_file_overrides_shared_file(tmp_path: Path):
    (tmp_path / "printer.env").write_text("PC1_A=shared\nPC1_B=shared\n")
    (tmp_path / "printer.local.env").write_text("PC1_A=local\n")
    env = {}
    load_env_files(tmp_path, environ=env)
    assert env == {"PC1_A": "local", "PC1_B": "shared"}


def test_missing_files_are_fine(tmp_path: Path):
    env = {}
    assert load_env_files(tmp_path, environ=env) == {}
    assert env == {}


def test_disable_switch(tmp_path: Path):
    (tmp_path / "printer.env").write_text("PC1_A=1\n")
    env = {"PC1_DISABLE_ENV_FILE": "1"}
    assert load_env_files(tmp_path, environ=env) == {}
    assert "PC1_A" not in env


def test_shipped_printer_env_has_expected_values():
    values = parse_env_text((REPO_ROOT / "printer.env").read_text())
    assert values == {
        "PC1_QUEUED_PRESS_SETTLE_SECONDS": "10",
        "PC1_SERIAL_MAX_BYTES_PER_SECOND": "4800",
        "PC1_SERIAL_DARK_ROW_SECONDS": "0.05",
        "PC1_FEED_COMMAND": "esc_j",
        "PC1_CUTTER_FEED_LINES": "4",
        "PC1_CROSSWORD_BLOCK_FILL": "60",
    }
