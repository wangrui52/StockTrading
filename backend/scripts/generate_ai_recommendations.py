"""Use the authenticated host Codex CLI to review the active candidate list."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROMPT_VERSIONS = {
    "candidate": "candidate-review-v1",
    "watchlist": "watchlist-review-v1",
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "market",
                    "stock_code",
                    "recommendation",
                    "ai_score",
                    "horizon_trading_days",
                    "reasons",
                    "risks",
                    "invalidation",
                    "confidence",
                ],
                "properties": {
                    "market": {"type": "string", "minLength": 1, "maxLength": 8},
                    "stock_code": {"type": "string", "minLength": 1, "maxLength": 16},
                    "recommendation": {"enum": ["FOCUS", "WATCH", "AVOID"]},
                    "ai_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "horizon_trading_days": {"enum": [1, 3, 5]},
                    "reasons": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "risks": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "invalidation": {"type": "string", "minLength": 1, "maxLength": 500},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}


def _http_json(url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(
        url,
        data=body,
        method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - user-selected local API
            return json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise RuntimeError(f"StockTrading API 返回 {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 StockTrading API: {error.reason}") from error


def _load_evidence(args: argparse.Namespace) -> dict[str, Any]:
    if args.dashboard_file:
        dashboard = json.loads(Path(args.dashboard_file).read_text(encoding="utf-8"))
    else:
        dashboard = _http_json(f"{args.api_base.rstrip('/')}/dashboard")
    snapshot: dict[str, Any] = {
        key: dashboard.get(key)
        for key in (
            "batch_id",
            "trade_date",
            "rule_version",
            "completeness_rate",
            "market_summary",
            "indices",
        )
    }
    snapshot["scope"] = args.scope
    if args.scope == "candidate":
        source_items = dashboard.get("candidates", [])
        evidence_key = "candidates"
    else:
        if args.watchlist_file:
            watchlist = json.loads(Path(args.watchlist_file).read_text(encoding="utf-8"))
        else:
            watchlist = _http_json(f"{args.api_base.rstrip('/')}/watchlist/items")
        source_items = watchlist.get("items", [])
        evidence_key = "stocks"
    if args.market and args.stock_code:
        source_items = [
            item
            for item in source_items
            if item.get("market") == args.market and item.get("stock_code") == args.stock_code
        ]
    snapshot[evidence_key] = []
    for source_item in source_items[:20]:
        item = dict(source_item)
        item.pop("ai_recommendation", None)
        item.pop("ai_analysis", None)
        snapshot[evidence_key].append(item)
    return snapshot


def _run_codex(args: argparse.Namespace, evidence: dict[str, Any]) -> dict[str, Any]:
    scope_description = "默认策略候选" if evidence["scope"] == "candidate" else "用户自选股"
    prompt = (
        f"你是A股{scope_description}的证据审查助手。只能分析下方JSON中的股票，"
        "不得添加分析范围之外的股票，"
        "不得联网、读取其他文件或声称知道未提供的事实。规则得分不是收益预测。"
        "请逐只输出审慎的关注等级、0到100分、1/3/5交易日观察周期、正向依据、"
        "主要风险、结论失效条件和0到1置信度。风险信息不足时必须明确写入risks。\n\n"
        f"证据JSON：\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}"
    )
    with tempfile.TemporaryDirectory(prefix="stock-ai-") as directory:
        temporary = Path(directory)
        schema_path = temporary / "schema.json"
        output_path = temporary / "result.json"
        schema_path.write_text(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8")
        command = [
            args.codex_command,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-C",
            str(temporary),
        ]
        if args.model:
            command.extend(["--model", args.model])
        command.append("-")
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError(f"找不到 Codex CLI: {args.codex_command}") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Codex CLI 在 {args.timeout} 秒内未完成") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1:] or ["未知错误"]
            raise RuntimeError(f"Codex CLI 执行失败: {detail[0][:500]}")
        if not output_path.exists():
            raise RuntimeError("Codex CLI 未生成结构化输出")
        return json.loads(output_path.read_text(encoding="utf-8"))


def _validate_symbols(evidence: dict[str, Any], result: dict[str, Any]) -> None:
    evidence_key = "candidates" if evidence["scope"] == "candidate" else "stocks"
    allowed = {
        (item["market"], item["stock_code"]) for item in evidence.get(evidence_key, [])
    }
    items = result.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("Codex CLI 返回的推荐列表为空或格式错误")
    received = [(item.get("market"), item.get("stock_code")) for item in items]
    if len(received) != len(set(received)) or not set(received) <= allowed:
        raise RuntimeError("Codex CLI 返回了重复股票或候选范围之外的股票")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用本机 Codex CLI 生成候选股 AI 推荐")
    parser.add_argument("--api-base", default="http://localhost:8080/api/v1")
    parser.add_argument("--scope", choices=("candidate", "watchlist"), default="candidate")
    parser.add_argument("--market", choices=("SH", "SZ", "BJ"))
    parser.add_argument("--stock-code")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--model", help="可选 Codex 模型；默认使用账号配置")
    parser.add_argument("--timeout", type=int, default=180, choices=range(10, 601))
    parser.add_argument("--dashboard-file", help="离线验收：从文件读取看板 JSON")
    parser.add_argument("--watchlist-file", help="离线验收：从文件读取自选股 JSON")
    parser.add_argument("--output-import-file", help="离线验收：输出待导入 JSON，不调用 API")
    args = parser.parse_args(argv)
    if bool(args.market) != bool(args.stock_code):
        parser.error("--market 和 --stock-code 必须同时提供")
    if (args.market or args.watchlist_file) and args.scope != "watchlist":
        parser.error("个股参数和 --watchlist-file 仅适用于 --scope watchlist")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evidence = _load_evidence(args)
        evidence_key = "candidates" if args.scope == "candidate" else "stocks"
        if not evidence[evidence_key]:
            print("当前分析范围没有股票，无需生成 AI 推荐")
            return 0
        result = _run_codex(args, evidence)
        _validate_symbols(evidence, result)
        payload = {
            "batch_id": evidence["batch_id"],
            "scope": args.scope,
            "provider": "codex_cli",
            "model": args.model or "codex-account-default",
            "prompt_version": PROMPT_VERSIONS[args.scope],
            "evidence_snapshot": evidence,
            "items": result["items"],
        }
        if args.output_import_file:
            Path(args.output_import_file).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已生成 {len(result['items'])} 条待导入 AI 推荐")
        else:
            imported = _http_json(
                f"{args.api_base.rstrip('/')}/ai-recommendations/import", payload=payload
            )
            print(
                f"已导入 {imported['imported_count']} 条 AI 推荐，"
                f"批次 {imported['batch_id']}，运行 {imported['run_id']}"
            )
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"生成 AI 推荐失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
