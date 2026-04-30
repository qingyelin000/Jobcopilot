# JobCopilot 业务层面问题与建议

> 与 `OPTIMIZATION.md`（工程层）互补。本文聚焦**产品定义、业务闭环、数据治理、合规与商业化**。
> 优先级：🔴 影响产品价值/合规｜🟡 影响用户体验/留存｜🟢 体验优化。

> ## ✅ 已落地批次（与 README 同步）
> - §1 领域模型升级（UserInfo / JDInfo / WorkExperience / CandidateJobFit）
> - §2 简历定制：渲染完整简历、ATS 关键字覆盖、事实一致性 factcheck、JD 文风 style_profile
> - §3 模拟面试：Rubric 命中点 + 多维评分 + Coding 静态审阅 stub + next_actions + 复盘导出
> - §4 文档解析：pdfplumber 主解析 + docx 解析（OCR 仅 TODO）
> - §5 投递管理：ApplicationRecord/Timeline + dashboard + transition
> - §6 Chat：会话持久化（ChatSession/ChatMessage），删除"附近职位"承诺
> - §7 隐私：PII redact 工具、LLM 二次同意、数据销毁、AuditLog
> - §8 商业化骨架：User.plan + UsageEvent 模型（计费策略尚未启用）
> - §9 反馈/埋点：FeedbackEntry + AnalyticsEvent + 路由
> - §10 国际化/Track：解析 schema 已含 language/track；UI 切换待落地
>
> 仍未落地：§3.3 题源扩展、§3.4 真 Coding 沙箱、§4.1 OCR、§9.3 RAG CI、§10.2 SEO/SSR/移动端。

---

## 一、领域模型与"业务真实性"

### 🔴 1.1 `UserInfo` 简历模型严重过简，缺核心招聘字段
`backend/schemas.py` 中：
```
UserInfo = name + education(单字符串) + global_tech_stack + projects
ProjectExperience = project_name + role + description + tech_stack
```
- **缺失**：工作经历 / 实习 / 求职意向 / 期望城市 / 期望薪资 / 工作年限 / 学校层级 / 毕业时间 / 外语 / 证书 / 获奖 / 联系方式。
- **业务影响**：
  - 校招 vs 社招、3 年 vs 8 年完全无法区分，下游"匹配度"自然不准。
  - 项目段缺"时间、团队规模、个人占比、量化结果"，**STAR 法则中最关键的 R (Result) 没有结构化字段**，模型重写时只能凭描述里的零散数字，**无法保证"可量化、不杜撰"**。
  - 用户上传完整简历但系统只能识别一小部分，已经决定了"重写"质量上限。
- **建议**：给 schema 升级到 `v2`：
  ```python
  class WorkExperience(BaseModel):
      company: str
      title: str
      start: str  # YYYY-MM
      end: str | None
      team_size: int | None
      individual_contribution: str
      quantified_results: list[str]   # ["将 P99 从 1.2s 降至 240ms", ...]
  class CandidateProfile(UserInfo):
      target_role: str
      target_city: list[str]
      expected_salary_kk: tuple[int, int] | None
      years_of_experience: float
      level: Literal["intern", "campus", "1-3y", "3-5y", "5-10y", "10y+"]
      languages: list[str]
      certificates: list[str]
      work_experience: list[WorkExperience]
  ```

### 🔴 1.2 `JDInfo` 缺招聘要素，"匹配度"无依据
- 当前只有 `must_have_skills / nice_to_have / responsibilities / business_domain`。
- 缺：薪资 / 学历 / 工作年限 / 城市 / HC 数 / 是否 Base / 招聘行业 / 汇报对象。
- **业务影响**：用户问"我适不适合投"时，系统**没有数据回答**——年限差 5 年的岗，文本相似度仍可能 90%。

### 🔴 1.3 "匹配度"模型评估的是"AI 输出质量"而非"候选人 ↔ 岗位的真实匹配度"
- `MappingQualityScore` / `RewriteQualityScore` 衡量的是 LLM 自评，不是用户最关心的 **"我投这家成单概率"**。
- 应另外建一个面向用户的 `CandidateJobFit`：
  ```
  hard_requirement_pass: bool        # 学历/年限/必备技能等硬条件
  skill_coverage_ratio: float        # must_have 命中率
  experience_gap_years: float
  domain_familiarity: float
  recommended_action: Literal["可冲刺", "可投", "建议补强后再投", "不建议"]
  gap_explanation: list[str]
  upskill_plan: list[{topic, est_hours, resources}]
  ```
