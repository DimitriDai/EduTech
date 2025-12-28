
# vocab_service 项目阶段与模块结构说明（冻结版）

> 定位说明  
> 本文档用于**冻结当前工程结构与阶段判断**，作为后续扩展、重构、协作的唯一参考版本。

---

## 一、当前在需求案中的准确位置

### 一句话结论

**你已经完成了需求案的「主干闭环」：**

> 词汇输入 → entry 生成 → 词汇笔记 → 乱序练习 → 音频 → 练习 Docx  
> 这一整条生产流水线，已经处于**功能完成态**。

当前工作性质不再是 MVP，而是：

> **从“脚本集合”收口为“可维护工具链”的工程化收尾阶段**

---

### 按需求案 Step 对齐状态

| 需求案阶段 | 状态 | 说明 |
|---|---|---|
| Step 1 词汇提取 | ✅ 已完成 | extract_service + passage / scattered 输入链路存在 |
| Step 2 字段勾选 | ✅ 已冻结 | field_definitions.py 成为字段唯一真相源 |
| Step 3 entry 匹配 & enrich | ✅ 已完成 | match / enrich / pipeline 已可稳定运行 |
| Step 4 词汇笔记生成 | ✅ 已完成 | Excel + Docx 均已稳定 |
| Step 5 练习生成（乱序 + 音频 + Docx） | ✅ 刚完成 | practice_docx 本地测试通过（关键里程碑） |
| Step 6 智能批改 | ⏸ 未开始 | 需求存在，但当前阶段**不应启动** |
| Step 7 词汇总表整理 | 🟡 半完成 | excel_generator 已具备约 80% 能力 |

**阶段判断：**  
你现在已经处在 **“可以随时上线 demo，但选择继续打磨工程结构”** 的阶段。

---

## 二、冻结级模块结构（PROJECT_STRUCTURE.md）

### 项目目标

将「词汇输入 → 多形态学习材料产出」这一流程  
**工程化、稳定化、可扩展化**。

---

## 1. API 层（Web 接入层）

职责：

- 参数接收  
- 路由分发  
- 返回状态 / 文件路径  

**禁止写业务逻辑**

```text
api/
├─ routes_vocab.py        # 词汇相关 API 路由（触发 pipeline / generators）
├─ schemas.py             # FastAPI 请求 / 响应 schema
└─ app.py                 # FastAPI 入口（注册路由 / 中间件）

---
1. Core 层（全项目真相源，禁止随意改）
职责：
- Entry 原子结构
- 字段定义与输出契约
- 全局约束、守卫、日志
core/
├─ entry_schema.py        # Entry 原子结构定义（系统最小单元）
├─ field_definitions.py   # 字段 key / label / preset / 输出契约（唯一真相源）
├─ guards.py              # 输入校验 / 安全兜底
├─ file_lock.py           # 文件写入锁
├─ logging_setup.py       # 统一日志配置
│
├─ limiter/
│  ├─ base.py
│  ├─ factory.py
│  ├─ memory.py
│  └─ redis_limiter.py
原则：
- generator / service 不得自行定义字段
- 所有表头、列名、字段裁剪，统一服从 field_definitions.py

---
3. Services 层（业务逻辑层）
职责：
- 提取
- 匹配
- enrich
- pipeline 编排
允许失败，但必须可解释、可重试
services/
├─ extract_service.py     # passage / scattered 词汇提取
├─ match_service.py       # 缓存 / 词表匹配逻辑
├─ enrich_service.py      # DeepSeek 字段补全
└─ pipeline_service.py    # extract → match → enrich → cache
extract_service_bootstrap.py   # extract 启动与测试脚手架

---
4. Generators 层（纯导出 / 纯渲染）
职责：
- Excel / Docx 生成
- 不做业务判断
- 不访问模型
generators/
├─ excel_generator.py           # 词汇笔记 / shuffle / 练习 Excel
├─ docx_generator.py            # 词汇笔记 Docx（排版基准）
└─ practice_excel_to_docx.py    # 练习 Excel → 练习 Docx
当前状态：
- practice_docx 已通过本地测试
- 练习渲染链路已闭环

---
5. Tools 层（离线工具链 / 运维工具）
职责：
- 音频合成
- 缓存修复
- 一键编排
不上线，但保证资产完整
tools/
├─ practice_audio_compose.py    # 练习 Excel → 单条 mp3
├─ audio_orchestrator.py        # full / parts 一键编排
├─ audio_cache_build.py         # 音频缓存构建
├─ audio_missing_fill.py        # 缺失音频补齐
├─ missing_audio_enqueue.py     # 缺失音频入队
├─ clear_audio_fields.py        # 清理音频字段
├─ shuffle_practice_generate.py # 旧版 shuffle（备用）
└─ AUDIO_PROTOCOL_LOCK_V1.md    # 音频命名 / combo / timer 协议锁

---
6. Storage（数据与产物）
storage/
├─ uploaded_vocab/              # 用户上传词汇原始文件
├─ audio_cache/                 # 单词 / 例句音频缓存
├─ practice_audio/              # 合成后的练习音频
├─ out/                          # 所有生成产物（Excel / Docx / mp3）
│
├─ global_cache.json             # 主缓存
├─ global_cache.bootstrap.json  # 预置缓存
└─ uploaded_vocab_cache.json    # 上传词库索引

---
7. Utils / 其他
utils/
├─ slug.py                      # slug / 文件名 / 音频路径生成
.env                             # 本地环境变量（不入库）
prefill_cache_from_excel.py      # 从 Excel 预填缓存

---
8. 需求文档
词汇整理 练习生成 一站式工具_需求案.docx
- 当前主线已完成至 Step 5
- Step 6（智能批改）为下一阶段规划内容