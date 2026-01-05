# speaking_service/modules/speaking/ocr_stage1.py
# ------------------------------------------------------------
# Stage 1: Image OCR -> (optional) DeepSeek struct -> prefill txt
#
# 你要求的“兜底规则”在：apply_topic_fallback()
# - 若 DeepSeek 输出里标题缺失 / 错乱（例如变成 P1题卡 / 题卡字母 / 直接从 Part 1 开始）
#   就从 OCR 原文里用“Part X 上一行 = Topic”补回。
# - 若 OCR 也没抓到 Topic（极少数，比如标题那行完全没识别出来），就用 cue 句(Describe...)作为 topic 兜底。
#
# 另外加了：
# - fix_spaced_cjk(): 把“家 中 重 要 老 物 件”这种拆字，修成“家中重要老物件”
# - chi_sim 可用性探测：如果没装，就自动降级到 eng（避免直接报错）
# ------------------------------------------------------------

import os
import re
import json
import time
import random
from typing import List, Dict, Optional, Tuple

import requests
from PIL import Image
import pytesseract


DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 你现有项目里应该已经有自己的 STRUCT_PROMPT（不要太大改动）
# 这里给一个“稳态”版本：要求输出严格结构，便于后续 parser。
STRUCT_PROMPT = r"""
You are given OCR text from an IELTS Speaking topic card screenshot.
Please rewrite it into a clean prefill text with this format:

(1) The first non-empty line MUST be the TOPIC title (exactly as shown on the card, English or Chinese).
(2) Then output sections in order if present:

TOPIC_LINE
Part 1
1. ...
2. ...

TOPIC_LINE
Part 2
Describe ...
You should say:
- ...
- ...
- ...
- ...

Part 3
1. ...
2. ...

Rules:
- Keep original wording (do NOT paraphrase).
- Keep numbering for Part 1 and Part 3 questions.
- For Part 2 bullets, use plain lines or "- " lines; keep the text intact.
- Do not add any extra commentary.
"""


# -----------------------------
# Helpers
# -----------------------------

def list_images_sorted(image_dir: str, prefix: str) -> List[str]:
    """List images startswith prefix, sort by filename.
    Fallback: if no files matched the prefix, use all images.
    """
    exts = (".png", ".jpg", ".jpeg", ".webp")
    files = []

    for fn in os.listdir(image_dir):
        if fn.lower().endswith(exts) and fn.startswith(prefix):
            files.append(fn)

    # 🔁 fallback：prefix 不匹配时，使用全部图片
    if not files:
        for fn in os.listdir(image_dir):
            if fn.lower().endswith(exts):
                files.append(fn)

    files.sort()
    return files

def call_deepseek(api_key: str, prompt: str, temperature: float = 0.1, max_tokens: int = 2000) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a precise IELTS speaking data formatter."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(DEEPSEEK_API_URL, headers=headers, data=json.dumps(payload), timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def normalize_text(s: str) -> str:
    """Basic cleanup. 注意：不在这里强行去掉标题行。"""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # 去掉多余空行
    s = re.sub(r"\n{3,}", "\n\n", s).strip() + "\n"
    return s


def fix_spaced_cjk(s: str) -> str:
    """
    修复 OCR 常见问题：中文每个字被空格拆开
    例：'家 中 重 要 老 物 件' -> '家中重要老物件'
    只在连续 CJK 字符间做合并，不影响英文空格。
    """
    # 把 “中 文” 这种变成 “中文”
    # 原理：CJK + space + CJK => CJKCJK
    return re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", s)


def detect_tesseract_lang() -> str:
    """
    优先 eng+chi_sim。
    如果用户电脑里没有 chi_sim，就退化为 eng，避免直接报错。
    """
    try:
        langs = pytesseract.get_languages(config="")
        if "chi_sim" in langs:
            return "eng+chi_sim"
        return "eng"
    except Exception:
        # 某些环境 get_languages 会失败，也直接用 eng
        return "eng"


