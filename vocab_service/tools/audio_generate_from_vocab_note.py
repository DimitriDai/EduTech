import os
import sys
import json
import subprocess
from pathlib import Path

# ✅ 确保以脚本方式运行时也能 import 项目内模块（utils/generators 等）
ROOT = Path(__file__).resolve().parents[2]   # vocab_service/
sys.path.insert(0, str(ROOT))

import pandas as pd
from utils.slug import safe_filename_from_word
from generators.excel_generator import load_entries_from_vocab_excel
from core.file_lock import file_lock

# ===== COS (新增) =====
try:
    from qcloud_cos import CosConfig, CosS3Client
except Exception:
    CosConfig = None
    CosS3Client = None


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)

def env(k: str) -> str:
    v = os.getenv(k, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {k}")
    return v


def env_opt(k: str, default: str = "") -> str:
    return os.getenv(k, default).strip() or default


# ===== COS helpers (新增) =====
def cos_client_or_none():
    """
    最小改动策略：
    - 如果你没配 TC_SECRET_ID/KEY，则自动跳过 COS（保持原本地行为）
    - 配了就启用 COS 共享缓存
    """
    if CosConfig is None or CosS3Client is None:
        return None

    sid = os.getenv("TC_SECRET_ID", "").strip()
    sk = os.getenv("TC_SECRET_KEY", "").strip()
    region = os.getenv("COS_REGION", "").strip()
    bucket = os.getenv("COS_BUCKET", "").strip()

    if not (sid and sk and region and bucket):
        return None

    cfg = CosConfig(Region=region, SecretId=sid, SecretKey=sk)
    client = CosS3Client(cfg)
    return client


def cos_key_for_audio(prefix: str, acc: str, filename: str) -> str:
    # prefix 不要以 / 结尾
    p = prefix.strip().strip("/")
    return f"{p}/{acc}/{filename}"


def cos_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def cos_download_to_file(client, bucket: str, key: str, local_path: Path):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    # 直接保存到目标文件（你本地锁会保护同名并发）
    client.get_object_to_file(Bucket=bucket, Key=key, DestFilePath=str(local_path))


def cos_upload_file(client, bucket: str, key: str, local_path: Path):
    # put_object_from_local_file 在 COS 侧是原子覆盖（对象级）
    client.put_object_from_local_file(
        Bucket=bucket,
        LocalFilePath=str(local_path),
        Key=key
    )


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

log(f"[AUDIO][SCRIPT] {__file__}")
log("[AUDIO][SCRIPT] [COS ENABLED]")

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

    # ===== COS init (新增) =====
    cos_client = cos_client_or_none()
    cos_bucket = os.getenv("COS_BUCKET", "").strip()
    cos_prefix = env_opt("COS_AUDIO_PREFIX", "audio_cache")

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

            # ===== 0) 先本地快速复用（保持原逻辑） =====
            if not force and (mp3.exists() or wav.exists()):
                use = mp3 if mp3.exists() else wav
                row[acc] = {
                    "ok": True,
                    "format": use.suffix[1:],
                    "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                    "cache": "local_hit",
                }
                ok += 1
                continue

            # ===== 1) 如果本地没命中：先看 COS 是否已有，能下载就直接复用（新增） =====
            # 说明：为了让你现有 AUDIO_URL_PREFIX 继续可用，我们下载到本地目录
            # 选择优先 mp3（如果你没 ffmpeg，可能只有 wav）
            cos_key_mp3 = None
            cos_key_wav = None
            if cos_client and cos_bucket:
                cos_key_mp3 = cos_key_for_audio(cos_prefix, acc, mp3.name)
                cos_key_wav = cos_key_for_audio(cos_prefix, acc, wav.name)

                try:
                    _cos_hit = False  # 标记：本轮是否走过 COS 命中

                    # 打印本轮要检查的 key（非常关键，用于确认 key 是否一致）
                    log(f"[AUDIO][COS CHECK] acc={acc} mp3_key={cos_key_mp3}")
                    log(f"[AUDIO][COS CHECK] acc={acc} wav_key={cos_key_wav}")

                    # ✅ force 也允许 COS 命中
                    if cos_exists(cos_client, cos_bucket, cos_key_mp3):
                        log(f"[AUDIO][COS HIT] {acc}/{mp3.name} key={cos_key_mp3}")
                        with file_lock(str(lock_path), timeout=120.0):
                            if not mp3.exists():
                                cos_download_to_file(cos_client, cos_bucket, cos_key_mp3, mp3)
                            use = mp3

                        row[acc] = {
                            "ok": True,
                            "format": use.suffix[1:],
                            "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                            "cache": "cos_hit",
                            "cos_key": cos_key_mp3,
                        }
                        ok += 1
                        _cos_hit = True
                        # 这里已经完全满足需求：命中就 continue，绝不生成、绝不上传
                        continue

                    if cos_exists(cos_client, cos_bucket, cos_key_wav):
                        log(f"[AUDIO][COS HIT] {acc}/{wav.name} key={cos_key_wav}")
                        with file_lock(str(lock_path), timeout=120.0):
                            if not wav.exists():
                                cos_download_to_file(cos_client, cos_bucket, cos_key_wav, wav)
                            use = wav

                        row[acc] = {
                            "ok": True,
                            "format": use.suffix[1:],
                            "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                            "cache": "cos_hit",
                            "cos_key": cos_key_wav,
                        }
                        ok += 1
                        _cos_hit = True
                        continue

                except Exception as ex:
                    log(f"[AUDIO][COS ERROR] acc={acc} err={ex}")
                    # COS 异常不阻断生成：降级走本地生成（最小侵入）
                    pass

            # ===== 2) COS 没命中：进入原本的“锁内生成 + 原子落盘”，然后上传 COS（新增） =====
            try:
                with file_lock(str(lock_path), timeout=120.0):
                    # 进入锁后必须二次检查（仍保留）
                    if not force and (mp3.exists() or wav.exists()):
                        use = mp3 if mp3.exists() else wav
                        row[acc] = {
                            "ok": True,
                            "format": use.suffix[1:],
                            "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                            "cache": "local_hit_after_lock",
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

                    # ===== 3) 生成后上传 COS（新增，best-effort） =====
                    if cos_client and cos_bucket:
                        try:
                            key = cos_key_for_audio(cos_prefix, acc, use.name)

                            # 如果对象已经存在，就不要覆盖上传（避免 COS 修改时间刷新）
                            if cos_exists(cos_client, cos_bucket, key):
                                log(f"[AUDIO][COS SKIP UPLOAD] exists key={key}")
                                cos_key_used = key
                            else:
                                log(f"[AUDIO][COS UPLOAD] key={key} file={use.name}")
                                cos_upload_file(cos_client, cos_bucket, key, use)
                                cos_key_used = key
                        except Exception as ex:
                            log(f"[AUDIO][COS UPLOAD ERROR] acc={acc} err={ex}")
                            cos_key_used = None
                    else:
                        cos_key_used = None

                row[acc] = {
                    "ok": True,
                    "format": use.suffix[1:],
                    "url": f"{env('AUDIO_URL_PREFIX')}/{acc}/{use.name}",
                    "cache": "generated",
                    "cos_key": cos_key_used,
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