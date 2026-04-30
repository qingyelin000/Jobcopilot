# RAG 改进建议交接文档

## 范围

本文档记录 JobCopilot 当前 RAG 链路的优化建议与后续执行交接信息。范围仅覆盖本次审查中除“本地 `.env` 密钥治理”之外的 RAG 相关问题。

当前 RAG 主要用于模拟面试题库检索增强：`backend\interview\retriever_v2.py` 负责 Qdrant dense 召回、本地词法召回、融合排序与 rerank；`backend\api.py` 在面试开始和每轮答题后调用 retriever；`backend\agents.py` 使用候选题作为参考生成下一题。

## 交接更新规则

每完成一个优化步骤，都在本文档的“执行记录”中新增一行，至少包含：

- 完成时间或提交标识
- 处理的优化项编号
- 改动文件
- 验证方式与结果
- 遗留风险或下一步

## 执行记录

| 状态 | 优化项 | 说明 |
| --- | --- | --- |
| 已记录 | 全部建议 | 已创建本交接文档，尚未实施代码改动。 |
| 已完成 | Docker smoke test / Qdrant 启动 | `docker compose up -d qdrant` 成功启动 Qdrant；`docker compose ps qdrant` 显示服务运行并暴露 6333/6334。 |
| 已完成 | Docker smoke test / 临时索引 | 使用 `backend` 容器执行 `scripts/build_qdrant_index.py`，以 `hash` embedding 重建临时集合 `rag_smoke_hash_test`；manifest 显示输入 10397 条、索引 10397 条、跳过 0 条。 |
| 已完成 | Docker smoke test / 检索验证 | 使用 `scripts/run_retriever_v2.py` 查询 `Java 后端 Redis 一致性 高并发 面试题`，成功返回 5 条 Redis/MySQL 一致性相关题。观察到返回顺序的展示分数不完全单调，后续可检查 MMR 去重选择后是否需要按最终展示分或 adjusted score 明确排序。 |
| 已完成 | Docker smoke test / 评测脚本 | 使用临时 hash 集合运行 `scripts/evaluate_retriever_v2.py`，能产出评测报告。注意：`data\nowcoder\eval\retriever_cases.sample.jsonl` 中存在 `replace_with_question_id_*` 占位标注，所以 Hit/NDCG 等有监督指标不能代表真实质量；无监督 `quality_score` 可作为 smoke 参考。 |
| 已完成 | Docker smoke test / HTTP API | 启动 backend 后调用 `POST /api/v1/interview/retrieve`，能通过临时集合返回 5 条结果，证明 Docker 环境下 API -> RetrieverV2 -> Qdrant 链路可用。 |
| 已完成 | P0-2 | 已调整 `backend\interview\retriever_v2.py`：dense 查询优先使用当前索引脚本创建的 unnamed vector；仅保留 named vector `dense` 作为旧集合兼容 fallback。验证：`run_retriever_v2.py`、`evaluate_retriever_v2.py`、HTTP API smoke 均通过。 |
| 已完成 | score 展示排序 | 已调整 `backend\interview\retriever_v2.py`：MMR/去重选出结果后，最终返回按展示 `score` 降序排序，避免 API 消费方看到非单调分数。验证：脚本与 HTTP API 返回均已按 score 递减。 |
| 已完成 | 回归测试 | 在一次性 backend 容器中执行 `docker compose run --rm --no-deps -w /app -e PYTHONPATH=/app backend sh -c "pip install -q -r requirements-test.txt && pytest tests/test_auth_and_interview_api.py -q"`，9 个测试通过。 |
| 已完成 | Docker 配置恢复 | smoke test 期间 backend 临时使用 `rag_smoke_hash_test`、`hash`、`RERANK_ENABLED=false`；测试后已执行 `docker compose up -d --force-recreate backend`，确认恢复为 `nowcoder_interview_questions_v1`、`openai_compatible`、`RERANK_ENABLED=true`。 |
| 已完成 | 临时产物清理 | 已删除临时 Qdrant 集合 `rag_smoke_hash_test`，并移除本次 smoke 生成的临时 manifest/eval JSON 文件。 |
| 已完成 | 银标指标评测 | 基于 3 个临时人工判断银标 case 测试 Hit@5、Recall@5、NDCG@5。扩展银标结果：Hit@5=1.0、Recall@5=0.473039、NDCG@5=0.584694；Redis case Recall@5=0.5/NDCG@5=0.55944，高并发系统设计 Recall@5=0.625/NDCG@5=0.86131，JVM+MySQL+线程池多意图 case Recall@5=0.294118/NDCG@5=0.333333。临时 eval JSONL 已清理。 |
| 已完成 | 长上下文影响测试 | 构造同一 Redis/MySQL 一致性 query 的短上下文与 1418 字长上下文对照。短上下文：Hit@5=1.0、Precision@5=0.4、Recall@5=0.2、NDCG@5=0.360055、召回约 1467ms；长上下文：Hit@5=1.0、Precision@5=0.2、Recall@5=0.1、NDCG@5=0.16958、召回约 1535ms。长上下文把结果更多带向分布式锁/分布式事务/项目泛化题，验证原始 JD+简历全文会稀释精确召回。 |
| 已完成 | 分层上下文第一版 | 已实现面试 RAG 检索短上下文：新增 `InterviewSession.retrieval_resume_text` / `retrieval_jd_text`，启动时从 `interview_profile_json` 构造短检索文本并落库，后续答题检索复用短上下文；`resume_text` / `jd_text` 原文仍保留给评估和出题 Agent。Docker MySQL 已通过 startup DDL 补列。合成 Redis/MySQL case 对照：raw_long Precision@5=0.2、Recall@5=0.1、NDCG@5=0.131205、约 1746ms；layered_profile Precision@5=0.4、Recall@5=0.2、NDCG@5=0.300785、约 1286ms。`python -m py_compile api.py models.py` 与 `pytest tests/test_auth_and_interview_api.py -q` 通过。 |

