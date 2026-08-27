import tomllib
from pathlib import Path


def test_arm64_uses_maintained_javascript_runtime_without_legacy_collision() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text())
    assert "mini-racer==0.14.1" in config["project"]["dependencies"]
    assert "py-mini-racer; sys_platform == 'never'" in config["tool"]["uv"]["override-dependencies"]


def test_javascript_runtime_can_execute_calendar_decoder_primitives() -> None:
    from py_mini_racer import MiniRacer

    with MiniRacer() as runtime:
        assert runtime.eval("[2026, 8, 27].join('-')") == "2026-8-27"