- **没有这个指标，产品就只是"改简历工具"**，无法回答"我下一步该投谁、该补什么"。

---

## 二、简历定制（Resume Customization）业务

### 🔴 2.1 输出形态不可直接交付
- `OptimizedResume = summary_hook + skills_rewrite_suggestions + optimized_projects.optimized_bullets`。**用户最终要的是一份完整 PDF/Markdown 简历**，目前只给了"局部 bullet"，要用户自己复制粘贴回原简历。
- **建议**：
  1. 新增 `RenderableResume` schema，能直接渲染为 Markdown / PDF / Docx 模板（多模板可选）。
  2. 提供"一键导出针对岗位 X 的简历"接口，文件名带岗位/公司/版本号。
  3. 保留版本树，便于用户对同一原始简历做多次定向投递。

### 🔴 2.2 "诚实边界"未形成强约束
- `parse_resume_to_json` 的 prompt 写了"不得杜撰"，但 `rewrite_resume_bullets` 阶段未在代码层做事实校验：
  - 没有把"原始 bullet 文本 + 数字"作为 hard constraint 喂回给重写 prompt 让模型对齐。
  - `honesty_risks` 字段仅供查看，不参与"reject 重写结果"的循环。
- **业务风险**：模型自由发挥往往会"造数据"（如 QPS、用户量、降本百分比），用户拿去投简历→面试被问→当场塌房。
- **建议**：
  - rewrite 阶段加"事实校验 agent"：抽取重写后所有数字与名词，回原始简历搜索来源；找不到来源→标红/驳回。
  - 重写结果至少给 3 档：保守（仅措辞）/ 平衡 / 激进（含合理推测，必须用户确认）。

### 🟡 2.3 缺 ATS 关键词覆盖与建议
- 国内大厂的 ATS 关键词筛选已经很普遍，外企更甚。
- 当前 prompt 里"自然融入 JD 关键词"是软目标，但**没有覆盖率检查**——重写后是否真的覆盖了 must_have？应输出：
  ```
  ats_coverage = {
    "must_have": {"Kafka": True, "Flink": False, ...},
    "nice_to_have": {...},
    "missing_keywords_to_add": [...]
  }
  ```

### 🟡 2.4 文风/行业风格未差异化
- 互联网大厂、外企、国企/银行、咨询、设计岗的简历语言风格差异极大；当前一个 prompt 通吃。
- 建议在 JD 解析时识别行业/公司类型，作为 rewrite stage 的 `style_profile` 输入。

---

## 三、模拟面试业务

### 🔴 3.1 评分缺 Rubric → 同一道题分数不可复现
- `docs/mock_interview_rag_design.md` 设计了"Rubric RAG"双库检索，但 `evaluator_agent_evaluate_answer` 的 prompt **完全没有把 rubric / expected_points 喂进去**，等于让模型靠经验拍分。
- **业务影响**：
  - 同一答案两次评分相差 20 分的情况会很常见，用户失去信任。
  - 跨用户对比无意义（无法做"你比 70% 同岗位用户答得好"这种产品话术）。
- **建议**：
  1. 题目侧维护 `expected_points`（关键得分点列表）和 `bad_signals`（典型踩坑）。
  2. 评分 prompt 强制用 `must_hit / hit / partial / miss` 四档对每个 expected_point 打标，再聚合为分数。
  3. 输出"得分点逐条命中表"给用户。

### 🔴 3.2 评分维度漏关键能力
- 当前维度：accuracy / depth / structure / resume_fit / overall。
- 漏：**communication（表达清晰）、honesty（不会就说不会）、problem_solving_process（拆解步骤）、edge_case_awareness（coding 必查）、time_complexity（coding）**。
- 行为面（BQ）的 STAR 完整度也无评分维度——而面试场上 BQ 占比常常 30%+。

