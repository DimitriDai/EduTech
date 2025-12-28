# vocab_service/tools/cleanup_out_runs.py
from __future__ import annotations

import sys
from pathlib import Path

# === 关键：把项目根目录加进 sys.path ===
ROOT = Path(__file__).resolve().parents[1]  # vocab_service/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 现在再 import 项目内模块
from utils.path_utils import get_project_root

import argparse
import shutil
import time
from typing import List, Tuple, Dict, Any


def _dir_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def _is_run_dir(p: Path) -> bool:
    # 只把 out 下的“目录”当作 run 目录；文件（如 shuffle_e2c_master.xlsx）永远不删
    return p.exists() and p.is_dir()


def _list_run_dirs(out_dir: Path) -> List[Path]:
    if not out_dir.exists():
        return []
    return [p for p in out_dir.iterdir() if _is_run_dir(p)]


def _should_skip_run_dir(run_dir: Path, min_age_sec: int) -> Tuple[bool, str]:
    """
    跳过条件：
    1) 太新（最近 min_age_sec 内修改过）——避免删到正在生成的 run
    2) 存在 .lock 或 .keep 文件 —— 手动保护
    """
    now = time.time()
    age = now - _dir_mtime(run_dir)

    if age < min_age_sec:
        return True, f"too_new(age={int(age)}s)"

    if (run_dir / ".lock").exists():
        return True, "locked(.lock)"

    if (run_dir / ".keep").exists():
        return True, "keep(.keep)"

    return False, ""


def _delete_run_cache_file(run_cache_dir: Path, run_id: str, dry_run: bool) -> Tuple[bool, str]:
    """
    尝试删除 storage/run_cache/{run_id}.json
    返回 (deleted, message)
    """
    cache_file = run_cache_dir / f"{run_id}.json"
    if not cache_file.exists():
        return False, "run_cache_missing"

    if dry_run:
        return True, "run_cache_deleted(dry-run)"

    try:
        cache_file.unlink()
        return True, "run_cache_deleted"
    except Exception as e:
        return False, f"run_cache_delete_failed: {e}"


def cleanup_runs_threshold(
    keep_last_n: int,
    threshold: int,
    min_age_minutes: int,
    dry_run: bool,
) -> Dict[str, Any]:
    """
    只有当 run 目录数量 > threshold 才执行清理。
    清理策略：保留最新 keep_last_n 个，其余（最旧）尝试删除；受 min_age / lock / keep 影响可能删不掉。
    """
    root = get_project_root()
    out_dir = root / "storage" / "out"
    run_cache_dir = root / "storage" / "run_cache"
    min_age_sec = int(max(0, min_age_minutes) * 60)

    run_dirs = _list_run_dirs(out_dir)
    # 新 -> 旧
    run_dirs_sorted = sorted(run_dirs, key=_dir_mtime, reverse=True)

    keep_last_n = max(0, keep_last_n)
    threshold = max(0, threshold)

    report: Dict[str, Any] = {
        "project_root": str(root),
        "out_dir": str(out_dir),
        "run_cache_dir": str(run_cache_dir),
        "keep_last_n": keep_last_n,
        "threshold": threshold,
        "min_age_minutes": min_age_minutes,
        "dry_run": dry_run,
        "total_run_dirs": len(run_dirs_sorted),
        "action": "noop",
        "kept": [],
        "planned_deletions": 0,
        "removed": [],
        "skipped": [],
        "run_cache_actions": [],
        "errors": [],
    }

    # 不超过阈值：不做任何删除
    if len(run_dirs_sorted) <= threshold:
        report["action"] = "noop(threshold_not_exceeded)"
        # 仍然把最新 keep_last_n 的名字记录一下，方便你观察
        for p in run_dirs_sorted[:keep_last_n]:
            report["kept"].append(p.name)
        return report

    report["action"] = "cleanup"

    # 保留最新 keep_last_n（通常 keep_last_n == threshold == 50）
    keep_set = set(p.resolve() for p in run_dirs_sorted[:keep_last_n])
    for p in run_dirs_sorted[:keep_last_n]:
        report["kept"].append(p.name)

    # 只删除“超出的数量”
    overflow = max(0, len(run_dirs_sorted) - keep_last_n)
    report["planned_deletions"] = overflow

    # 删除目标：从最旧开始（旧 -> 新）
    candidates_oldest_first = list(reversed(run_dirs_sorted[keep_last_n:]))

    deleted_count = 0
    for run_dir in candidates_oldest_first:
        if deleted_count >= overflow:
            break

        run_id = run_dir.name

        # 安全跳过条件
        skip, reason = _should_skip_run_dir(run_dir, min_age_sec=min_age_sec)
        if skip:
            report["skipped"].append({"run_id": run_id, "reason": reason})
            continue

        # 删除 out/{run_id}
        if dry_run:
            report["removed"].append({"run_id": run_id, "out_dir": "deleted(dry-run)"})
            deleted, msg = _delete_run_cache_file(run_cache_dir, run_id, dry_run=True)
            report["run_cache_actions"].append({"run_id": run_id, "action": msg})
            deleted_count += 1
            continue

        try:
            shutil.rmtree(run_dir, ignore_errors=False)
            report["removed"].append({"run_id": run_id, "out_dir": "deleted"})
        except Exception as e:
            report["errors"].append({"run_id": run_id, "where": "delete_out_dir", "error": str(e)})
            # out 删除失败：不动 run_cache，避免半可复现
            continue

        # 同步删 run_cache/{run_id}.json
        deleted, msg = _delete_run_cache_file(run_cache_dir, run_id, dry_run=False)
        report["run_cache_actions"].append({"run_id": run_id, "action": msg})
        if not deleted and msg.startswith("run_cache_delete_failed"):
            report["errors"].append({"run_id": run_id, "where": "delete_run_cache", "error": msg})

        deleted_count += 1

    return report