## 优化建议

### P0-0：分层上下文，避免长 JD/简历稀释 RAG 召回

**现状**

用户上传简历和 JD 后，系统会保存三类内容：

- `source_text`：PDF/JD 原文。
- `parsed_json`：LLM 结构化解析结果。简历对应 `UserInfo`，JD 对应 `JDInfo`。
- `interview_profile_json`：由代码从 `parsed_json` 压缩出的面试画像。简历侧包含 `top_skills`、`project_highlights`；JD 侧包含 `job_title`、`company_name`、`must_have_skills`、`nice_to_have_skills`、`core_responsibilities`、`business_domain`。

但当前模拟面试 RAG 在 `backend\api.py` 中仍把 `resume_document.source_text` 和 `jd_document.source_text` 直接传给 `retriever.search()`。长上下文测试显示，同一 Redis/MySQL 一致性 query 下，约 1418 字上下文会把结果带向分布式锁、分布式事务和项目泛化题，Precision@5、Recall@5、NDCG@5 均低于短上下文。

**目标**

采用“分层上下文”：

1. **Retriever/embedding/rerank 用短上下文**：优先从 `interview_profile_json` 拼 300-500 字检索文本，保证召回聚焦、快且稳定。
2. **面试官 Agent 用适中上下文**：继续保留候选题、当前阶段、历史问答、面试画像和必要项目摘要，用于生成更贴近真实简历的追问。
3. **原文作为兜底/详情来源**：不直接喂给 embedding；只有在画像缺失或出题需要更具体证据时再使用截断后的原文片段。

**建议短 query 格式**

```text
显式查询: <query_text>
目标岗位: <target_role>
目标公司: <target_company>
JD岗位: <job_title>
JD领域: <business_domain>
JD核心技能: <must_have_skills top 10>
JD职责: <core_responsibilities top 5, 每条截断>
简历技能: <top_skills top 10>
简历项目: <project_name + tech_stack + summary，最多 3 个项目，每个 summary 截断到 80 字>
```

