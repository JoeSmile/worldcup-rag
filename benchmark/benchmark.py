import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from core.config import settings

DEFAULT_API_BASE = settings.benchmark_api_base
DEFAULT_GOLDEN_PATH = "benchmark/golden.json"
DEFAULT_RESULT_PATH = "benchmark/result.json"


@dataclass
class BenchmarkConfig:
    api_base: str = DEFAULT_API_BASE
    golden_path: str = DEFAULT_GOLDEN_PATH
    result_path: str = DEFAULT_RESULT_PATH
    timeout: float = 60.0
    workers: int = 1
    retries: int = 1
    min_accuracy: Optional[float] = None
    category: Optional[str] = None
    skip_ready_check: bool = False


def validate_sql(sql: Optional[str], case: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    """校验 SQL 是否为只读查询，并按 golden 用例检查关键片段。"""
    if not sql:
        return None

    normalized = sql.strip()
    if not normalized.upper().startswith("SELECT"):
        return False

    fragments = (case or {}).get("expected_sql_contains") or []
    if not fragments:
        return True

    lowered = normalized.lower()
    return all(fragment.lower() in lowered for fragment in fragments)


def score_answer(answer: str, case: Dict[str, Any]) -> bool:
    """答案评分：支持 OR、AND、以及多组「每组至少命中一个」."""
    any_keywords = case.get("expected_answer_contains", [])
    all_keywords = case.get("expected_answer_contains_all", [])
    groups: List[List[str]] = case.get("expected_answer_groups", [])

    if not any_keywords and not all_keywords and not groups:
        return False

    any_ok = not any_keywords or any(keyword in answer for keyword in any_keywords)
    all_ok = not all_keywords or all(keyword in answer for keyword in all_keywords)
    groups_ok = all(
        any(keyword in answer for keyword in group)
        for group in groups
    )
    return any_ok and all_ok and groups_ok


def _git_commit() -> Optional[str]:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                timeout=2,
            )
            .strip()[:12]
        )
    except (subprocess.SubprocessError, OSError):
        return None


def _golden_digest(path: str) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digest[:12]


def build_run_metadata(config: BenchmarkConfig) -> Dict[str, Any]:
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "api_base": config.api_base,
        "golden_path": config.golden_path,
        "golden_sha256": _golden_digest(config.golden_path),
        "git_commit": _git_commit(),
        "model": settings.model_name,
        "embedding_model": settings.embedding_model,
        "workers": config.workers,
        "timeout_s": config.timeout,
        "retries": config.retries,
    }


def check_api_ready(config: BenchmarkConfig) -> None:
    if config.skip_ready_check:
        return

    ready_url = f"{config.api_base.rstrip('/')}/ready"
    try:
        response = requests.get(ready_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"API 未就绪 ({ready_url})，请先启动服务: python app.py\n{exc}"
        ) from exc

    if payload.get("status") != "ready":
        raise RuntimeError(f"API /ready 返回异常: {payload}")


def _base_result_fields(case: Dict[str, Any], case_index: int) -> Dict[str, Any]:
    return {
        "case_index": case_index,
        "question": case["question"],
        "category": case.get("category", "unknown"),
        "expected_tool": case.get("expected_tool"),
        "expected_keywords": case.get("expected_answer_contains", []),
        "expected_keywords_all": case.get("expected_answer_contains_all", []),
        "expected_answer_groups": case.get("expected_answer_groups", []),
    }


def _failed_result(
    case: Dict[str, Any],
    case_index: int,
    latency: float,
    error: str,
) -> Dict[str, Any]:
    expected_tool = case.get("expected_tool")
    return {
        **_base_result_fields(case, case_index),
        "actual_answer": "",
        "is_correct": False,
        "tool_used": None,
        "tools_used": [],
        "tool_match": False if expected_tool else None,
        "sql_generated": None,
        "sql_valid": None,
        "sql_required_miss": expected_tool == "sql_query",
        "latency": round(latency, 2),
        "tokens_used": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "answer_length": 0,
        "has_error": True,
        "error": error,
    }


