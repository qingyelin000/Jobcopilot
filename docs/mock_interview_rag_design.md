# 模拟面试 RAG 设计

## 1. 目标

模拟面试不做成“泛聊天”，而做成一个基于真实面经和评分规则的面试引擎。

核心目标有三件事：

1. 用真实面经决定“问什么”。
2. 用用户简历和 JD 决定“优先问什么”。
3. 用评分规则决定“答得怎么样，下一步怎么追问”。

这意味着模拟面试的底层不应只是一个对话接口，而应该由以下几层组成：

- 面经题源库
- 评分规则库
- 检索与重排层
- 有状态面试流程
- 会话记录与复盘层

## 2. 为什么这个功能更适合承载 RAG / LangGraph / Function Calling

简历优化更像一条固定流水线：

- 解析简历
- 解析 JD
- 生成映射
- 重写 bullet

而模拟面试天然是一个动态过程：

- 先根据岗位和简历选题
- 用户回答后判断是否追问
- 结合知识点和 rubric 评分
- 按当前表现调整后续题目

因此模拟面试更适合承载：

- `RAG`：从外部面经和评分规则中取证
- `Function Calling`：检索题目、读取简历、保存轮次、生成总结
- `LangGraph`：显式管理面试状态与流转
- `Skill`：拆成技术面 / 项目面 / 行为面等不同策略
- `Multi-Agent`：后续再拆 interviewer / evaluator / coach

## 3. 总体架构

```text
用户简历 + JD
        |
        v
能力画像抽取
        |
        v
面试计划生成
        |
        v
RAG 检索题源 + 检索评分规则
        |
        v
出题 -> 用户回答 -> 评分 -> 追问/切题
        |
        v
整场总结 + 改进建议
```

系统拆成六个模块：

1. `ingest`
   负责采集和清洗牛客面经、题目、追问、来源元信息。
2. `retriever`
   负责面经题源库和评分库的召回与重排。
3. `planner`
   负责根据简历、JD 和历史回答安排题目配比。
4. `evaluator`
   负责按 rubric 评分，并决定是否追问。
5. `session_service`
   负责会话状态、轮次记录、总结。
6. `graph`
   负责把完整流程显式建模为状态图。

## 4. 数据层设计

### 4.1 面经原始层

先保留原始响应，方便回放和重新清洗：

- `raw/search/*.json`
- `raw/detail/*.json`

这一层不做语义假设，只保证：

- 来源可追溯
- 接口原文可复现
- 后续抽取失败时可重跑

### 4.2 规范化层

规范化后，每条记录至少有：

- `source_platform`
- `source_url`
- `source_type`
- `content_id`
- `content_type`
- `title`
- `body_text`
- `author`
- `create_time`
- `tags`
- `view_count`
- `comment_count`
- `like_count`
- `query`

### 4.3 题目层

后续从面经正文中抽取真正可用的题目单元：

- `question_id`
- `case_id`
- `company`
- `role`
- `round_type`
- `question_text`
- `question_type`
- `topic_tags`
- `followups`
- `expected_points`
- `difficulty`
- `source_span`

### 4.4 评分规则层

评分规则不直接从用户对话里生成，而维护成单独知识层：

- `rubric_id`
- `question_type`
- `topic_tags`
- `must_hit_points`
- `good_signals`
- `bad_signals`
- `followup_rules`
- `score_dimensions`

## 5. 检索设计

模拟面试的 RAG 不只检索“题目”，而是双库检索：

1. `Question RAG`
   - 找真实面经里的题目、追问、轮次信息
2. `Rubric RAG`
   - 找这类题目应该怎么评、应该追问什么

每一轮检索时，输入上下文包括：

- 当前目标岗位
- JD 关键词
- 用户简历中的项目和技能
- 当前面试阶段
- 当前会话中已问过的问题
- 当前暴露出来的薄弱点

推荐流程：

1. 过滤
   - 按岗位、方向、轮次、标签过滤
2. 混合召回
   - 关键词召回
   - 向量召回
3. 重排
   - 与 JD 的相关度
   - 与简历项目的相关度
   - 与当前状态的适配度
   - 去重与多样性

## 6. 面试状态机

第一版建议就按显式状态图来做：

1. `setup`
   - 读取简历和 JD 快照
2. `profile_resume`
   - 抽取能力标签、项目标签、风险点
3. `plan_session`
   - 生成本场题型配比
4. `retrieve_candidates`
   - 检索题目和评分规则
5. `ask_question`
   - 生成当前题目
6. `wait_answer`
   - 等待用户回答
7. `evaluate_answer`
   - 评分并提取缺失点
8. `decide_next_step`
   - 追问 / 切题 / 结束
9. `summarize_session`
   - 输出总结和训练建议

这一层最适合后续用 LangGraph 承载。

## 7. Function Calling 设计

第一版不建议直接把全部逻辑交给 agent，自定义工具边界更重要。

推荐工具：

- `get_resume_profile(session_id)`
- `get_jd_profile(session_id)`
- `search_interview_cases(query, filters, top_k)`
- `get_question_detail(question_id)`
- `get_scoring_rubric(question_id or tags)`
- `save_interview_turn(session_id, payload)`
- `generate_session_summary(session_id)`

模型只做两件事：

1. 决定何时调用工具
2. 根据工具结果决定如何提问、如何追问、如何总结

## 8. Multi-Agent 放在哪

不建议第一版就上 multi-agent。

更合理的节奏是：

1. 单 agent + 显式状态机
2. 检索与评分稳定后，再拆角色

第二版可拆成：

- `Interviewer`
  负责选题与追问
- `Evaluator`
  负责评分与风险识别
- `Coach`
  负责总结与改进建议

## 9. 第一阶段落地顺序

先做最小闭环，不要一开始就追求“全栈 agent 化”。

建议顺序：

1. 采集牛客面经原始数据
2. 规范化并存盘
3. 从面经正文抽题
4. 建立检索索引
5. 做单 agent 面试闭环
6. 再用 LangGraph 重构

## 10. 牛客面经采集策略

第一阶段优先走牛客公开 JSON 接口，而不是先做页面 DOM 解析。

当前已确认可用链路：

1. 搜索接口
   - `POST https://gw-c.nowcoder.com/api/sparta/pc/search`
   - 用于按关键词拉取帖子列表
2. 长帖详情接口
   - `GET https://gw-c.nowcoder.com/api/sparta/detail/content-data/detail/{id}`
3. 动态帖详情接口
   - `GET https://gw-c.nowcoder.com/api/sparta/detail/moment-data/detail/{uuid}`

推荐采集策略：

1. 先按关键词搜帖子列表
2. 根据 `contentType` 分流详情接口
3. 保存原始搜索 JSON 与详情 JSON
4. 统一清洗成 JSONL
5. 后续再做题目抽取和 embedding

## 11. 目录建议

```text
docs/
  mock_interview_rag_design.md

backend/
  interview/
    retriever.py
    planner.py
    evaluator.py
    graph.py
    session_service.py
  scripts/
    crawl_nowcoder_interviews.py
```

## 12. 当前结论

当前最先做的不是聊天界面，也不是面试 UI，而是：

1. 把牛客面经采集下来
2. 把原始 JSON 与规范化 JSONL 存好
3. 为后续题目抽取、RAG 建库和状态机面试做数据底座