def _print_report(r: Dict[str, Any]) -> None:
    print("=" * 72)
    print("CLEANUP REPORT")
    for k in [
        "project_root",
        "out_dir",
        "run_cache_dir",
        "keep_last_n",
        "threshold",
        "min_age_minutes",
        "dry_run",
        "total_run_dirs",
        "action",
        "planned_deletions",
    ]:
        print(f"- {k}: {r.get(k)}")
    print(f"- removed: {len(r['removed'])}")
    print(f"- skipped: {len(r['skipped'])}")
    print(f"- errors:  {len(r['errors'])}")
    print("-" * 72)

    if r["removed"]:
        print("REMOVED:")
        for x in r["removed"][:50]:
            print("  ", x)
        if len(r["removed"]) > 50:
            print("  ...")

    if r["skipped"]:
        print("\nSKIPPED:")
        for x in r["skipped"][:50]:
            print("  ", x)
        if len(r["skipped"]) > 50:
            print("  ...")

    if r["run_cache_actions"]:
        print("\nRUN_CACHE ACTIONS:")
        for x in r["run_cache_actions"][:50]:
            print("  ", x)
        if len(r["run_cache_actions"]) > 50:
            print("  ...")

    if r["errors"]:
        print("\nERRORS:")
        for x in r["errors"][:50]:
            print("  ", x)
        if len(r["errors"]) > 50:
            print("  ...")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Cleanup storage/out run directories (threshold-based) and paired storage/run_cache/{run_id}.json"
    )
    parser.add_argument("--keep", type=int, default=50, help="保留最新的前 N 个 run 目录（默认 50）")
    parser.add_argument("--threshold", type=int, default=50, help="只有当 run 目录数量 > threshold 才清理（默认 50）")
    parser.add_argument("--min-age-minutes", type=int, default=60, help="跳过最近 N 分钟内修改过的 run（默认 60）")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不实际删除")
    args = parser.parse_args()

    report = cleanup_runs_threshold(
        keep_last_n=args.keep,
        threshold=args.threshold,
        min_age_minutes=args.min_age_minutes,
        dry_run=bool(args.dry_run),
    )
    _print_report(report)


if __name__ == "__main__":
    main()