### 🔴 3.3 题源与场景过窄
- `crawl_nowcoder_interviews.py` 仅采集牛客中文面经；
  - 海外/英文岗位、外企/咨询/管培、产品/设计/数据 几乎无覆盖。
  - 题库版本 `_v1`，无定期刷新策略；八股每年都在变（如 Spring 6 / JDK 21 / RAG/LLM 工程），陈旧题占比会逐月上升。
- **建议**：
  - 引入开源题库（GitHub 上 MIT/Apache 的面试题项目）作为补充。
  - 加题库版本号 + 题目 `last_verified_at`；评估脚本输出"过期题比例"看板。
  - 题源标签必须含 `language`(zh/en) 和 `track`(backend/frontend/algo/data/product/design/...)，让 retrieval filter 直接生效。

### 🔴 3.4 Coding 题没有运行/判题环节
- `question_type` 含 `coding`，但没有沙箱执行、用例集、复杂度静态分析。
- **业务影响**：算法岗/后端 coding 是核心，仅凭 LLM 评判文字答案 → "看起来对就给高分"。
- **建议**：接 [Judge0 / 自建 Docker 沙箱]，或先支持"用户在前端粘贴代码 → 后端用预设用例运行 → 把结果回喂给评分模型"。

### 🟡 3.5 题目与简历脱节风险
- `interviewer_agent_pick_question` 通过正则在简历文本里抽 project / skill anchors（前 4 / 前 10）。
- 当简历排版稍异（双栏、表格、英文）时正则抽不到，模型就**纯按 JD 出题**，与简历完全脱钩。
- **建议**：用第 §1.1 升级后的结构化 `CandidateProfile.work_experience / projects` 直接传入，不再依赖文本启发式抽取。

### 🟡 3.6 面试节奏一刀切
- `max_rounds` 固定（默认 5），不区分岗位级别 / 表现差异。资深岗 5 轮根本聊不出深度；应届生 5 轮可能挫败感太强。
- 建议根据 `level + decision == finish` 动态终止；表现好提早进入"系统设计深题"，表现差自动降难度并在结束时给学习路线。

### 🟡 3.7 没有"反馈→改进闭环"
- Summary 输出 strengths/improvements，**但没有给用户**：
  - 针对每个 gap 的 RAG 资料推荐 / 真题集合 / 自检 Quiz
  - "下次面试再来"的复练入口（同领域不同题 / 同题难度升级）
- 这是面试产品最重要的"复购钩子"，目前缺失。

### 🟢 3.8 缺会话回放与导出
- 用户面完想分享/复盘，没法导出 Markdown 报告。建议 `/sessions/{id}/export?format=md|pdf`。

---

## 四、PDF / 简历输入

### 🔴 4.1 PyPDF2 对真实简历适配差
- PyPDF2 对**双栏、表格、图标式技能墙、扫描版**抽出来基本是乱序甚至空白。
- **业务影响**：用户上传一份"看起来很正常"的 PDF 简历，进入解析阶段直接丢项目/丢公司——首跳留存严重受损。
- **建议**：
  1. 改用 `pypdfium2` / `pdfplumber` / `mineru`，对双栏与表格表现更好。
  2. 加 OCR 兜底（`paddleocr`/`rapidocr`），用于扫描版简历。
  3. 支持 `.docx` 解析（`python-docx` / `mammoth`），覆盖国内主流模板。
  4. 解析结果先回显给用户校对，再进入 LLM 结构化（避免"GIGO"）。

---

## 五、缺失的核心闭环——投递 / 跟进

### 🔴 5.1 README 痛点 #1 是"海投效率低"，但代码里没有"投递管理"
- 没有：投递记录表、阶段流转（投递→笔试→面试 1/2/3→Offer→拒）、面经回填、HR 联系记录、Offer 比较。
- **业务影响**：用户改完简历就走，没有后续粘性；产品停在"工具"层，永远成不了"求职操作系统"。
- **建议（最小 MVP）**：
  ```
  ApplicationRecord
    user_id, company, role, jd_id, resume_version_id,
    channel(boss/lagou/官网/内推/邮件),
    stage(投递/笔试/一面/二面/HRBP/Offer/拒/无回应),
    deadline_at, last_contact_at,
    notes, attachments_json
  ```
  + 每日待办（"3 天没动静的可催 HR / 5 天前的笔试可复盘"）。
  + 与简历定制&面试模块串联：导出简历时自动新增一条"投递候选"。

