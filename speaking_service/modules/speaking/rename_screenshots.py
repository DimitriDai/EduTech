# rename_screenshots.py
# ============================================================
# 功能：按 run_id 自动定位截图目录 runs/<run_id>/img 并批量重命名
#
# 固定规则（按你的选择写死）：
# - 截图目录名固定：img
# - 重命名前缀固定：雅思话题
#
# 重命名规则：
# - 按最后修改时间排序（mtime）
# - 输出：雅思话题_0001.png / 雅思话题_0002.jpg ...
# - 保留扩展名（png/jpg/jpeg/webp）
# - 支持 --dry-run 预览
# - 不覆盖：若目标名已存在则直接报冲突退出（避免误覆盖）
#
# 用法：
#   cd ...\modules\speaking
#   python rename_screenshots.py --run_id 47da2fec --dry-run
#   python rename_screenshots.py --run_id 47da2fec
# ============================================================

import os
import argparse
from typing import List, Tuple

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# 固定配置（按你的选择写死）
FIXED_IMG_DIRNAME = "img"
FIXED_PREFIX = "雅思话题"


def _infer_runs_dir_from_this_file() -> str:
    """
    假设本文件在：
      speaking_service/modules/speaking/rename_screenshots.py
    那么 runs 目录是：
      speaking_service/runs
    """
    here = os.path.abspath(os.path.dirname(__file__))
    speaking_service_dir = os.path.abspath(os.path.join(here, "..", ".."))
    runs_dir = os.path.join(speaking_service_dir, "runs")
    return runs_dir


def list_images(image_dir: str) -> List[str]:
    files = []
    for name in os.listdir(image_dir):
        ext = os.path.splitext(name)[1].lower()
        if ext in IMG_EXTS:
            files.append(os.path.join(image_dir, name))
    return files


def sort_by_mtime(paths: List[str]) -> List[str]:
    return sorted(paths, key=lambda p: os.path.getmtime(p))


def build_new_name(prefix: str, idx: int, ext: str) -> str:
    return f"{prefix}_{idx:04d}{ext.lower()}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", required=True, help="runs/<run_id>/ 目录名，例如 47da2fec")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不实际改名")
    ap.add_argument("--start", type=int, default=1, help="起始序号（默认1）")
    ap.add_argument(
        "--runs_dir",
        default="",
        help="可选：手动指定 speaking_service/runs 路径；不填则自动推断",
    )
    args = ap.parse_args()

    runs_dir = args.runs_dir.strip() or _infer_runs_dir_from_this_file()
    run_dir = os.path.join(runs_dir, args.run_id)
    image_dir = os.path.join(run_dir, FIXED_IMG_DIRNAME)

    if not os.path.isdir(run_dir):
        raise RuntimeError(f"Run dir not found: {run_dir}")
    if not os.path.isdir(image_dir):
        raise RuntimeError(f"Image dir not found: {image_dir}")

    images = list_images(image_dir)
    if not images:
        print("[WARN] No images found in:", image_dir)
        return

    images = sort_by_mtime(images)

    # 先计算映射（旧->新）
    plan: List[Tuple[str, str]] = []
    idx = args.start
    for old_path in images:
        ext = os.path.splitext(old_path)[1]
        new_name = build_new_name(FIXED_PREFIX, idx, ext)
        new_path = os.path.join(image_dir, new_name)
        plan.append((old_path, new_path))
        idx += 1

    # 检查冲突（目标名已存在且不是同一个文件）
    conflicts = []
    for old_path, new_path in plan:
        if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
            conflicts.append((old_path, new_path))

    if conflicts:
        print("[ERROR] Target filename conflicts detected (will NOT overwrite):")
        for old_path, new_path in conflicts:
            print(" -", os.path.basename(old_path), "->", os.path.basename(new_path))
        print("\n解决办法：")
        print("1) 把 img 目录里已有的 '雅思话题_####.*' 文件先移走；或")
        print("2) 用 --start 改起始序号（例如 --start 101）。")
        return

    print("[INFO] runs_dir :", runs_dir)
    print("[INFO] run_dir  :", run_dir)
    print("[INFO] image_dir:", image_dir)
    print("\n[PLAN] Rename order (by mtime):")
    for old_path, new_path in plan:
        print(" -", os.path.basename(old_path), "->", os.path.basename(new_path))

    if args.dry_run:
        print("\n[DRY-RUN] No changes made.")
        return

    # 实际改名：为避免“互相覆盖”，先改成临时名，再改到最终名
    tmp_paths = []
    for i, (old_path, new_path) in enumerate(plan, start=1):
        tmp_path = old_path + f".__tmp__{i}"
        os.rename(old_path, tmp_path)
        tmp_paths.append((tmp_path, new_path))

    for tmp_path, new_path in tmp_paths:
        os.rename(tmp_path, new_path)

    print("\n[OK] Renamed successfully.")


# rename_screenshots.py

def rename_screenshots(img_dir: str) -> dict:
    """
    规范化 img 目录下的截图命名
    返回映射关系：old_name -> new_name
    """
    mapping = {}   # 👈 关键：必须先定义

    files = sorted(os.listdir(img_dir))
    index = 1

    for fname in files:
        if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        old_path = os.path.join(img_dir, fname)
        new_name = f"雅思话题_{index:04d}.jpg"
        new_path = os.path.join(img_dir, new_name)

        if fname != new_name:
            os.rename(old_path, new_path)
            mapping[fname] = new_name

        index += 1

    return mapping

