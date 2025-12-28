# speaking_service/modules/speaking/ocr_stage1.py
# ============================================================
# Stage 1 (保底稳定版 v3):
# - OCR 图片 -> DeepSeek 结构化 -> 修正标题(按 Part 块规则) -> 输出预填 txt
#
# 你现在的痛点：
# - 标题容易被 OCR UI 噪声污染：如 "P1 题 卡 Q" / "P2&P3 题卡 G"
# - 中文标题 OCR 逐字空格：如 "家 中 重 要 考 物 件"
# - 你不想再为“标题必须完全一致”付出巨大修改成本
#
# 本版本原则（最重要）：
# 1) 不再让 DeepSeek 决定标题；DeepSeek 只负责“结构化 Part1/2/3”
# 2) 标题 = 每个 Part 行上方最近的非空、非噪声行（这就是你说的“Part X 上面那一行”）
# 3) Part 1 标题强制英文：如果不是英文，用 OCR 中提取的英文 topic（Friends）兜底
# 4) 强过滤 UI 噪声，并解决“题 卡”拆字、“<”符号等问题
# ============================================================

import os
import re
import random
import requests
from typing import List, Dict, Optional, Tuple
from PIL import Image
import pytesseract

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ============================================================
# DeepSeek 结构化 Prompt（只管结构，不要它“发挥标题”）
# ============================================================
STRUCT_PROMPT = r"""
你是一位 IELTS Speaking 题卡整理助手。下面给你一段 OCR 文本（可能包含中文+英文）。

要求：
1) 只输出题卡内容的“结构化版本”，不要输出任何解释。
2) Part 标题必须是：Part 1 / Part 2 / Part 3（固定格式）。
3) 保留原问题含义；允许轻微清理 OCR 噪声（多余空格/乱码），但不要改写题目内容。
4) 第一行可以输出一个简短标题（如果不确定也没关系），但后续我会用程序规则修正标题。

输出格式示例：

Friends
Part 1
1. ...
2. ...

家中重要老物件
Part 2
Describe...
You should say:
- ...
Part 3
1. ...
2. ...
""".strip()


# ============================================================
# 基础清洗（少动为宜）
# ============================================================
def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)   # 去 markdown 粗体
    text = re.sub(r"-{2,}", "", text)              # 去分割线
    text = re.sub(r"\n{3,}", "\n\n", text)         # 压缩空行
    text = "\n".join([ln.rstrip() for ln in text.splitlines()])
    return text.strip()


# ============================================================
# DeepSeek 调用
# ============================================================
def call_deepseek(api_key: str, prompt_text: str, timeout: int = 40) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.2,
    }
    res = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=timeout)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"].strip()


