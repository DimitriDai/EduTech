# cache.py
# ============================================================
# 作用：question 级缓存（精确到每个 question）
# - key = hash(exam + band + style + part + topic + question_text + schema_version)
# - 命中缓存：直接复用，省钱省时间
# - 不命中：生成后写入缓存
# ============================================================

import time
import tempfile
from typing import Callable, Tuple, Any, Optional

import os
import json
import hashlib
from dataclasses import dataclass
from typing import Optional, Dict, Any
import time
from contextlib import contextmanager

SCHEMA_VERSION = "v1"  # 将来你改输出格式/规则时，改这个即可让缓存自动失效重建


@contextmanager
def file_lock(lock_path: str, retry: int = 50, sleep: float = 0.05):
    """
    简单跨请求 / 跨进程文件锁
    - Windows / Linux 通用
    - 用 .lock 文件占位
    """
    for _ in range(retry):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(sleep)
    else:
        raise RuntimeError(f"Cannot acquire lock: {lock_path}")

    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


@dataclass
class CacheItem:
    key: str
    payload: Dict[str, Any]


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_cache_key(
    exam: str,
    band: str,
    style: str,
    part: int,
    topic: str,
    question_text: str,
) -> str:
    obj = {
        "schema_version": SCHEMA_VERSION,
        "exam": exam,
        "band": band,
        "style": style,
        "part": int(part),
        "topic": topic.strip(),
        "question": question_text.strip(),
    }
    return _sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True))

import os
import json
import tempfile
from typing import Dict, Any


class JsonlCache:
    def __init__(self, cache_dir: str, filename: str = "answers_cache.jsonl"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.path = os.path.join(self.cache_dir, filename)
        self.lock_path = self.path + ".lock"

        self._loaded = False
        self._index: Dict[str, Dict[str, Any]] = {}

    # ---------- 基础功能（与你原本一致） ----------

    def _load(self) -> None:
        if self._loaded:
            return

        self._index = {}
        if not os.path.exists(self.path):
            self._loaded = True
            return

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                key = obj.get("key")
                payload = obj.get("payload")
                if key and isinstance(payload, dict):
                    # last-write-wins
                    self._index[key] = payload

        self._loaded = True

    def get(self, key: str):
        self._load()
        return self._index.get(key)

    def set(self, key: str, payload: Dict[str, Any]) -> None:
        self._load()

        # 所有“写文件”的操作，必须在同一把锁内
        with file_lock(self.lock_path):
            # 更新内存索引
            self._index[key] = payload

            # 追加写入 jsonl
            rec = {"key": key, "payload": payload}
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            # ---------- 自动控体量（方式 A 的核心） ----------
            max_keys = int(os.getenv("GLOBAL_CACHE_MAX_KEYS", "20000"))
            if max_keys > 0:
                self.compact_if_needed(max_keys=max_keys, factor=1.2)

    # ---------- compact 相关（新增） ----------

    def _count_lines_fast(self, max_probe: int = 2_000_000) -> int:
        if not os.path.exists(self.path):
            return 0
        n = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for _ in f:
                n += 1
                if n >= max_probe:
                    break
        return n

    def compact(self, max_keys: int = 20000) -> Dict[str, Any]:
        """
        规则：
        1) 每个 key 只保留最后一次写入（last-write-wins）
        2) 若 unique keys > max_keys，只保留最后 max_keys 个
        3) 原子写回（临时文件 -> replace）
        """
        if max_keys <= 0:
            return {"ok": True, "skipped": True, "reason": "max_keys<=0"}

        if not os.path.exists(self.path):
            return {"ok": True, "skipped": True, "reason": "cache_file_not_found"}

        total_lines = 0
        bad_lines = 0

        last_payload_by_key: Dict[str, Dict[str, Any]] = {}
        order = []

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    bad_lines += 1
                    continue

                key = obj.get("key")
                payload = obj.get("payload")
                if not key or not isinstance(payload, dict):
                    bad_lines += 1
                    continue

                last_payload_by_key[key] = payload
                order.append(key)

        # 去重 order，保留“最后出现顺序”
        uniq_rev = []
        seen = set()
        for k in reversed(order):
            if k not in seen:
                seen.add(k)
                uniq_rev.append(k)
        uniq_order = list(reversed(uniq_rev))

        # 超限裁剪
        if len(uniq_order) > max_keys:
            uniq_order = uniq_order[-max_keys:]

        # 原子写回
        fd, tmp_path = tempfile.mkstemp(
            prefix="answers_cache_", suffix=".jsonl", dir=self.cache_dir
        )
        os.close(fd)

        kept = 0
        with open(tmp_path, "w", encoding="utf-8") as out:
            for k in uniq_order:
                payload = last_payload_by_key.get(k)
                if payload is None:
                    continue
                rec = {"key": k, "payload": payload}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                kept += 1

        os.replace(tmp_path, self.path)

        # 重建内存索引，避免 get() 命中旧数据
        self._index = {k: last_payload_by_key[k] for k in uniq_order}
        self._loaded = True

        return {
            "ok": True,
            "total_lines_before": total_lines,
            "bad_lines_skipped": bad_lines,
            "unique_keys_kept": kept,
            "max_keys": max_keys,
        }

    def compact_if_needed(self, max_keys: int = 20000, factor: float = 1.2) -> Dict[str, Any]:
        threshold = int(max_keys * factor)
        n = self._count_lines_fast()
        if n <= threshold:
            return {"ok": True, "skipped": True, "lines": n, "threshold": threshold}
        return self.compact(max_keys=max_keys)

# 记得去powershell里设置环境变量：$env:GLOBAL_CACHE_MAX_KEYS="20000"