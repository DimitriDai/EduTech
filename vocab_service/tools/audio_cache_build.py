# tools/audio_cache_build.py
# -*- coding: utf-8 -*-
"""
批量为 global_cache.json / uploaded_vocab_cache.json 生成（或计划生成）音频缓存，并写回 entry 字段：
- audio_uk
- audio_us
- audio_provider
- audio_version

支持 dry-run：只计算、不生成、不写回。

用法示例（dry-run 50 条）：
python tools/audio_cache_build.py ^
  --global_cache "storage/global_cache.json" ^
  --uploaded_cache "storage/uploaded_vocab_cache.json" ^
  --audio_root "storage/audio_cache" ^
  --piper_bin "C:\\tts\\piper\\piper.exe" ^
  --uk_model "C:\\tts\\piper\\en_GB-alba-medium.onnx" ^
  --us_model "C:\\tts\\piper\\en_US-amy-medium.onnx" ^
  --dry_run --limit 50
"""

from __future__ import annotations

import sys
from pathlib import Path

# ensure project root is on sys.path (so "utils" can be imported)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.slug import normalize_word, safe_filename_from_word


# -------------------------
# File helpers
# -------------------------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    """
    原子写：写临时文件 -> fsync -> replace
    防止写一半崩溃导致 cache 损坏
    """
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_cmd(cmd: List[str], timeout: int = 180, input_text: Optional[str] = None) -> Tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        input=input_text,
        text=True if input_text is not None else False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        encoding="utf-8",
        errors="ignore",
    )
    return p.returncode, p.stdout, p.stderr


# -------------------------
# Piper
# -------------------------

@dataclass
class PiperConfig:
    piper_bin: Path
    model_path: Path  # .onnx


def synth_piper_to_wav(text: str, out_wav: Path, cfg: PiperConfig) -> None:
    """
    echo "hello" | piper.exe -m model.onnx -f out.wav
    """
    ensure_dir(out_wav.parent)
    cmd = [str(cfg.piper_bin), "-m", str(cfg.model_path), "-f", str(out_wav)]
    code, _, err = run_cmd(cmd, timeout=180, input_text=text)
    if code != 0:
        raise RuntimeError(f"Piper failed: {err.strip()}")


def wav_to_mp3(ffmpeg_bin: str, wav_path: Path, mp3_path: Path) -> None:
    ensure_dir(mp3_path.parent)
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(wav_path),
        "-codec:a", "libmp3lame",
        "-q:a", "4",
        str(mp3_path),
    ]
    code, _, err = run_cmd(cmd, timeout=240)
    if code != 0:
        raise RuntimeError(f"ffmpeg convert failed: {err.strip()}")


# -------------------------
# Cache scanning
# -------------------------

# 重点：兼容你的 Entry 字段（word_original / word_norm）
WORD_KEYS_CANDIDATES = [
    "word_norm",
    "word_original",
    "word",
    "english",
    "en",
    "英文单词",
    "English",
]


def extract_word_from_entry(obj: Dict[str, Any]) -> Optional[str]:
    for k in WORD_KEYS_CANDIDATES:
        v = obj.get(k)
        if isinstance(v, str):
            vv = normalize_word(v)
            if vv:
                return vv
    return None


def looks_like_entry(obj: Dict[str, Any]) -> bool:
    return extract_word_from_entry(obj) is not None


def find_entry_dicts(root: Any) -> List[Dict[str, Any]]:
    """
    遍历整个 JSON，找出“像 Entry 的 dict”，并返回这些 dict 的引用（可原地修改）。
    你的 cache 通常是 WordEntryGroup -> entries[] -> Entry dict，
    这个函数会把 Entry dict 找出来。
    """
    found: List[Dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            # 注意：WordEntryGroup 也有 word_norm，所以我们要求它“更像 Entry”
            # 这里用一个额外条件：entries 不存在 或不是 list（避免把 group 当 entry）
            if looks_like_entry(x) and not isinstance(x.get("entries"), list):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(root)
    return found


# -------------------------
# URL/path building
# -------------------------

def build_audio_ref(accent: str, slug: str, ext: str, url_mode: str, url_prefix: str) -> str:
    """
    url_mode:
      - static:   /static/audio/uk/xxx.mp3   （推荐，上线直接挂静态目录）
      - relative: audio_cache/uk/xxx.mp3     （纯相对路径）
    """
    accent = accent.lower()
    if url_mode == "static":
        prefix = url_prefix.rstrip("/")
        return f"{prefix}/{accent}/{slug}.{ext}"
    else:
        prefix = url_prefix.strip("/").rstrip("/")
        return f"{prefix}/{accent}/{slug}.{ext}"


@dataclass
class AccentJob:
    name: str            # "uk" / "us"
    cfg: PiperConfig
    out_dir: Path        # 实际文件落盘目录


def ensure_audio_files(
    word_text: str,
    slug: str,
    jobs: List[AccentJob],
    target_format: str,
    ffmpeg_bin: Optional[str],
    dry_run: bool
) -> Dict[str, Tuple[Path, str]]:
    """
    为一个词确保音频存在，返回 {accent: (实际文件路径, 实际扩展名)}
    - 如果 format=mp3 且没有 ffmpeg，会退化为 wav
    """
    result: Dict[str, Tuple[Path, str]] = {}
    for job in jobs:
        wav_path = job.out_dir / f"{slug}.wav"
        mp3_path = job.out_dir / f"{slug}.mp3"

        # 已存在：优先返回目标格式文件；否则返回已有 wav
        if target_format == "mp3":
            if mp3_path.exists():
                result[job.name] = (mp3_path, "mp3")
                continue
            if wav_path.exists() and not ffmpeg_bin:
                result[job.name] = (wav_path, "wav")
                continue
        else:  # wav
            if wav_path.exists():
                result[job.name] = (wav_path, "wav")
                continue

        if dry_run:
            # dry-run 只“计划”生成
            planned_ext = "mp3" if (target_format == "mp3" and ffmpeg_bin) else "wav"
            planned_path = mp3_path if planned_ext == "mp3" else wav_path
            result[job.name] = (planned_path, planned_ext)
            continue

        # 先合成 wav
        synth_piper_to_wav(word_text, wav_path, job.cfg)

        if target_format == "wav":
            result[job.name] = (wav_path, "wav")
        else:
            if ffmpeg_bin:
                wav_to_mp3(ffmpeg_bin, wav_path, mp3_path)
                try:
                    wav_path.unlink(missing_ok=True)
                except TypeError:
                    if wav_path.exists():
                        wav_path.unlink()
                result[job.name] = (mp3_path, "mp3")
            else:
                # 没有 ffmpeg 就只能用 wav
                result[job.name] = (wav_path, "wav")
    return result


# -------------------------
# Main
# -------------------------

def process_cache(
    cache_path: Path,
    jobs: List[AccentJob],
    audio_root: Path,
    url_mode: str,
    url_prefix: str,
    provider: str,
    version: str,
    target_format: str,
    dry_run: bool,
    limit: int,
    write_back: bool,
    repair_missing_files: bool,
    only_words_file: str = "",   # ✅ 新增
) -> None:
    data = load_json(cache_path)
    entry_dicts = find_entry_dicts(data)

    # ---- 新增：only_words_file 过滤（在 limit 之前执行）----
    only_set = None
    owf = (only_words_file or "").strip()

    if owf:
        p = Path(owf)
        if p.exists():
            raw = p.read_text(encoding="utf-8").strip()
            words = []
            if raw:
                # 支持 txt 每行一个词 / 或 json list
                if raw.startswith("["):
                    try:
                        words = json.loads(raw)
                    except Exception:
                        words = []
                else:
                    words = [x.strip() for x in raw.splitlines() if x.strip()]
            only_set = {normalize_word(str(x)).lower() for x in words if str(x).strip()}

    if only_set:
        filtered = []
        for e in entry_dicts:
            w = extract_word_from_entry(e)
            if not w:
                continue
            if normalize_word(w).lower() in only_set:
                filtered.append(e)
        entry_dicts = filtered
    # ---- 新增结束 ----

    total = len(entry_dicts)
    if limit > 0:
        entry_dicts = entry_dicts[:limit]

    ffmpeg_bin = which("ffmpeg") if target_format == "mp3" else None
    if target_format == "mp3" and not ffmpeg_bin:
        print("[WARN] ffmpeg not found on PATH. mp3 requested but will fall back to wav for new files.")

    # 去重：同一个词只计算/生成一次，然后复用 url 写回多个 entry
    computed: Dict[str, Dict[str, str]] = {}  # word_lower -> {"audio_uk": "...", "audio_us": "..."}
    updated = 0
    planned_or_generated = 0

    for idx, entry in enumerate(entry_dicts, 1):
        w = extract_word_from_entry(entry)
        if not w:
            continue
        w_key = normalize_word(w).lower()

        # 判断是否需要补
        want_uk = any(j.name == "uk" for j in jobs)
        want_us = any(j.name == "us" for j in jobs)
        has_uk = bool(entry.get("audio_uk"))
        has_us = bool(entry.get("audio_us"))
        if (not want_uk or has_uk) and (not want_us or has_us):
            # 默认：字段已有就跳过
            if not repair_missing_files:
                continue

            # repair 模式：字段有，但文件可能被你删了 -> 检查磁盘是否存在 mp3
            slug = safe_filename_from_word(w)

            def _exists(accent: str) -> bool:
                # 优先找 mp3（你现在已经装了 ffmpeg）
                mp3_path = (audio_root / accent / f"{slug}.mp3")
                if mp3_path.exists():
                    return True
                # 兼容旧 wav
                wav_path = (audio_root / accent / f"{slug}.wav")
                return wav_path.exists()

            ok_uk = (not want_uk) or _exists("uk")
            ok_us = (not want_us) or _exists("us")

            # 两个口音文件都在 -> 跳过
            if ok_uk and ok_us:
                continue

            # 否则继续往下走，触发 ensure_audio_files() 重新生成

        if w_key in computed:
            urls = computed[w_key]
        else:
            slug = safe_filename_from_word(w)

            # 生成/计划生成文件
            files = ensure_audio_files(
                word_text=w,
                slug=slug,
                jobs=jobs,
                target_format=target_format,
                ffmpeg_bin=ffmpeg_bin,
                dry_run=dry_run,
            )

            # 由“实际扩展名”计算 url
            urls = {}
            if want_uk and "uk" in files:
                _, ext = files["uk"]
                urls["audio_uk"] = build_audio_ref("uk", slug, ext, url_mode, url_prefix)
            if want_us and "us" in files:
                _, ext = files["us"]
                urls["audio_us"] = build_audio_ref("us", slug, ext, url_mode, url_prefix)

            computed[w_key] = urls
            planned_or_generated += 1

        changed = False
        if want_uk and urls.get("audio_uk"):
            if (not entry.get("audio_uk")) or repair_missing_files:
                if entry.get("audio_uk") != urls["audio_uk"]:
                    entry["audio_uk"] = urls["audio_uk"]
                    changed = True

        if want_us and urls.get("audio_us"):
            if (not entry.get("audio_us")) or repair_missing_files:
                if entry.get("audio_us") != urls["audio_us"]:
                    entry["audio_us"] = urls["audio_us"]
                    changed = True

        if changed:
            entry["audio_provider"] = provider
            entry["audio_version"] = version
            updated += 1

        if idx % 200 == 0:
            print(f"[INFO] {cache_path.name}: processed {idx}/{len(entry_dicts)} (scanned_total={total})")

    if write_back and not dry_run and updated > 0:
        atomic_write_json(cache_path, data)

    print(f"[DONE] {cache_path.name}: scanned_total={total}, considered={len(entry_dicts)}, updated={updated}, planned_or_generated_words={planned_or_generated}")


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--global_cache", required=True, help="path to global_cache.json")
    ap.add_argument("--uploaded_cache", default=None, help="path to uploaded_vocab_cache.json (optional)")
    ap.add_argument("--write_uploaded", action="store_true", help="also write back to uploaded cache")

    ap.add_argument("--audio_root", required=True, help="audio storage root dir, e.g. storage/audio_cache")
    ap.add_argument("--format", choices=["mp3", "wav"], default="mp3")

    ap.add_argument("--piper_bin", required=True, help="path to piper.exe")
    ap.add_argument("--uk_model", default=None, help="path to UK model .onnx (optional)")
    ap.add_argument("--us_model", default=None, help="path to US model .onnx (optional)")

    ap.add_argument("--url_mode", choices=["static", "relative"], default="static",
                    help='static: "/static/audio/uk/x.mp3"; relative: "audio_cache/uk/x.mp3"')
    ap.add_argument("--url_prefix", default="/static/audio",
                    help='prefix for url_mode=static (default "/static/audio"), or base folder for relative')

    ap.add_argument("--provider", default="piper", help="audio_provider value")
    ap.add_argument("--version", default="piper_v1", help="audio_version value")

    ap.add_argument("--dry_run", action="store_true", help="do not generate or write, only plan")
    ap.add_argument("--repair_missing_files", action="store_true",
                help="if audio fields exist but audio files are missing, regenerate them")
    ap.add_argument("--limit", type=int, default=0, help="only process first N entries (0=all)")

    # 在 main() 的 parser.add_argument(...) 区域新增
    ap.add_argument(
        "--only_words_file",
        default="",
        help="Optional. Path to a txt/json file containing word_norm list. If set, only process those words."
    )

    args = ap.parse_args()

    global_path = Path(args.global_cache)
    if not global_path.exists():
        raise FileNotFoundError(f"global_cache not found: {global_path}")

    uploaded_path = Path(args.uploaded_cache) if args.uploaded_cache else None
    if uploaded_path and not uploaded_path.exists():
        raise FileNotFoundError(f"uploaded_cache not found: {uploaded_path}")

    piper_bin = Path(args.piper_bin)
    if not piper_bin.exists():
        raise FileNotFoundError(f"piper_bin not found: {piper_bin}")

    audio_root = Path(args.audio_root)
    ensure_dir(audio_root)

    jobs: List[AccentJob] = []
    if args.uk_model:
        jobs.append(AccentJob(
            name="uk",
            cfg=PiperConfig(piper_bin=piper_bin, model_path=Path(args.uk_model)),
            out_dir=audio_root / "uk"
        ))
    if args.us_model:
        jobs.append(AccentJob(
            name="us",
            cfg=PiperConfig(piper_bin=piper_bin, model_path=Path(args.us_model)),
            out_dir=audio_root / "us"
        ))
    if not jobs:
        raise ValueError("You must provide at least one of --uk_model or --us_model")

    # global cache：默认写回（dry-run 时不会写）
    process_cache(
        cache_path=global_path,
        jobs=jobs,
        audio_root=audio_root,
        url_mode=args.url_mode,
        url_prefix=args.url_prefix,
        provider=args.provider,
        version=args.version,
        target_format=args.format,
        dry_run=args.dry_run,
        limit=args.limit,
        write_back=True,
        repair_missing_files=args.repair_missing_files,
        only_words_file=args.only_words_file,
    )

    # uploaded cache：只有你显式 --write_uploaded 才写回
    if uploaded_path:
        process_cache(
            cache_path=uploaded_path,
            jobs=jobs,
            audio_root=audio_root,
            url_mode=args.url_mode,
            url_prefix=args.url_prefix,
            provider=args.provider,
            version=args.version,
            target_format=args.format,
            dry_run=args.dry_run,
            limit=args.limit,
            write_back=bool(args.write_uploaded),
            repair_missing_files=args.repair_missing_files,
            only_words_file=args.only_words_file,
        )
        if not args.write_uploaded:
            print("[INFO] uploaded_cache provided but not written (use --write_uploaded to enable).")

    print("[DONE] all finished.")


if __name__ == "__main__":
    main()