**实现位置**

- 在 `backend\api.py` 新增 `_build_interview_retrieval_context(...)` 或同等 helper。
- `start_interview_session`：读取 `resume_document.interview_profile_json` 与 `jd_document.interview_profile_json`，构造 `retrieval_resume_text`、`retrieval_jd_text` 后传给 `retriever.search()`。
- `answer_interview_session`：建议在 `InterviewSession` 中保留原文，同时后续可以增加 `retrieval_resume_text` / `retrieval_jd_text` 缓存字段；第一版可在 session 创建时把短上下文写入 `query` 或在 `current_question_json` metadata 中保留。若不改 DB schema，则先在 session 中保留原文，下一轮检索暂用同一 helper 从已保存画像或 session metadata 取短文本。

**验收**

- 对长上下文 Redis/MySQL 一致性 case，Recall@5 和 NDCG@5 不低于当前短上下文 baseline。
- 对 JVM+MySQL+线程池多意图 case，不因短 query 丢失显式 query 中的 JVM/线程池关键词。
- HTTP `/api/v1/interview/sessions/start` 能正常生成第一题。
- 现有 `tests/test_auth_and_interview_api.py` 通过。

### P0-1：避免异步接口阻塞 event loop

**现状**

`backend\api.py` 中以下接口处在 `async def` 路径，但直接调用同步 RAG/LLM 网络 I/O：

- `POST /api/v1/interview/retrieve`
- `POST /api/v1/interview/sessions/start`
- `POST /api/v1/interview/sessions/{session_id}/answer`

这些路径会同步访问 embedding 服务、Qdrant、rerank API 和出题 Agent，可能阻塞 FastAPI event loop，导致并发请求互相拖慢。

**建议**

短期先把同步调用包到 `asyncio.to_thread(...)`：

- `retriever.search(...)`
- `_pick_interviewer_question(...)`
- `agents.evaluator_agent_evaluate_answer(...)`

中长期再评估迁移到 async Qdrant client、`httpx.AsyncClient`、异步 embedding/rerank 客户端。

**验收**

- 并发请求下接口 P95 延迟不因单个 RAG/rerank 请求显著放大。
- `pytest tests/test_auth_and_interview_api.py` 通过。
- Docker 环境下能完成一次面试 session start 与 answer。

### P0-2：统一 Qdrant 向量命名

**现状**

`backend\scripts\build_qdrant_index.py` 创建 collection 时使用 unnamed vector：

```python
vectors_config=models.VectorParams(size=vector_size, distance=distance)
```

但 `backend\interview\retriever_v2.py` 的 dense 检索优先尝试 named vector：

```python
self.client.query_points(query=query_vector, using="dense", ...)
self.client.search(query_vector=models.NamedVector(name="dense", vector=query_vector), ...)
```

失败后才 fallback 到 unnamed vector。正常路径可能先触发异常再降级，增加延迟并掩盖真实配置问题。

**建议**

二选一并全链路统一：

1. 推荐：索引脚本创建 named vector `dense`，检索也只使用 `dense`。
2. 保守：索引和检索都改为 unnamed vector，删除 named vector fallback。

若采用 named vector，需要同步更新 collection 维度校验逻辑、manifest 字段和重建说明。

**验收**

- 新建 collection 后，dense 查询不再依赖异常 fallback。
- Qdrant collection schema 与 retriever 查询参数一致。
- `build_qdrant_index.py --recreate` 后 `run_retriever_v2.py` 能查到结果。

### P0-3：检索和 rerank 失败必须可观测

**现状**

RAG 链路存在多处静默降级：

- dense 检索异常后返回空结果。
- rerank API 异常后返回 `None`。
- 答题后下一题检索异常后直接 `candidate_questions = []`。

用户仍可能拿到默认题或 LLM 生成题，但线上无法判断 RAG 是否已退化。

**建议**

引入结构化日志与最小指标：

