# 🚀 JobCopilot - AI 求职辅导与定制投递引擎

## 📖 项目背景与愿景

在技术求职过程中，候选人往往面临 **"海投效率低"**、**"简历匹配度差"** 以及
**"面试八股 / 场景题缺乏真实反馈"** 的痛点。**JobCopilot** 通过一个由 LLM 驱动的多智能体协作流水线，
覆盖 **JD 解析 → 简历定制 → 适配评分 → 模拟面试 → 投递跟踪 → 复盘** 的完整业务闭环。

### 🌟 核心设计理念
- **拒绝爬虫焦虑**：不主动抓取第三方招聘数据，完全依赖用户上传 + 公开技术语料。
- **结构化优先**：Agent 之间通过 Pydantic Schema 通信（含 ATS 命中、事实一致性、Rubric 命中点等业务字段）。
- **诚实为先**：重写简历后强制走 `factcheck_rewrite`，禁止 LLM 编造原文未出现的数字、技术栈或经历。
- **业务闭环**：从分析 → 投递 → 面试 → 复盘 → 反馈，每一步都有结构化数据落库，便于后续个性化与回归。
- **隐私可控**：内置 PII redact，登录用户可一键销毁全部数据；登录后需显式同意 LLM 处理。

---

## 🏗️ 系统架构

1. **Frontend (展示层)**：基于 React + Vite（之前的 Streamlit 入口已下线）。
2. **Backend (中枢控制层)**：FastAPI 单体（`backend/api.py`），调度 LLM Agent、RAG 检索与业务持久化。
3. **AI 编排层**：DeepSeek（chat / reasoner） + Mimo（map / rewrite / evaluator）双客户端，由
   `_resolve_stage_model` 按阶段路由；面试题库走 Qdrant + Embedding 召回（v2 retriever）。
4. **数据层**：MySQL 持久化用户/简历/JD/Process Job/面试 Session/投递记录/反馈/事件/审计；Redis 用作限流；Qdrant 存题库向量。

---

## 🛠️ 核心业务闭环 (业务级 Agent Flow)

### 阶段一 · 简历分析与定制
1. **简历解析官** → `UserInfo`（含 `years_of_experience` / `level` / `track` / `expected_salary_kk` / `work_experience`）
2. **岗位分析官** → `JDInfo`（含 `salary_range_kk` / `years_min/max` / `industry` / `style_profile` / `track`）
3. **匹配映射官** → `ResumeJDMapping` + 内嵌确定性 `AtsCoverageReport`（必备/加分关键字命中率）
4. **简历优化师** → `OptimizedResume`，并强制经过 **`factcheck_rewrite` 事实一致性校验**（数字/技术栈未在原简历出现 = 高风险）
5. **质量评分官** → `MappingQualityScore` + `RewriteQualityScore`
6. **候选人匹配度** → `compute_candidate_job_fit`（产出 `CandidateJobFit`：硬性条件、技能覆盖率、经验差距、`recommended_action`、可执行的 `upskill_plan`）
7. **完整简历渲染** → `render_resume_markdown`，把优化结果 + 原始 UserInfo 拼成完整 Markdown 简历

### 阶段二 · 模拟面试
- **检索增强**：Qdrant + Embedding 召回相似题目，作为出题官的素材；本地数据集兜底。
- **出题官 Rubric**：题目可携带 `expected_points` / `bad_signals` / `question_type`，evaluator 必须逐条命中点判定。
- **评估官 Rubric 评分**：除原有 accuracy/depth/structure/resume_fit 外，新增 `communication / honesty / problem_solving_process / edge_case_awareness / time_complexity (coding) / star_completeness (BQ)`。
- **Coding 题静态审阅 (stub)**：解析答案中的代码块，输出复杂度/边界/异常的启发式 review；真实 Judge0 沙箱仍是 TODO。
- **动态终止**：基于 `decision == finish` 而非死板回合上限。
- **Summary 生成**：含 `next_actions`（每个 gap 给出复练建议占位资源）。
- **复盘报告导出**：`GET /api/v1/interview/sessions/{id}/export?fmt=md` 一键导出 Markdown 复盘。

### 阶段三 · 投递管理
- `ApplicationRecord` 持久化每一份投递（公司/岗位/渠道/阶段/截止时间/笔记）。
- `ApplicationTimeline` 记录阶段流转日志，配合 `POST /applications/{id}/transition`。
- `GET /api/v1/applications/dashboard` 输出阶段聚合 + 临近 Deadline 列表。

### 阶段四 · 反馈与复盘
- `POST /api/v1/feedback`：对任意目标（mapping/rewrite/question/answer/summary）打 ±1 + 评论。
- `POST /api/v1/events/track`：前端业务事件埋点。
- 后端关键节点（Process / Interview / Application）会自动写 `analytics_events`。

---

## 🔐 隐私与合规