def extract_topic_from_ocr(ocr_text: str) -> Optional[str]:
    """
    从 OCR 原始文本中，按“Part X 上一行 = Topic”提取。
    - Part 1 卡：标题通常是英文（Friends / Advertisement / ...）
    - P2&P3 卡：标题通常是中文（家中重要老物件 / ...）
    """
    raw = normalize_text(ocr_text)
    raw = fix_spaced_cjk(raw)
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]

    if not lines:
        return None

    # 找 Part 1 / Part 2 的位置
    part_idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^Part\s*1\b", ln, re.I) or re.match(r"^Part\s*2\b", ln, re.I):
            part_idx = i
            break

    if part_idx is None:
        return None

    # 往上找第一个“像标题”的行
    for j in range(part_idx - 1, -1, -1):
        cand = lines[j]
        # 排除明显不是标题的东西
        if "题卡" in cand:
            continue
        if re.search(r"\b(202\d|20\d{2})\b", cand):
            continue
        if cand.lower().startswith(("p1", "p2", "p3")):
            continue
        if len(cand) > 80:
            continue
        return cand

    return None


def looks_like_bad_topic(first_line: str) -> bool:
    """
    DeepSeek 输出第一行如果是这些，就认为“标题缺失/错乱”：
    - 直接是 Part 1/Part 2
    - 含“题卡”
    - 单独一个字母/很短噪声
    - 以 Describe 开头（通常说明把 cue 当标题了；可以接受但我们仍尝试用 OCR 题目行替换）
    """
    ln = first_line.strip()
    if not ln:
        return True
    if re.match(r"^Part\s*[123]\b", ln, re.I):
        return True
    if "题卡" in ln:
        return True
    if re.match(r"^[A-Za-z]$", ln):
        return True
    if ln.lower().startswith("describe "):
        return True
    return False


def apply_topic_fallback(ds_text: str, ocr_text: str) -> str:
    """
    兜底规则：
    - 如果 ds_text 第一行不是一个好标题 => 用 OCR 的 topic 补回
    - 如果 OCR 也补不回 => 用 cue(Describe...)作为兜底标题
    """
    ds_text = normalize_text(ds_text)
    ds_text = fix_spaced_cjk(ds_text)

    ds_lines = [ln.rstrip() for ln in ds_text.split("\n")]
    # 找第一个非空行
    first_non_empty_idx = None
    for i, ln in enumerate(ds_lines):
        if ln.strip():
            first_non_empty_idx = i
            break

    if first_non_empty_idx is None:
        # ds 空了，直接用 OCR
        topic = extract_topic_from_ocr(ocr_text) or "Unknown Topic"
        return topic + "\n" + normalize_text(fix_spaced_cjk(ocr_text))

    first_line = ds_lines[first_non_empty_idx].strip()

    if not looks_like_bad_topic(first_line):
        # 已经像一个正常标题了，不动
        return "\n".join(ds_lines).strip() + "\n"

    # 用 OCR 提取标题
    topic = extract_topic_from_ocr(ocr_text)

    # OCR 没抓到，就用 cue 句兜底（从 ds_text 或 ocr_text 中找 Describe...）
    if not topic:
        m = re.search(r"^(Describe .*?)\s*$", normalize_text(fix_spaced_cjk(ocr_text)), re.M | re.I)
        if m:
            topic = m.group(1).strip()
        else:
            m2 = re.search(r"^(Describe .*?)\s*$", ds_text, re.M | re.I)
            topic = m2.group(1).strip() if m2 else "Unknown Topic"

    # 替换第一行（第一个非空行）
    ds_lines[first_non_empty_idx] = topic
    return "\n".join(ds_lines).strip() + "\n"

