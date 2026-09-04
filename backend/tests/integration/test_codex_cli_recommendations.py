import json
import subprocess
import sys
from pathlib import Path


def test_host_command_generates_and_imports_codex_cli_recommendations(tmp_path: Path) -> None:
    dashboard_file = tmp_path / "dashboard.json"
    dashboard_file.write_text(
        json.dumps(
            {
                "batch_id": 7,
                "trade_date": "2026-09-01",
                "rule_version": "v1",
                "completeness_rate": 1,
                "market_summary": {"up": 2000, "down": 3000, "flat": 100, "amount": 1},
                "indices": [],
                "candidates": [
                    {
                        "market": "SH",
                        "stock_code": "600000",
                        "stock_name": "浦发银行",
                        "score": 4,
                        "reasons": ["MACD_GOLDEN_CROSS"],
                        "outcome_status": "PENDING",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_file = tmp_path / "import.json"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import sys
prompt = sys.stdin.read()
assert '600000' in prompt
output = sys.argv[sys.argv.index('-o') + 1]
result = {'items': [{'market': 'SH', 'stock_code': '600000',
  'recommendation': 'FOCUS', 'ai_score': 82, 'horizon_trading_days': 5,
  'reasons': ['趋势信号一致'], 'risks': ['市场下跌家数较多'],
  'invalidation': '跌破MA20', 'confidence': 0.76}]}
open(output, 'w').write(json.dumps(result, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_ai_recommendations.py",
            "--dashboard-file",
            str(dashboard_file),
            "--output-import-file",
            str(import_file),
            "--codex-command",
            str(fake_codex),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "已生成 1 条待导入 AI 推荐" in result.stdout
    received = json.loads(import_file.read_text(encoding="utf-8"))
    assert received["batch_id"] == 7
    assert received["provider"] == "codex_cli"
    assert received["items"] == [
        {
            "market": "SH",
            "stock_code": "600000",
            "recommendation": "FOCUS",
            "ai_score": 82,
            "horizon_trading_days": 5,
            "reasons": ["趋势信号一致"],
            "risks": ["市场下跌家数较多"],
            "invalidation": "跌破MA20",
            "confidence": 0.76,
        }
    ]


def test_host_command_can_review_one_watchlist_stock(tmp_path: Path) -> None:
    dashboard_file = tmp_path / "dashboard.json"
    dashboard_file.write_text(
        json.dumps(
            {
                "batch_id": 9,
                "trade_date": "2026-09-02",
                "rule_version": "v1",
                "completeness_rate": 1,
                "market_summary": None,
                "indices": [],
                "candidates": [],
            }
        ),
        encoding="utf-8",
    )
    watchlist_file = tmp_path / "watchlist.json"
    watchlist_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "market": "SH",
                        "stock_code": "600000",
                        "stock_name": "浦发银行",
                        "close": 10.2,
                        "pct_change": 2,
                        "signal_codes": ["MACD_GOLDEN_CROSS"],
                        "risk_level": "low",
                        "realtime": None,
                    },
                    {"market": "SZ", "stock_code": "000001", "stock_name": "平安银行"},
                ]
            }
        ),
        encoding="utf-8",
    )
    import_file = tmp_path / "watchlist-import.json"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import sys
prompt = sys.stdin.read()
assert '600000' in prompt
assert '000001' not in prompt
output = sys.argv[sys.argv.index('-o') + 1]
result = {'items': [{'market': 'SH', 'stock_code': '600000',
  'recommendation': 'WATCH', 'ai_score': 66, 'horizon_trading_days': 3,
  'reasons': ['MACD信号为正向'], 'risks': ['缺少基本面数据'],
  'invalidation': '跌破MA20', 'confidence': 0.62}]}
open(output, 'w').write(json.dumps(result, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_ai_recommendations.py",
            "--scope",
            "watchlist",
            "--market",
            "SH",
            "--stock-code",
            "600000",
            "--dashboard-file",
            str(dashboard_file),
            "--watchlist-file",
            str(watchlist_file),
            "--output-import-file",
            str(import_file),
            "--codex-command",
            str(fake_codex),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(import_file.read_text(encoding="utf-8"))
    assert payload["scope"] == "watchlist"
    assert payload["prompt_version"] == "watchlist-review-v1"
    symbols = [
        (item["market"], item["stock_code"])
        for item in payload["evidence_snapshot"]["stocks"]
    ]
    assert symbols == [("SH", "600000")]
