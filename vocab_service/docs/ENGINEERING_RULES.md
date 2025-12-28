# vocab_service 工程规范与禁止事项（冻结版）

> 文档目的  
> 本文档用于**明确工程边界与不可逾越的规则**，  
> 以避免后期返工、逻辑污染和隐性耦合。

---

## 一、总体工程原则（必须遵守）

### 原则 1：Entry 是唯一“系统原子”

- Entry（entry_schema.py）是系统中**唯一可复用的数据原子**
- Excel / Docx / 练习 / 批改 **都只是 Entry 的不同视图**
- 禁止出现“为了某个导出格式临时拼一个新结构”

**禁止行为：**

- ❌ 在 generator 中新造字段
- ❌ 在 practice / grading 中引入独立于 Entry 的“半结构数据”

---

### 原则 2：字段契约先于一切实现

- `field_definitions.py` 是字段**唯一真相源**
- 所有列名、表头、字段裁剪，必须来自这里
- generator / service 不得自行判断“要不要这个字段”

**允许行为：**

- ✔ generator 只接收 `selected_fields`
- ✔ 未选字段：要么不输出，要么明确置空（全项目统一）

**禁止行为：**

- ❌ Excel / Docx 中硬编码列名
- ❌ service 层判断“这个 docx 需要哪些字段”

---

### 原则 3：DeepSeek 永远是“补全者”，不是权威

- DeepSeek 输出**永远视为不可靠输入**
- 任何 AI 结果都必须：
  - 可失败
  - 可降级
  - 可绕过

**硬性要求：**

- 超时必须捕获
- 失败不得 500
- enrich 失败 → 允许字段缺失继续跑

**禁止行为：**

- ❌ 任何地方假设 DeepSeek 一定返回完整结构
- ❌ enrich 失败直接中断 pipeline

---

## 二、目录级职责边界（不可越界）

### api/

职责：

- HTTP 参数校验
- request_id 注入
- 错误码映射

**禁止：**

- ❌ 写任何业务判断
- ❌ import services 以外的 generator / core 细节

---

### services/

职责：

- extract / match / enrich / pipeline 编排
- 决策树与缓存策略

**允许：**

- ✔ 内存态修改 entry
- ✔ 返回 missing_fields / explain 信息

**禁止：**

- ❌ import FastAPI / router / app
- ❌ 直接写文件（除非通过统一 cache 接口）

---

### generators/

职责：

- 纯渲染 / 纯导出
- Excel / Docx / mp3 的“视图层”

**允许：**

- ✔ 根据字段元信息排版
- ✔ 做横竖版 / 列宽等视觉判断

**禁止：**

- ❌ 调用 DeepSeek
- ❌ 判断字段业务含义
- ❌ 写缓存 / 改 entry

---

### core/

职责：

- 全局真相
- 枚举 / schema / 约束 / 日志

**规则：**

- core 可被任何层 import
- core **不 import 任何业务层**

---

### tools/

职责：

- 离线脚本
- 运维 / 资产修复

**规则：**

- tools 不作为线上依赖
- tools 可“脏”，但输出必须干净

---

## 三、缓存写入规范（高风险区域）

### 缓存写入原则

1. **一次请求，最多一次写入**
2. 写入必须是：
   - 原子写（tmp → replace）
   - 明确日志

### 推荐结构

- match / enrich：
  - 只改内存 entry
  - 标记 dirty
- pipeline_service：
  - 统一 save()

**禁止行为：**

- ❌ match 写一次 cache
- ❌ enrich 再写一次 cache
- ❌ generator 写 cache

---

## 四、Debug 与日志规范

### Debug 开关

- 只允许通过环境变量控制：
  
```text
VOCAB_DEBUG=1