- Qdrant 查询耗时、命中数、异常类型。
- embedding 调用耗时、异常类型。
- rerank 调用耗时、候选数、异常类型、是否超时。
- dense/lexical/rerank/fallback 命中路径。
- 每轮面试是否使用了 fallback question。

避免宽泛 `except Exception: return []`；若必须降级，至少打 warning，并把降级原因写入 score breakdown 或内部日志。

**验收**

- 断开 Qdrant 或关闭 rerank key 时，接口仍可按设计降级，但日志中能明确看到原因。
- 正常请求日志能看到 dense/lexical 命中数量和 rerank 是否生效。

### P0-4：修正缺失分支的分数归一化

**现状**

`backend\interview\retriever_v2.py` 的 `_normalize_scores` 在所有值相等时返回全 1：

```python
if maximum - minimum < 1e-12:
    return [1.0] * len(values)
```

当某个分支完全没命中并以 0 填充时，该分支会被归一化成满分，可能误导最终融合排序。

**建议**

区分“真实 0 分”和“该分支未召回”：

- dense/lexical rank 缺失时使用 `None` 表示。
- 只对该分支实际命中的候选做 min-max 归一化。
- 未命中分支得分为 0，或按可用分支动态重分配权重。

**验收**

- 构造 dense 全空、lexical 有命中的 fixture，最终排序主要由 lexical 决定。
- 构造 lexical 全空、dense 有命中的 fixture，最终排序主要由 dense 决定。
- 所有分支都空时返回空或明确 fallback，不产生伪高分。

### P0-5：缓存会话内候选池和重复计算结果

**现状**

面试开始和每轮答题后都会用相同的 resume/JD/query 再次调用 retriever。默认 `INTERVIEW_TOP_K` 最多 20，每轮可能重复触发 embedding、Qdrant 和 rerank。

**建议**

分两层缓存：

1. 会话级候选池：面试开始时检索一次较大的 candidate pool，写入 `InterviewSession` 或 Redis；后续轮次按历史题过滤、重排和补充。
2. 计算级缓存：缓存 `query -> embedding`、`(query, candidate, rerank_model) -> rerank_score`，key 使用稳定 hash，例如 `sha256(model + query + document)`。

**验收**

- 同一个 session 的后续轮次不会重复生成相同 query embedding。
- rerank 调用次数显著减少。
- 历史已问题仍不会重复出现。

### P1-1：结构化 query embedding 输入，降低长文本噪声

**现状**

`compose_query_embedding_text` 直接拼接：

```python
extra_query + jd_text + resume_text
```

长简历和 JD 会稀释目标岗位、核心技能、项目亮点等关键信号，也可能触及 embedding 服务的 token 限制。

**建议**

使用结构化、短文本 query：

- 目标公司、目标岗位、JD title。
- JD must-have skills / responsibilities。
- 简历 top skills。
- 代表项目名称和技术栈。
- 当前面试阶段或 follow-up hint。

可复用已解析的 `UserInfo`、`JDInfo`，避免每次从原始长文本中猜测重点。

**验收**

- 典型长简历 + 长 JD 请求不超 embedding 输入限制。
- 对相同岗位 query，TopK 更聚焦于目标技术栈和题型。

### P1-2：把 rubric 字段带入题库 payload 和评估链路

**现状**

README 描述面试题可携带 `expected_points` / `bad_signals`，`agents.evaluator_agent_evaluate_answer` 也支持这些字段。但当前 `RetrievalQuestion`、`build_payload`、`serialize_retrieved_question` 主要只保留题目元数据，导致 evaluator 多数情况下拿不到真正 rubric。

**建议**

扩展题库 schema 与 Qdrant payload：

- `expected_points`
- `bad_signals`
- `difficulty`
- `track`
- `tags`
- `source_url` 或来源标识（如允许）

同步更新：

- `backend\interview\lexical_retriever.py`
- `backend\interview\retriever_v2.py`
- `backend\scripts\build_qdrant_index.py`
- API 序列化响应
- 测试 fixture

**验收**

