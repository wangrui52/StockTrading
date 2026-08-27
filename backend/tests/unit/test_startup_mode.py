from pathlib import Path


def test_startup_only_seeds_demo_when_explicitly_requested():
    script = Path("../start_local.command").read_text()
    assert 'if [[ "${START_LOCAL_DEMO:-0}" == 1 ]]; then' in script
    assert script.index('if [[ "${START_LOCAL_DEMO:-0}" == 1 ]]; then') < script.index(
        "python -m scripts.seed_demo"
    )