def _request_chat(case: Dict[str, Any], config: BenchmarkConfig) -> requests.Response:
    chat_url = f"{config.api_base.rstrip('/')}/chat"
    last_error: Optional[Exception] = None

    for attempt in range(config.retries + 1):
        try:
            response = requests.post(
                chat_url,
                json={"query": case["question"]},
                timeout=config.timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < config.retries:
                time.sleep(1)
                continue
            raise

        if response.status_code >= 500 and attempt < config.retries:
            time.sleep(1)
            continue
        return response

    if last_error:
        raise last_error
    raise RuntimeError("chat request failed without response")


def evaluate_single_case(
    case: Dict[str, Any],
    case_index: int,
    config: BenchmarkConfig,
) -> Dict[str, Any]:
    """测试单个用例。"""
    try:
        start_time = time.time()
        response = _request_chat(case, config)
        latency = time.time() - start_time
        has_error = response.status_code != 200

        if has_error:
            return _failed_result(
                case,
                case_index,
                latency,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
            )

        result = response.json()
        answer = result.get("answer", "")
        tools_used = list(result.get("tools_used") or [])
        tool_name = result.get("tool_name")
        if tool_name and tool_name not in tools_used:
            tools_used.append(tool_name)

        expected_tool = case.get("expected_tool")
        tool_match = expected_tool in tools_used if expected_tool else None
        sql_generated = result.get("sql_generated")
        sql_expected = expected_tool == "sql_query"
        sql_used = "sql_query" in tools_used
        usage = result.get("usage", {})

        return {
            **_base_result_fields(case, case_index),
            "actual_answer": answer[:200],
            "is_correct": score_answer(answer, case),
            "tool_used": tool_name,
            "tools_used": tools_used,
            "tool_match": tool_match,
            "sql_generated": sql_generated,
            "sql_valid": validate_sql(sql_generated, case),
            "sql_required_miss": sql_expected and not sql_used,
            "latency": round(latency, 2),
            "tokens_used": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "answer_length": len(answer),
            "has_error": has_error,
            "error": None,
        }
    except Exception as exc:
        return _failed_result(case, case_index, latency=0, error=str(exc))


def _filter_cases(cases: List[Dict[str, Any]], category: Optional[str]) -> List[Dict[str, Any]]:
    if not category:
        return cases
    return [case for case in cases if case.get("category") == category]


def _summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    correct = sum(1 for result in results if result.get("is_correct"))
    tool_cases = [result for result in results if result.get("expected_tool")]
    tool_correct = sum(1 for result in tool_cases if result.get("tool_match"))
    sql_cases = [result for result in results if result.get("sql_generated")]
    sql_valid_count = sum(1 for result in sql_cases if result.get("sql_valid") is True)
    sql_required_miss = sum(1 for result in results if result.get("sql_required_miss"))
    avg_latency = sum(result.get("latency", 0) for result in results) / total if total else 0

    category_stats: Dict[str, Dict[str, int]] = {}
    for result in results:
        category = result.get("category", "未知分类")
        if category not in category_stats:
            category_stats[category] = {"correct": 0, "total": 0, "tool_correct": 0}
        category_stats[category]["total"] += 1
        if result.get("is_correct"):
            category_stats[category]["correct"] += 1
        if result.get("tool_match"):
            category_stats[category]["tool_correct"] += 1

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total * 100, 1) if total else 0.0,
        "tool_correct": tool_correct,
        "tool_total": len(tool_cases),
        "tool_accuracy": round(tool_correct / len(tool_cases) * 100, 1) if tool_cases else 0.0,
        "average_latency": round(avg_latency, 2),
        "total_tokens": sum(result.get("tokens_used", 0) for result in results),
        "prompt_tokens": sum(result.get("prompt_tokens", 0) for result in results),
        "completion_tokens": sum(result.get("completion_tokens", 0) for result in results),
        "sql_valid_rate": f"{sql_valid_count}/{len(sql_cases)}" if sql_cases else "N/A",
        "sql_required_miss": sql_required_miss,
        "error_count": sum(1 for result in results if result.get("has_error")),
        "by_category": category_stats,
    }