### 🟡 5.2 多简历/多岗位绑定缺失
- 已有 `ResumeDocument` 多版本，但没有"哪份简历投了哪家公司"。
- 投递记录上 → 简历版本 → 重写依据 JD → 面试结果 → Offer 决策，整条链路没有 ID 串起来。

---

## 六、聊天 / 助手能力

### 🔴 6.1 `/api/v1/chat` 是无状态裸 LLM 调用
- 一次 `ChatOpenAI.ainvoke(prompt)` 直接返回，没有：
  - 用户身份与画像注入（即便已登录也不会带入 `target_role`、最近的 mock interview 表现）。
  - 历史对话 memory（用户每次都得重新自我介绍）。
  - 工具调用（无法触发"帮我看看这次面试的总结""帮我推一道题"）。
- **业务影响**：定位为"AI 求职助手"，但行为只是"通用聊天框"，与 ChatGPT 没差异化。
- **建议**：用 LangGraph 或 OpenAI Function Calling 把它做成 **agentic chat**，工具集合包括：
  `read_my_resume / search_jobs / start_mock_interview / pull_my_recent_feedback / draft_followup_email`。

### 🔴 6.2 README 提到的"附近职位检索"在代码里看不到落地
- 文档说"聊天命中『附近职位检索』会触发定位授权"，但 `chat_with_agent` 没做意图识别，也没招聘网调用。
- 业务上**承诺了用户却没兑现**（或者只在某条隐藏分支里有），属于产品话术风险。请要么补实现（接 LinkedIn/Boss/拉勾 公开 API 或允许用户上传搜索结果 CSV），要么从 README 删掉。

---

## 七、合规、版权与隐私

### 🔴 7.1 牛客面经爬取与 README "拒绝爬虫" 自相矛盾
- README 强调"数据源完全依赖用户自主输入...彻底规避反爬风险"，实际仓库内有 `crawl_nowcoder_interviews.py`，并直接调用 `gw-c.nowcoder.com` 内部 API（设计文档明列）。
- **风险**：
  - 牛客 ToS 明确禁止爬虫；账号封禁、IP 封禁、严重时律师函。
  - 题库内容版权属于原作者/牛客，作为商业化产品分发可能侵权。
- **建议**：
  1. 立即停止使用未授权数据源进行**再分发**（仅本地研究可考虑，但需在 README 明示）。
  2. 题源切到：开源 MIT/CC 协议面试题（JavaGuide/CS-Notes/...）+ 用户上传 + 模型自生成。
  3. 数据库加 `source_license` / `attribution_required` / `redistribution_allowed`。
  4. 前端展示题目时附"参考来源 + 二次创作免责声明"。

### 🔴 7.2 PII 与 LLM 出境
- 简历包含手机号、邮箱、身份证、家庭住址等 PII；当前**明文存 MySQL，明文发到 DeepSeek/Mimo**。
- 国内合规（《个人信息保护法》）要求：
  - 用户**明确知情同意**才能将 PII 发给第三方处理者。
  - 数据**最小化**：能脱敏就脱敏（手机号 → 前 3 后 4，姓名/身份证不进 LLM）。
  - **跨境/第三方处理**需在隐私政策列出。
- **建议**：
  1. 解析阶段在送 LLM 前做 PII redact（保留语义占位符 `<PHONE>`、`<EMAIL>`），输出后再回填。
  2. `parsed_json`、`source_text` 加字段级加密（KMS / Fernet）。
  3. 提供"删除我的所有数据"接口（GDPR-style）。
  4. 在前端注册/上传时弹出"数据将发送至大模型服务（DeepSeek/Mimo）"二次同意。

### 🟡 7.3 操作审计缺失
- 谁在何时下载了某用户简历、谁在何时修改了 JD —— 没有审计表。一旦发生数据泄露事件追责困难。

---

## 八、商业化与成本

