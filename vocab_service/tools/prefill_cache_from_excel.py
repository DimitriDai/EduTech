# -*- coding: utf-8 -*-
"""
Step 3: 解析“预上传词汇表 Excel” -> 预填全局缓存 global_cache.json

用法：
1) 修改下面 CONFIG 区域的 INPUT_EXCEL / OUTPUT_CACHE
2) 运行：
   python prefill_cache_from_excel.py

说明：
- 本脚本只负责把 Excel 解析成“全局缓存”的标准结构，不做 DeepSeek，不做 docx/excel 生成。
- 输出 JSON 中：每个 word 对应一个 entries 列表（允许同词多义/多来源行）。
"""

import os
import re
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd

# =======================
# CONFIG（你只需要改这里）
# =======================

# 批量：把要预上传的词表 Excel 全丢进这个目录
INPUT_DIR = r"C:\Users\24340\Desktop\EduTech\vocab_service\storage\uploaded_vocab"

# 是否递归扫描子文件夹
RECURSIVE = True

# 输出全局缓存
OUTPUT_CACHE = r"C:\Users\24340\Desktop\EduTech\vocab_service\storage\uploaded_vocab_cache.json"


# 如果你的 Excel 列名很奇怪，优先在这里“显式指定”
# None 表示自动猜列
FORCE_COLUMNS = {
    "word": None,           # 例如："英文单词"
    "phonetic_uk": None,    # 例如："英式音标"
    "phonetic_us": None,    # 例如："美式音标"
    "pos_cn": None,         # 例如："中文解释" / "释义"（包含词性 n./v./adj.）
    "definition_en": None,  # 例如："英文解释"
    "example": None,        # 例如："例句"
    "example_cn": None,     # 例如："例句翻译"
    "synonyms": None,       # 例如："同义替换"
}

# 允许的“列名别名”（自动猜列时用）
COLUMN_ALIASES = {
    "word": ["英文单词", "单词", "词汇", "word", "Word", "Expression", "短语", "词组", "词条"],
    "phonetic_uk": ["英式音标", "英音音标", "UK", "BrE", "phonetic_uk", "IPA(UK)", "British IPA"],
    "phonetic_us": ["美式音标", "美音音标", "US", "AmE", "phonetic_us", "IPA(US)", "American IPA"],
    "pos_cn": ["中文解释", "中文释义", "释义", "解释", "meaning", "Meaning", "中释", "词性+中文解释"],
    "definition_en": ["英文解释", "英文释义", "definition", "Definition", "English definition"],
    "example": ["例句", "例句(英)", "sentence", "Sentence", "Example", "example"],
    "example_cn": ["例句翻译", "例句(中)", "translation", "Translation", "例句译文"],
    "synonyms": ["同义替换", "同义词", "synonym", "Synonym", "Synonyms", "替换", "替换词"],
}

# 输出 JSON 是否压缩（False 更好读；True 更小）
MINIFY_JSON = False

# =======================
# 数据结构
# =======================

POS_TAGS = ["n", "v", "adj", "adv", "prep", "conj", "pron", "num", "det", "int", "phr", "aux", "modal"]

POS_REGEX = re.compile(r"\b(" + "|".join([re.escape(t) for t in POS_TAGS]) + r")\.\b", re.IGNORECASE)

MULTI_SPLIT = re.compile(r"[;；/／|｜]+")  # 用于拆 synonyms 等


@dataclass
class Entry:
    word_original: str
    word_norm: str
    phonetic_uk: str = ""
    phonetic_us: str = ""
    pos_cn: str = ""          # 原始“词性+中文解释”文本（不强行拆成 dict，后续 match_service 再精细化也行）
    definition_en: str = ""
    example: str = ""
    example_cn: str = ""
    synonyms: List[str] = None

    tokens: int = 1
    pos_count: int = 0
    source: str = "uploaded"
    meta: Dict[str, Any] = None  # 记录 sheet/row 等，方便追溯

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # dataclass 默认 None 也会输出，做点清理
        if d["synonyms"] is None:
            d["synonyms"] = []
        if d["meta"] is None:
            d["meta"] = {}
        return d


# =======================
# 工具函数
# =======================

def norm_word(s: str) -> str:
    """用于匹配的规范化 key：去首尾空格、合并内部多空格、全小写。
    注意：只用于 key，不改变输出的 word_original。
    """
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()