- 检索返回的题目包含 rubric 字段。
- 答案评估时能逐条输出 rubric hit/miss。
- 无 rubric 的旧题仍兼容。

### P1-3：优化词法召回性能

**现状**

`LexicalRetriever.search` 对所有题目逐条计算 BM25。题库小规模时可接受，但题库增长后会线性变慢。

**建议**

按数据规模选择：

- 小规模：保留当前实现，但初始化时预构建倒排表，查询时只遍历命中 token 的候选文档。
- 中规模：使用成熟 BM25 库。
- 大规模：引入 Qdrant sparse vector 或 Elasticsearch/OpenSearch，把 sparse 分支服务化。

**验收**

- 在目标题库规模下，词法召回 P95 可控。
- dense + lexical 总耗时低于面试交互可接受阈值。

### P1-4：改进 metadata filter 和业务匹配策略

**现状**

metadata 严格过滤只作用于 dense 分支，并且是 exact match。公司和岗位字段常见别名或表述差异，例如“后端开发”“Java后端”“服务端开发”，严格匹配容易漏召回；同时 lexical 分支仍可能混入不匹配公司/岗位的题。

**建议**

- 标准化 company/role 字段，维护 alias 或 normalized 字段。
- 优先使用软过滤 + boost，而不是默认硬过滤。
- 若 strict filter 无结果，自动放宽并记录 fallback reason。
- 最终输出阶段也做可配置的公司/岗位约束。

**验收**

- target_role 为“Java后端”时，可以命中“后端开发”“Java开发”等近义角色。
- strict 模式无结果时有明确降级行为和日志。

### P1-5：为 Qdrant payload 创建索引

**现状**

Qdrant collection 当前只创建 vector config，没有为常用 payload 字段创建索引。未来按 `company`、`role`、`question_type`、`publish_time` 过滤或排序时会受影响。

**建议**

在 `backend\scripts\build_qdrant_index.py` 中创建 payload index：

- `company`
- `role`
- `question_type`
- `publish_time`
- 可选：`normalized_key`、`track`、`tags`

数据量上来后再评估 HNSW 参数、on-disk vectors、quantization。

**验收**

- collection recreate 后 payload schema 可见。
- strict metadata filter 查询可使用 payload index。

### P1-6：建立可复现的 RAG 评测样本和回归流程

**现状**

`backend\scripts\evaluate_retriever_v2.py` 已支持 Hit@K、Precision@K、Recall@K、MRR、MAP、NDCG、多样性、时效性、公司岗位命中等指标，但仓库中未看到可复现的脱敏 eval cases。

**建议**

新增一小批脱敏 JSONL case，覆盖：

- 后端基础题
- 项目/系统设计题
- coding 题
- behavioral 题
- 公司/岗位匹配
- 长简历 + 长 JD
- 无 Qdrant 或 rerank disabled 的降级场景

把评测命令写入 README 或 scripts README；调融合权重前后必须对比报告。

**验收**

- 本地和 Docker 环境都能运行固定评测命令。
- 每次调整 retriever 权重或过滤策略，都能产出对比报告。

## Docker 环境测试建议

可以在 Docker 环境中测试 RAG。推荐测试分三层：

1. **服务连通性**：启动 `qdrant`、`backend`，确认 backend 能访问 Qdrant。
2. **索引构建**：在 backend 容器内运行 `backend\scripts\build_qdrant_index.py` 对题库 JSONL 建索引。
3. **检索验证**：运行 `backend\scripts\run_retriever_v2.py` 或调用 `POST /api/v1/interview/retrieve`，验证 dense、lexical、rerank、fallback 路径。

执行前需要确认：

- Docker Desktop / Docker Engine 正在运行。
- `.env` 中 embedding/rerank 相关配置可用。
- `data\nowcoder\pipeline_runs_llm\canonical\long_content_retrieval_questions.jsonl` 存在，或指定 `RETRIEVER_DATASET_PATH`。
- 如要重建索引，确认可以安全使用 `--recreate`。