### 🔴 8.1 单次"定制"调用 LLM 5+ 次，无配额/付费分层
- 一次完整流程：`parse_resume + parse_jd + map + rewrite + score(mapping) + score(rewrite)`，加上面试每轮 2 次（出题 + 评分），日活上 100 就开始烧钱。
- 没有：
  - **用户配额**（免费用户每月 N 次定制，会员无限）。
  - **token 上限**（恶意用户灌 50KB 简历直接打爆）。
  - **缓存/秒级去重 UI 展示**（哈希命中后免费提示）。
- **建议**：
  1. `User.plan = free | pro | team`，对应不同配额表。
  2. 调用前做 token 预估（`tiktoken` 或 mimo SDK），超过阈值拒绝或降级到便宜模型。
  3. 在路由层 emit 计费事件，写到独立 `usage_events` 表，便于做账与做风控。

### 🟡 8.2 模型选择策略写死，无成本/质量自动权衡
- `_resolve_stage_model` 把模型写在 env，但没有运行时根据"用户付费等级 / 任务难度"自动选模型（小任务用 chat、大任务用 reasoner、批量低质量任务用更便宜的）。
- 建议建一个 `ModelRouter`，按 `(stage, plan, complexity_estimate)` 路由。

---

## 九、产品反馈与数据飞轮

### 🟡 9.1 没有用户反馈回路
- 重写结果好不好用、面试题问得对不对，前端没有 👍/👎 + 文本反馈按钮，**所以模型永远不会变好**。
- 反馈数据是后续微调 / few-shot 池的最大金矿，目前 0 收集。

### 🟡 9.2 没有产品级埋点
- 不知道用户卡在哪一步：上传 PDF 解析失败率？mock interview 平均到第几轮就退出？哪个 stage 答得最差？
- 建议接 PostHog / 自建 `events` 表，前后端各埋关键节点。

### 🟡 9.3 没有 RAG 召回评估的回归门禁
- `evaluate_retriever_v2.py` 存在，但没有 CI 回归（题库更新/嵌入模型换版后跑一遍，输出 nDCG@k / 命中率对比）；rerank 收益、过期题占比也无可视化。

---

## 十、产品边界 & 国际化

### 🟡 10.1 默认中文 + 默认后端岗
- 几乎所有 prompt 写死"使用中文"、写死"Senior backend interviewer"。
- 算法 / 前端 / 数据 / 产品 / 设计 / 运营全无独立 prompt。海外用户/英文 JD 完全不可用。
- 建议：每个 `track` 一套 prompt 模板 + i18n；至少 zh-CN / en-US 双语。

### 🟢 10.2 无障碍 / SEO / 移动端
- React SPA + nginx try_files，搜索引擎收录差；招聘内容页（"x 公司面经汇总""x 岗位简历范例"）SEO 价值大，建议预渲染或迁 Next.js。
- 移动端是求职查岗的高频场景，目前未见专门适配。

---

## 优先实施清单（建议按"商业风险→产品价值→体验"）

### 第 1 优先（风险止损 + 产品立项底线）
1. **§7.1 合规**：要么停爬，要么加授权与 license 字段；README 与代码自洽。
2. **§7.2 PII**：上线前必须做 PII redact + LLM 跨境同意。
3. **§2.2 诚实约束**：rewrite 加事实校验，避免用户拿"造数据简历"被面试官识破。
4. **§3.1 + §3.2**：Rubric 化评分、补关键评分维度，让模拟面试结果"有解释、可复现"。

### 第 2 优先（核心业务闭环）
5. **§1.1 / §1.2 / §1.3**：升级简历 & JD schema，新增"候选人 ↔ JD 真实匹配度"模型。
6. **§5 投递管理**：补 Roadmap Phase 3，构成"定制 → 投递 → 面试 → 复盘"闭环。
7. **§2.1 简历可导出**：从"建议片段"升级到"完整可下载简历"。
8. **§4 PDF/Docx 解析升级**：解决首跳留存。

### 第 3 优先（差异化 & 商业化）
9. **§3.4 Coding 沙箱、§3.7 学习路径推荐、§3.8 复练**：构建复购钩子。
10. **§8 计费配额、§9 反馈回路 / 埋点**：让产品具备增长 & 迭代能力。
11. **§6 chat agent 化 + §10 国际化**：拓宽人群。

完成第 1、2 两批后，JobCopilot 才真正从"AI 改简历 demo"升级为可对外、可商业化的求职平台。
