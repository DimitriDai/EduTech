# utils/path_utils.py
from pathlib import Path


def get_project_root() -> Path:
    """
    返回项目根目录 vocab_service
    统一口径：以本文件位置向上找
    """
    return Path(__file__).resolve().parents[1]


def ensure_run_out_dir(run_id: str) -> Path:
    """
    确保 storage/out/{run_id} 存在，并返回该目录 Path
    """
    root = get_project_root()
    out_dir = root / "storage" / "out" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir

def ensure_run_category_dir(run_id: str, category: str) -> Path:
    """
    确保 storage/out/{run_id}/{category} 存在，并返回该目录 Path
    """
    out_dir = ensure_run_out_dir(run_id)
    cat_dir = out_dir / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    return cat_dir

def build_run_output_path(run_id: str, filename: str) -> Path:
    """
    给定 run_id + 文件名，返回：
    storage/out/{run_id}/{filename}
    并保证目录存在
    """
    out_dir = ensure_run_out_dir(run_id)
    return (out_dir / filename).resolve()


def build_run_category_output_path(run_id: str, category: str, filename: str) -> Path:
    """
    给定 run_id + category + 文件名，返回：
    storage/out/{run_id}/{category}/{filename}
    并保证目录存在
    """
    cat_dir = ensure_run_category_dir(run_id, category)
    return (cat_dir / filename).resolve()