- **PII redact**：`pii_utils.redact()` 在送 LLM 前替换手机号/邮箱/身份证/URL 为占位符；调用方可按需 `unredact`。
- **二次同意**：用户首次执行 LLM 流程前需调用 `POST /api/v1/users/me/llm-consent` 显式同意；前端可据 `User.llm_processing_consent_at` 判断是否弹同意书（UI 接入待落地）。
- **数据销毁**：`DELETE /api/v1/users/me/data` 删除该用户全部派生数据（简历/JD/Process/面试/投递/反馈/事件/审计），同时写入 `AuditLog`。
- **审计**：`AuditLog` 模型已落库；当前先记录关键写操作，后续会接入中间件覆盖更多敏感路由。

---

## 🆕 新增 API（业务闭环相关）

| 路由 | 说明 |
| --- | --- |
| `POST /api/v1/fit` | 候选人 ↔ JD 适配度评估，返回 `CandidateJobFit` + `AtsCoverageReport` |
| `POST /api/v1/resumes/render` | 用 OptimizedResume + UserInfo 渲染完整 Markdown 简历 |
| `POST /api/v1/resume/parse-docx` | 在 PDF 之外新增 docx 解析（pdfplumber 取代 PyPDF2 作为 PDF 主解析器） |
| `POST /api/v1/applications` 等 | 投递记录 CRUD + transition + dashboard |
| `POST /api/v1/feedback` | 业务对象的 ±1 反馈 |
| `POST /api/v1/events/track` | 前端业务事件埋点 |
| `POST /api/v1/users/me/llm-consent` | 显式登记 LLM 处理同意 |
| `DELETE /api/v1/users/me/data` | 一键销毁本人全部派生数据 |
| `POST /api/v1/chat/sessions` | 创建带画像快照的 chat 会话；`POST /api/v1/chat` 现支持 `session_id` 续聊 |
| `GET /api/v1/interview/sessions/{id}/export` | 导出面试复盘报告 (md/json) |

> 说明：`/process` 的返回体新增了 `ats_coverage` / `fact_check` / `candidate_job_fit` / `rendered_resume_markdown` 四个字段，向后兼容（旧字段保留）。

---

## ⚙️ 快速开始 (Docker 部署)

### 前置要求
- 已安装 **Docker Desktop** 或 Docker Engine。

### 1. 配置环境变量（`.env` 与 `docker-compose.yml` 同级）

```env
DEEPSEEK_API_KEY=sk-xxxxxx
OPENAI_BASE_URL=https://api.deepseek.com

DATABASE_URL=mysql+pymysql://root:你的密码@host.docker.internal:3306/jobcopilot?charset=utf8mb4
JWT_SECRET=请替换成随机强密钥
JWT_EXPIRE_MINUTES=10080

# Qdrant（面试题库 RAG）
QDRANT_URL=http://qdrant:6333

# 限流（可选）
REDIS_URL=redis://redis:6379/0
```

> 不再需要 `GEO_*` 配置——"附近职位检索"已从产品中下线，参见下方说明。

### 2. 初始化数据库
```sql
CREATE DATABASE jobcopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 构建与启动
```bash
docker compose up -d --build
```

### 4. 访问应用
- **前端**：http://localhost:5173
- **后端 API 文档**：http://localhost:8000/docs

### 5. 职位规划助手（取代旧"附近职位检索"）
- 旧版基于浏览器定位 + 反向地理编码召回"附近职位"的能力已下线（数据来源依赖第三方爬取，不在合规范围）。
- 新版 `POST /api/v1/chat`（可选 `session_id`）会在后端注入用户画像 (`target_role` / `city` / `profile_summary`) 与可调用工具的伪 function 列表（read_my_resume / start_mock_interview / pull_recent_feedback / draft_followup_email），真实工具落地是下一阶段任务。

---

## 🌐 国际化与岗位 Track

- 简历/JD 解析现已声明 `resume_language` / `jd_language` (zh/en) 与 `track`（backend/frontend/mobile/fullstack/algorithm/data/devops/qa/product/design/operations/general）。
- 面试评估官按 `track` 在 prompt 中追加方向性提示。
- UI 层多语言切换尚未落地，已在 Roadmap。

---

## 🗺️ 开发路线 (Roadmap)

- [x] **Phase 1 · MVP**：Pydantic Schema + 解析/优化 Agent。
- [x] **DevOps**：Docker / 前后端解耦 / 限流。
- [x] **Phase 2 · RAG**：Qdrant + Embedding 召回（v2 retriever）。
- [x] **Phase 2 · LangGraph 风格状态机**：模拟面试 Human-in-the-loop。
- [x] **Phase 3 · 投递小助手 v0**：投递 CRUD + 阶段流转 + dashboard。
- [x] **Phase 3 · 简历一键 Markdown 导出**。
- [x] **Phase 3 · 业务闭环增强**：ATS 覆盖、事实校验、Fit 评分、Rubric 评分、复盘导出、反馈/埋点。
- [ ] **Phase 4 · 真 Coding 沙箱**（Judge0 / Docker 隔离执行）。
- [ ] **Phase 4 · OCR 兜底**（扫描版 PDF）。
- [ ] **Phase 4 · 多语言 UI 切换 + SEO/SSR**。
- [ ] **Phase 4 · 商业化**：基于 `User.plan` + `usage_events` 做配额 & 计费。

---

*Powered by FastAPI · LangGraph 风格编排 · DeepSeek + Mimo · Qdrant · React/Vite*