def run_benchmark(config: BenchmarkConfig) -> Dict[str, Any]:
    """运行全部测试。"""
    check_api_ready(config)

    with open(config.golden_path, "r", encoding="utf-8") as file:
        cases = _filter_cases(json.load(file), config.category)

    if not cases:
        raise RuntimeError("没有可运行的 benchmark 用例")

    print(f"开始评测，共 {len(cases)} 个用例（workers={config.workers}）...")

    indexed_cases = list(enumerate(cases))
    results: List[Dict[str, Any]] = []

    if config.workers <= 1:
        for case_index, case in indexed_cases:
            results.append(evaluate_single_case(case, case_index, config))
    else:
        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = {
                executor.submit(evaluate_single_case, case, case_index, config): case_index
                for case_index, case in indexed_cases
            }
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda item: item.get("case_index", 0))
    summary = _summarize_results(results)
    report = {
        "run_metadata": build_run_metadata(config),
        "summary": summary,
        "details": results,
    }

    print("\n" + "=" * 50)
    print("评测报告")
    print("=" * 50)
    print(f"总用例数: {summary['total']}")
    print(f"答案正确: {summary['correct']}/{summary['total']} ({summary['accuracy']}%)")
    print(f"工具正确: {summary['tool_correct']}/{summary['tool_total']} ({summary['tool_accuracy']}%)")
    print(f"平均延迟: {summary['average_latency']}s")
    print(f"总 tokens: {summary['total_tokens']} (prompt {summary['prompt_tokens']} / completion {summary['completion_tokens']})")
    print(f"SQL 校验通过: {summary['sql_valid_rate']}")
    print(f"应使用 SQL 但未使用: {summary['sql_required_miss']}")
    print(f"请求失败: {summary['error_count']}")
    print("\n按类别统计:")
    for category, stats in summary["by_category"].items():
        answer_acc = stats["correct"] / stats["total"] * 100 if stats["total"] else 0
        tool_acc = stats["tool_correct"] / stats["total"] * 100 if stats["total"] else 0
        print(
            f"  - {category}: 答案 {answer_acc:.1f}% ({stats['correct']}/{stats['total']}), "
            f"工具 {tool_acc:.1f}% ({stats['tool_correct']}/{stats['total']})"
        )

    failed_cases = [result for result in results if not result.get("is_correct")]
    if failed_cases:
        print("\n未通过用例:")
        for result in failed_cases:
            flags = []
            if result.get("tool_match") is False:
                flags.append("tool")
            if result.get("sql_required_miss"):
                flags.append("sql_miss")
            if result.get("sql_valid") is False:
                flags.append("sql_invalid")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            print(f"  - #{result.get('case_index')}: {result.get('question')}{suffix}")

    result_path = Path(config.result_path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到 {config.result_path}")

    if config.min_accuracy is not None and summary["accuracy"] < config.min_accuracy:
        print(
            f"\n准确率 {summary['accuracy']}% 低于阈值 {config.min_accuracy}%",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return report


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(description="World Cup RAG benchmark runner")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--golden", default=DEFAULT_GOLDEN_PATH)
    parser.add_argument("--result", default=DEFAULT_RESULT_PATH)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--min-accuracy", type=float, default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--skip-ready-check", action="store_true")
    args = parser.parse_args()

    return BenchmarkConfig(
        api_base=args.api_base,
        golden_path=args.golden,
        result_path=args.result,
        timeout=args.timeout,
        workers=max(1, args.workers),
        retries=max(0, args.retries),
        min_accuracy=args.min_accuracy,
        category=args.category,
        skip_ready_check=args.skip_ready_check,
    )


if __name__ == "__main__":
    run_benchmark(parse_args())
