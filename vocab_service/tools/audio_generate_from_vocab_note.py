import os
import sys
import json
import subprocess
from pathlib import Path

# ✅ 确保以脚本方式运行时也能 import 项目内模块（utils/generators 等）
ROOT = Path(__file__).resolve().parents[1]   # vocab_service/
sys.path.insert(0, str(ROOT))

import pandas as pd
from utils.slug import safe_filename_from_word
from generators.excel_generator import load_entries_from_vocab_excel
from core.file_lock import file_lock

def env(k: str) -> str:
    v = os.getenv(k, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {k}")
    return v


def run_piper(piper_bin, model, text, out_wav):
    cmd = [piper_bin, "-m", model, "-f", str(out_wav)]
    p = subprocess.run(
        cmd,
        input=text,
        text=True,
        capture_output=True
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout or "piper failed")


def wav_to_mp3(ffmpeg_bin, wav, mp3):
    cmd = [ffmpeg_bin, "-y", "-i", str(wav), str(mp3)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not mp3.exists():
        raise RuntimeError(p.stderr or p.stdout or "ffmpeg failed")


def main():
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    vocab_excel = payload["vocab_excel"]
    sheet_name = payload.get("sheet_name", "vocab")
    accents = payload.get("accents", ["uk", "us"])
    force = payload.get("force", False)
    
    run_id = payload.get("run_id", "").strip()

    audio_root = Path(env("AUDIO_ROOT"))
    audio_root.mkdir(parents=True, exist_ok=True)

    piper_bin = env("PIPER_BIN")
    uk_model = env("PIPER_UK_MODEL")
    us_model = env("PIPER_US_MODEL")
    ffmpeg = os.getenv("FFMPEG_BIN", "").strip()

    model_map = {
        "uk": uk_model,
        "us": us_model,
    }

    data = load_entries_from_vocab_excel(vocab_excel, sheet_name)
    entries = data.get("entries", [])

    results = []
    ok = 0
    fail = 0

    for e in entries:
        if not isinstance(e, dict):
            continue
        word = (e.get("word_original") or "").strip()
        if not word:
            continue

        slug = safe_filename_from_word(word)

        row = {
            "word_original": word,
            "slug": slug,
            "uk": {},
            "us": {},
        }

        for acc in accents:
            model = model_map[acc]
            out_dir = audio_root / acc
            out_dir.mkdir(parents=True, exist_ok=True)

            wav = out_dir / f"{slug}.wav"
            mp3 = out_dir / f"{slug}.mp3"

            # 每个 (slug, acc) 一把锁
            lock_path = out_dir / f"{slug}.lock"

            # 快速复用（无需加锁）
            if not force and (mp3.exists() or wav.exists()):
                use = mp3 if mp3.exists() else wav
                row[acc] = {
                    "ok": True,
                    "format": use.suffix[1:],
                    "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                }
                ok += 1
                continue

            try:
                with file_lock(str(lock_path), timeout=120.0):
                    # 进入锁后必须二次检查
                    if not force and (mp3.exists() or wav.exists()):
                        use = mp3 if mp3.exists() else wav
                        row[acc] = {
                            "ok": True,
                            "format": use.suffix[1:],
                            "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                        }
                        ok += 1
                        continue

                    pid = os.getpid()
                    wav_tmp = out_dir / f"{slug}.tmp.{pid}.wav"
                    mp3_tmp = out_dir / f"{slug}.tmp.{pid}.mp3"

                    wav_tmp.unlink(missing_ok=True)
                    mp3_tmp.unlink(missing_ok=True)

                    # 1. 生成 wav 到临时文件
                    run_piper(piper_bin, model, word, wav_tmp)

                    # 2. 转 mp3（如果有 ffmpeg）
                    if ffmpeg:
                        try:
                            wav_to_mp3(ffmpeg, wav_tmp, mp3_tmp)
                            os.replace(mp3_tmp, mp3)
                            wav_tmp.unlink(missing_ok=True)
                            use = mp3
                        except Exception:
                            os.replace(wav_tmp, wav)
                            mp3_tmp.unlink(missing_ok=True)
                            use = wav
                    else:
                        os.replace(wav_tmp, wav)
                        use = wav

                row[acc] = {
                    "ok": True,
                    "format": use.suffix[1:],
                    "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                }
                ok += 1

            except Exception as ex:
                row[acc] = {"ok": False, "error": str(ex)}
                fail += 1

        results.append(row)
    # ===== 可选：按 run_id 落盘一份生成报告（用于排错/追踪） =====
    if run_id:
        report = {
            "run_id": run_id,
            "vocab_excel": vocab_excel,
            "sheet_name": sheet_name,
            "accents": accents,
            "total_words": len(results),
            "ok_words": ok,
            "failed_words": fail,
            "results": results,
        }
        report_path = Path("storage") / "tmp" / f"audio_gen_report__{run_id}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    print(json.dumps({
        "ok": True,
        "total_words": len(results),
        "ok_words": ok,
        "failed_words": fail,
        "results": results
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