def calc_tokens(word_norm: str) -> int:
    """tokens：按空格拆分；连字符当作一个 token（你后续想改也容易）。"""
    if not word_norm:
        return 0
    return len([t for t in word_norm.split(" ") if t])

def calc_pos_count(pos_cn_text: str) -> int:
    """统计 pos 标记出现次数（如 'n.' 'v.' 'adj.'），用于你后续 tie-break。"""
    if not pos_cn_text:
        return 0
    hits = POS_REGEX.findall(pos_cn_text)
    return len(hits)

def split_synonyms(s: str) -> List[str]:
    if not s:
        return []
    s = str(s).strip()
    if not s:
        return []
    parts = [p.strip() for p in MULTI_SPLIT.split(s) if p.strip()]
    # 去重但保序
    seen = set()
    out = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

def pick_column(df: pd.DataFrame, forced: Optional[str], aliases: List[str]) -> Optional[str]:
    """返回 df 中实际存在的列名"""
    if forced:
        return forced if forced in df.columns else None

    # 精确匹配
    for a in aliases:
        if a in df.columns:
            return a

    # 模糊匹配（列名包含）
    lower_cols = {c.lower(): c for c in df.columns}
    for a in aliases:
        a_low = a.lower()
        for c_low, c_raw in lower_cols.items():
            if a_low in c_low:
                return c_raw
    return None


def infer_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    mapping = {}
    for key in ["word", "phonetic_uk", "phonetic_us", "pos_cn", "definition_en", "example", "example_cn", "synonyms"]:
        mapping[key] = pick_column(df, FORCE_COLUMNS.get(key), COLUMN_ALIASES.get(key, []))
    return mapping


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and pd.isna(x):
        return ""
    s = str(x).strip()
    return s


# =======================
# 核心逻辑
# =======================

def build_entries_from_sheet(df: pd.DataFrame, sheet_name: str) -> List[Entry]:
    col_map = infer_columns(df)

    if not col_map["word"]:
        print(f"[WARN] Sheet '{sheet_name}' 未找到 word 列，跳过。现有列：{list(df.columns)}")
        return []

    entries: List[Entry] = []
    for idx, row in df.iterrows():
        w = safe_str(row.get(col_map["word"]))
        if not w:
            continue

        w_norm = norm_word(w)
        e = Entry(
            word_original=w,
            word_norm=w_norm,
            phonetic_uk=safe_str(row.get(col_map["phonetic_uk"])) if col_map["phonetic_uk"] else "",
            phonetic_us=safe_str(row.get(col_map["phonetic_us"])) if col_map["phonetic_us"] else "",
            pos_cn=safe_str(row.get(col_map["pos_cn"])) if col_map["pos_cn"] else "",
            definition_en=safe_str(row.get(col_map["definition_en"])) if col_map["definition_en"] else "",
            example=safe_str(row.get(col_map["example"])) if col_map["example"] else "",
            example_cn=safe_str(row.get(col_map["example_cn"])) if col_map["example_cn"] else "",
            synonyms=split_synonyms(safe_str(row.get(col_map["synonyms"]))) if col_map["synonyms"] else [],
            tokens=calc_tokens(w_norm),
            pos_count=calc_pos_count(safe_str(row.get(col_map["pos_cn"])) if col_map["pos_cn"] else ""),
            source="uploaded",
            meta={"sheet": sheet_name, "row_index": int(idx)}
        )
        entries.append(e)

    return entries


def merge_into_cache(cache: Dict[str, Any], entries: List[Entry]) -> Tuple[int, int]:
    """把 entries 合并到 cache。返回 (新增word数, 新增entry行数)"""
    new_words = 0
    new_rows = 0

    for e in entries:
        key = e.word_norm
        if not key:
            continue

        if key not in cache:
            cache[key] = {
                "word_norm": key,
                "word_display": e.word_original,  # 默认展示用（首条为准）
                "entries": []
            }
            new_words += 1

        # 去重：同一个 sheet+row 视为唯一（避免重复读取）
        existing = cache[key]["entries"]
        sig = (e.meta.get("sheet"), e.meta.get("row_index"))
        already = False
        for ex in existing:
            ex_sig = (ex.get("meta", {}).get("sheet"), ex.get("meta", {}).get("row_index"))
            if ex_sig == sig:
                already = True
                break
        if already:
            continue

        existing.append(e.to_dict())
        new_rows += 1

    return new_words, new_rows