# -----------------------------
# DeepSeek fix for TOPIC title
TOPIC_FIX_PROMPT = r"""
You are fixing the TOPIC title of an IELTS Speaking topic card prefill text.

Input is the cleaned prefill text for ONE card.

Your job:
1) Ensure the FIRST non-empty line is the TOPIC title (exactly the card title if it exists; otherwise create a short natural title).
2) If the first line starts with "Describe ..." or is a bullet like "Which area/subject it is", it is NOT a topic title. Replace it.
3) Prefer a Chinese topic title if the card clearly has one, which is mostly likely a Part 2&3 card (e.g., "感兴趣的科学学科/领域"). If not, use a concise English title (e.g., "Science").
4) Remove broken/truncated topic lines like "Describe ... (biology," and replace with a complete title.
5) Remove extra blank lines (at most ONE blank line between blocks).

Output ONLY the corrected prefill text, nothing else.
"""

def call_deepseek_fix_topic(api_key: str, prefill_text: str) -> str:
    prompt = TOPIC_FIX_PROMPT.strip() + "\n\n---INPUT---\n" + prefill_text.strip()
    fixed = call_deepseek(api_key, prompt, temperature=0.0, max_tokens=1200)
    return normalize_text(fixed)


# -----------------------------
# Main entry used by API layer
# -----------------------------

def run_stage1_ocr(
    api_key: str,
    run_dir: str,
    prefix: str,
    image_dir: str,
    tesseract_cmd: Optional[str] = None,
    tessdata_prefix: Optional[str] = None,
    output_dir: Optional[str] = None,   # ✅ 新增这一行
) -> Dict:
    """
    输入：
      - run_dir: runs/<run_id>
      - image_dir: runs/<run_id>/img
      - prefix: 例如 '雅思话题'
    输出：
      - recognized txt files (per image)
      - 预填 prefill txt
    """
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix

    images = list_images_sorted(image_dir, prefix)
    if not images:
        return {"ok": False, "message": f"No images found in {image_dir} with prefix={prefix}"}

    lang = detect_tesseract_lang()

    recognized_txt_files: List[str] = []
    failed: List[Dict] = []

    # per image -> recognized file in img/
    for idx, filename in enumerate(images, start=1):
        image_path = os.path.join(image_dir, filename)
        try:
            img = Image.open(image_path)
            ocr_text = pytesseract.image_to_string(img, lang=lang)

            prompt = STRUCT_PROMPT.strip() + "\n\n" + ocr_text.strip()
            ds = call_deepseek(api_key, prompt)

            cleaned = normalize_text(ds)
            cleaned = apply_topic_fallback(cleaned, ocr_text)  # ✅ 你要的兜底规则在这里

            # ✅ 交给 DeepSeek 做标题兜底 + 清空行（你要的“自行修改”就在这里）
            cleaned = call_deepseek_fix_topic(api_key, cleaned)

            out_name = os.path.splitext(filename)[0] + "-已识别.txt"
            out_path = os.path.join(image_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(cleaned)

            recognized_txt_files.append(out_path)

            # 小延迟，避免 API 过快
            time.sleep(0.15)

        except Exception as e:
            failed.append({"file": filename, "error": str(e)})

    # 汇总 prefill
    # ✅ 兼容 app.py 传入 output_dir：
    # - 传了就用传入的
    # - 没传就默认 runs/<run_id>/prefill
    out_dir = output_dir if output_dir else os.path.join(run_dir, "prefill")
    os.makedirs(out_dir, exist_ok=True)

    rand4 = random.randint(1000, 9999)
    prefill_name = f"雅思_口语话题_预填_{rand4}.txt"  # 你现在的命名习惯
    prefill_path = os.path.join(out_dir, prefill_name)

    # 预填：按图片顺序拼接，每张之间空两行（方便人看，也方便后续 parser）
    merged = []
    for p in recognized_txt_files:
        with open(p, "r", encoding="utf-8") as f:
            merged.append(f.read().strip())
    merged_text = "\n\n".join(merged).strip() + "\n"

    with open(prefill_path, "w", encoding="utf-8") as f:
        f.write(merged_text)

    return {
        "ok": True,
        "image_dir": image_dir,
        "output_dir": out_dir,
        "prefix": prefix,
        "recognized_count": len(recognized_txt_files),
        "failed": failed,
        "prefill_txt": prefill_path,
        "recognized_txt_files": recognized_txt_files,
        "lang_used": lang,
    }