# ============================================================
# OCR 行归一化：用于匹配/过滤（解决“题 卡”拆字、“<”符号等）
# ============================================================
def normalize_ocr_line_for_match(s: str) -> str:
    """
    用于【匹配/过滤】的归一化版本：
    - 去掉空白
    - 去掉常见括号、尖括号等符号
    例如："< P1 题 卡 Q" -> "P1题卡Q"
    """
    s = (s or "").strip()
    s = re.sub(r"[<>\[\]{}（）()【】]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def normalize_cn_spacing(s: str) -> str:
    """
    用于【中文标题输出】的去空格：
    把 '家 中 重 要 考 物 件' -> '家中重要考物件'
    只去“中文字符之间”的空格，不破坏英文空格。
    """
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", (s or "").strip())


# ============================================================
# 噪声过滤（标题相关要严格）
# ============================================================
def is_noise_line(line: str) -> bool:
    """
    过滤 UI/水印/按钮等噪声行。
    注意：用归一化后的文本匹配，解决“题 卡”拆字。
    """
    s = (line or "").strip()
    if not s:
        return True

    s2 = normalize_ocr_line_for_match(s)

    # UI/按钮/水印关键词（你截图界面常见）
    if re.search(r"(题卡|APP|同步更新|参考答案|立即练习|上一题|下一题|我要补充)", s2, re.I):
        return True

    # 题卡页类型（P1 / P2&P3）
    if re.match(r"^P\d", s2, re.I):
        return True
    if re.match(r"^P\d&P\d", s2, re.I):
        return True

    # 时间/电量
    if re.match(r"^\d{1,2}:\d{2}$", s2):
        return True
    if re.match(r"^\d{1,3}%$", s2):
        return True

    # 单字母（Q/G）
    if re.match(r"^[A-Za-z]$", s2):
        return True

    # 太短
    if len(s2) <= 1:
        return True

    return False


# ============================================================
# 从 OCR 原文里提取一个英文 topic 兜底（给 Part 1 强制英文用）
# ============================================================
def extract_en_topic_fallback(ocr_text: str) -> str:
    """
    找类似 Friends / Public transport 这种英文 topic。
    规则：短、不是问句、不是 Describe、不是 Part。
    """
    lines = [ln.strip() for ln in (ocr_text or "").splitlines() if ln.strip()]
    for ln in lines:
        if is_noise_line(ln):
            continue
        if re.search(r"[\u4e00-\u9fff]", ln):  # 含中文就跳过
            continue
        if re.match(r"^(Part\s*[123]|You should say)", ln, re.I):
            continue
        if re.match(r"^(Describe|Do|What|Why|Where|How)\b", ln, re.I):
            continue
        if 2 <= len(ln) <= 30 and re.search(r"[A-Za-z]", ln):
            return ln.strip()
    return ""


# ============================================================
# 核心：按 Part 块规则修标题
# ============================================================
def fix_titles_by_part_blocks(structured_text: str, en_topic_fallback: str = "") -> str:
    """
    规则：
    - 对每个 Part 行：标题 = Part 行上方最近的“非空且非噪声行”
    - Part 1 额外规则：标题必须英文；否则用 en_topic_fallback 兜底
    """
    text = normalize_text(structured_text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    if not lines:
        return text

    part_pat = re.compile(r"^Part\s*[123]\b", re.I)

    # 判断英文标题：不含中文且包含英文字母
    def is_english_title(s: str) -> bool:
        s = (s or "").strip()
        if re.search(r"[\u4e00-\u9fff]", s):
            return False
        return bool(re.search(r"[A-Za-z]", s))

    # 找所有 Part 行索引
    part_idxs = [i for i, ln in enumerate(lines) if part_pat.match(ln.strip())]
    if not part_idxs:
        return text

    for idx in part_idxs:
        # 往上找标题行
        j = idx - 1
        title = ""
        while j >= 0:
            cand = lines[j].strip()
            if not cand:
                j -= 1
                continue

            # 如果这行是噪声（例如 P1题卡Q），继续往上找
            if is_noise_line(cand):
                j -= 1
                continue

            title = cand
            break

        if not title:
            continue

        # 中文逐字空格清理
        title = normalize_cn_spacing(title)

        # Part 1 强制英文标题
        if lines[idx].strip().lower().startswith("part 1"):
            if not is_english_title(title) and en_topic_fallback:
                title = en_topic_fallback

        # 把 Part 行上一行“强制替换”为 title
        # （这正是你要求的：Part X 上面那一行就是标题）
        if idx - 1 >= 0:
            lines[idx - 1] = title
        else:
            # 理论上不会发生
            lines.insert(0, title)

    return "\n".join(lines).strip()


# ============================================================
# 列出图片文件：按修改时间排序（你的规则）
# ============================================================
def list_images_sorted(image_dir: str, prefix: str) -> List[str]:
    exts = (".png", ".jpg", ".jpeg")
    files = []
    for f in os.listdir(image_dir):
        if f.lower().endswith(exts) and f.startswith(prefix):
            files.append(f)
    files.sort(key=lambda x: os.path.getmtime(os.path.join(image_dir, x)))
    return files


# ============================================================
# 主函数：OCR -> 结构化 -> 修标题 -> 输出
# ============================================================
def stage1_ocr_to_prefill(
    api_key: str,
    image_dir: str,
    output_dir: str,
    prefix: str = "雅思话题",
    tesseract_cmd: Optional[str] = None,
    tessdata_prefix: Optional[str] = None,
) -> Dict:
    os.makedirs(output_dir, exist_ok=True)

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix

    images = list_images_sorted(image_dir, prefix)
    if not images:
        return {"ok": False, "message": f"No images found in {image_dir} with prefix={prefix}"}

    recognized_txt_files: List[str] = []
    failed: List[Dict] = []

    for filename in images:
        image_path = os.path.join(image_dir, filename)
        try:
            img = Image.open(image_path)

            # OCR：优先中英混合，失败就退回英文（你机器若有 chi_sim 则会用到）
            try:
                ocr_text = pytesseract.image_to_string(img, lang="eng+chi_sim")
            except Exception:
                ocr_text = pytesseract.image_to_string(img, lang="eng")

            # 提取 Part 1 的英文标题兜底（Friends）
            en_topic_fallback = extract_en_topic_fallback(ocr_text)

            # DeepSeek 结构化（只要结构）
            prompt = STRUCT_PROMPT + "\n\n" + ocr_text
            result = call_deepseek(api_key, prompt)

            cleaned = normalize_text(result)

            # 关键：按 Part 块规则强制修正“Part 上面那行”标题
            cleaned = fix_titles_by_part_blocks(cleaned, en_topic_fallback=en_topic_fallback)

            # 写出单张识别结果
            out_name = os.path.splitext(filename)[0] + "-已识别.txt"
            out_path = os.path.join(image_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(cleaned)

            recognized_txt_files.append(out_path)

        except Exception as e:
            failed.append({"file": filename, "error": str(e)})

    # 合并预填（按文件名排序合并，便于复现）
    merged_text = ""
    for p in sorted(recognized_txt_files):
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            merged_text += content + "\n\n"

    if not merged_text.strip():
        return {"ok": False, "message": "All recognized files were empty", "failed": failed}

    rand4 = f"{random.randint(1000, 9999)}"
    merged_filename = f"雅思_口语话题_预填_{rand4}.txt"
    merged_path = os.path.join(output_dir, merged_filename)
    with open(merged_path, "w", encoding="utf-8") as f:
        f.write(merged_text.strip())

    return {
        "ok": True,
        "image_dir": image_dir,
        "output_dir": output_dir,
        "prefix": prefix,
        "recognized_count": len(recognized_txt_files),
        "failed": failed,
        "prefill_txt": merged_path,
        "recognized_txt_files": recognized_txt_files,
    }