def load_cache(path: str) -> Dict[str, Any]:
    # 文件不存在：返回空缓存
    if not os.path.exists(path):
        return {}

    # 文件存在但为空：返回空缓存
    try:
        if os.path.getsize(path) == 0:
            print(f"[WARN] 缓存文件为空，将初始化为空缓存：{path}")
            return {}
    except OSError:
        # 获取大小失败也不阻塞
        pass

    # 文件存在但内容不是合法 JSON：返回空缓存（并备份坏文件）
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        bad_path = path + ".bad"
        try:
            os.replace(path, bad_path)  # 原子替换：把坏文件挪走
            print(f"[WARN] 缓存文件JSON损坏，已备份为：{bad_path}")
        except Exception:
            print(f"[WARN] 缓存文件JSON损坏，但备份失败（仍将初始化为空缓存）：{path}")
        return {}
    except Exception as e:
        print(f"[WARN] 读取缓存失败，将初始化为空缓存：{path}\n  -> {e}")
        return {}

def save_cache(path: str, cache: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if MINIFY_JSON:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(cache, f, ensure_ascii=False, indent=2)

def iter_excel_files(input_dir: str, recursive: bool = True) -> List[str]:
    exts = (".xlsx", ".xls", ".xlsm")
    paths = []
    if recursive:
        for root, _, files in os.walk(input_dir):
            for fn in files:
                if fn.lower().endswith(exts) and not fn.startswith("~$"):  # 跳过 Excel 临时文件
                    paths.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(input_dir):
            if fn.lower().endswith(exts) and not fn.startswith("~$"):
                paths.append(os.path.join(input_dir, fn))
    paths.sort()
    return paths
def main():
    if not os.path.isdir(INPUT_DIR):
        raise NotADirectoryError(f"找不到 INPUT_DIR：{INPUT_DIR}")

    excel_files = iter_excel_files(INPUT_DIR, recursive=RECURSIVE)
    if not excel_files:
        raise FileNotFoundError(f"在目录中未找到 Excel 文件：{INPUT_DIR}")

    print(f"[INFO] 批量读取目录: {INPUT_DIR}")
    print(f"[INFO] 找到 Excel 数量: {len(excel_files)}")

    cache = load_cache(OUTPUT_CACHE)
    before_words = len(cache)

    total_entries = 0
    total_new_words = 0
    total_new_rows = 0

    for excel_path in excel_files:
        print(f"\n[FILE] 读取 Excel: {excel_path}")
        try:
            xls = pd.ExcelFile(excel_path, engine="openpyxl")
        except Exception as e:
            print(f"[WARN] 打开失败，跳过：{excel_path}\n  -> {e}")
            continue

        file_entries = 0
        file_new_words = 0
        file_new_rows = 0

        for sheet in xls.sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=sheet, engine="openpyxl")
            except Exception as e:
                print(f"[WARN] 读取 Sheet 失败，跳过：{sheet}\n  -> {e}")
                continue

            df = df.dropna(axis=1, how="all")  # 去掉全空列

            # 这行沿用你原来的函数
            entries = build_entries_from_sheet(df, sheet_name=sheet)
            if not entries:
                continue

            # 给 meta 补充文件来源，方便追溯（强烈建议保留）
            for e in entries:
                if e.meta is None:
                    e.meta = {}
                e.meta["file"] = os.path.basename(excel_path)

            file_entries += len(entries)

            nw, nr = merge_into_cache(cache, entries)
            file_new_words += nw
            file_new_rows += nr

            print(f"[OK] Sheet '{sheet}': 解析 {len(entries)} 行，新增 word={nw}，新增 entry={nr}")

        total_entries += file_entries
        total_new_words += file_new_words
        total_new_rows += file_new_rows

        print(f"[FILE SUMMARY] {os.path.basename(excel_path)}: entries={file_entries}, new_words={file_new_words}, new_entries={file_new_rows}")

    save_cache(OUTPUT_CACHE, cache)

    after_words = len(cache)
    print("\n========== SUMMARY ==========")
    print(f"解析到 entries 总行数: {total_entries}")
    print(f"cache words: {before_words} -> {after_words} (新增 {total_new_words})")
    print(f"新增 entry 行数: {total_new_rows}")
    print(f"输出缓存: {OUTPUT_CACHE}")
    print("================================\n")

if __name__ == "__main__":
    main()