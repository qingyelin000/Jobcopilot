# backend/scripts 说明

本文档说明 `backend/scripts` 下各脚本的作用、输入输出与使用场景。

## 脚本清单

| 文件 | 作用 | 典型输入 | 典型输出 |
|---|---|---|---|
| `crawl_nowcoder_interviews.py` | 从牛客公开接口抓取面经帖子（搜索 + 详情），并规范化为基础 JSONL。 | 查询词（`--query`）与分页参数 | `<run_dir>/crawl/json/*.json`、`<run_dir>/crawl/normalized/interview_posts.jsonl` |
| `preclean_nowcoder_for_llm.py` | 轻清洗 crawl 产物，去掉无关字段/噪声，生成给 LLM 的帖子级输入。 | `crawl/normalized/interview_posts.jsonl` | `preclean/interview_posts_for_llm.jsonl`、`preclean/interview_posts_for_llm_manifest.json` |
| `llm_extract_nowcoder_questions.py` | 调用 `mimo-v2-flash`（OpenAI 兼容）做结构化抽取，输出 post 级结构化结果与 question 级检索候选。 | `preclean/interview_posts_for_llm.jsonl` | `llm/structured_posts.jsonl`、`llm/retrieval_questions.jsonl`、`llm/error_posts.jsonl` |
| `merge_retrieval_questions.py` | 将“新产出检索题库”与“历史检索题库”做增量合并去重。 | 旧库 + 新库 | 合并后的 JSONL 与合并报告（manifest） |
| `quality_gate_questions.py` | 对检索题库执行质量门槛检查（总量、重复率、类型分布等）。 | 检索题库 JSONL + 质量配置 JSON | 质量报告 JSON，失败时返回非 0 退出码 |
| `run_nowcoder_llm_pipeline.py` | 全自动 LLM 总控：`crawl -> preclean -> llm_extract -> merge -> quality_gate -> promote`。 | 抓取关键词（可选）+ OpenAI 兼容环境变量 | `data/nowcoder/pipeline_runs_llm/<run_id>/...` 全链路产物 |
| `evaluate_retriever_v2.py` | 离线评测 `RetrieverV2` 指标（相关性 + 质量维度）。 | 人工标注用例 JSONL（query + 相关题ID/相关度 + 公司岗位 + 简历关键词） | 评测报告 JSON（summary + per_case） |
| `build_qdrant_index.py` | 从 canonical 题库构建/刷新 Qdrant 向量索引。 | canonical JSONL + Qdrant 参数 + embedding 参数 | Qdrant collection + 索引 manifest |
| `run_retriever_v2.py` | 运行 `RetrieverV2`（Qdrant 向量召回 + 内置词法召回融合 + 质量重排）。 | 简历/JD 文本 + Qdrant 参数 + embedding 参数 | 控制台输出检索结果 JSON |

## 选择建议

| 需求 | 推荐脚本 |
|---|---|
| 只抓取原始数据并做初步规范化 | `crawl_nowcoder_interviews.py` |
| 按 LLM 新链路跑“轻清洗 + 抽取” | `preclean_nowcoder_for_llm.py` + `llm_extract_nowcoder_questions.py` |
| 从抓取到发布全链路自动化 | `run_nowcoder_llm_pipeline.py` |
| 查看检索效果是否符合预期 | `run_retriever_v2.py` |
| 量化检索质量并对比版本效果 | `evaluate_retriever_v2.py` |
| 构建向量索引并验证混合检索 | `build_qdrant_index.py` + `run_retriever_v2.py` |

## 注意事项

1. `run_nowcoder_llm_pipeline.py` 默认会在质检通过后覆盖 canonical 题库；可用 `--no-promote` 关闭发布。
2. `merge_retrieval_questions.py` 默认同分时优先新数据；可用 `--prefer-existing` 改为优先旧数据。
3. 质量阈值由 `backend/config/question_quality_gate.json` 控制。

4. crawl_nowcoder_interviews.py 默认关键词已覆盖 Java/后端/算法/AI应用开发/大模型算法/大模型开发/测试开发/前端，可通过 --query（或全流程脚本的 --crawl-query）覆盖默认值。
5. Manual publish override: use `--manual-promote-on-fail` in `run_nowcoder_llm_pipeline.py`; when quality gate fails, the script will ask y/n in terminal before promoting.
6. 新 LLM 总控默认运行目录为 `data/nowcoder/pipeline_runs_llm/`，且默认抓取上限配置为 1200、最少抓取记录阈值为 1000（可通过 `--crawl-max-items` 与 `--crawl-min-records` 调整）。
7. 新 LLM 链路默认模型为 `mimo-v2-flash`，默认读取 `MIMO_BASE_URL` 与 `MIMO_V2_PRO_API_KEY`（并兼容 `OPENAI_BASE_URL`、`DEEPSEEK_API_KEY` 兜底）。
8. 如需只用本次 LLM 数据覆盖题库（不并入旧 canonical），可在 `run_nowcoder_llm_pipeline.py` 加 `--llm-only`；并可通过 `--merge-drop-field source_url`（默认已开启）在入库前去掉无检索价值字段。
9. canonical 默认路径已迁移到 `data/nowcoder/pipeline_runs_llm/canonical/`。
10. `RetrieverV2` 默认需要可访问的 Qdrant（默认 `http://localhost:6333`）和已构建 collection（默认 `nowcoder_interview_questions_v1`）。
11. `embedding_provider=hash` 可离线快速打通；`embedding_provider=local_bge` 可用本地 `BAAI/bge-m3` 提升语义效果且无 API 成本（索引阶段会写入 dense+sparse，检索阶段走双路召回）；`embedding_provider=openai_compatible` 可接入在线 embedding 接口。

## 检索评测样例（evaluate_retriever_v2）

`--cases` 文件为 JSONL，每行一个评测样本，最小示例：

```json
{"case_id":"c1","query":"Java 后端 Redis 一致性","target_company":"快手","target_role":"后端开发","resume_keywords":["Redis","一致性","分布式锁"],"relevant_question_ids":["lc_001","lc_077"]}
{"case_id":"c2","query":"高并发系统设计","target_company":"字节跳动","target_role":"后端开发","resume_keywords":["限流","缓存","系统设计"],"judgments":[{"question_id":"lc_210","relevance":2},{"question_id":"lc_311","relevance":1}]}
```

仓库里也提供了可直接改的样例文件：`data/nowcoder/eval/retriever_cases.sample.jsonl`。

新增质量指标：

- `dup_rate_at_k`：重复率（越低越好）
- `diversity_at_k`：多样性（越高越好）
- `freshness_at_k`：时效性（越高越好，按 `publish_time` 衰减）
- `company_role_match_at_k`：同公司同岗位匹配度（越高越好）
- `top3_company_role_hit_rate`：前 3 命中“同公司同岗位”的比例
- `resume_alignment_at_k`：与简历/JD/查询词锚点契合度（越高越好）
- `quality_score`：综合质量分（0~1）

命令示例：

```powershell
C:\Users\32014\miniconda3\python.exe backend\scripts\evaluate_retriever_v2.py `
  --cases data\nowcoder\eval\retriever_cases.jsonl `
  --dataset data\nowcoder\pipeline_runs_llm\canonical\long_content_retrieval_questions.jsonl `
  --top-k 8 `
  --freshness-half-life-days 180 `
  --output data\nowcoder\pipeline_runs_llm\eval\retriever_v2_eval_report.json
```

对比评测 `RetrieverV2`：

```powershell
C:\Users\32014\miniconda3\python.exe backend\scripts\evaluate_retriever_v2.py `
  --cases data\nowcoder\eval\retriever_cases.jsonl `
  --dataset data\nowcoder\pipeline_runs_llm\canonical\long_content_retrieval_questions.jsonl `
  --qdrant-url http://localhost:6333 `
  --qdrant-collection nowcoder_interview_questions_v1 `
  --embedding-provider local_bge `
  --embedding-model BAAI/bge-m3 `
  --top-k 8 `
  --freshness-half-life-days 180 `
  --output data\nowcoder\pipeline_runs_llm\eval\retriever_v2_eval_report.json
```

## Qdrant 向量检索（RetrieverV2）

1) 构建索引（本地 bge-m3 向量）

```powershell
C:\Users\32014\miniconda3\python.exe backend\scripts\build_qdrant_index.py `
  --dataset data\nowcoder\pipeline_runs_llm\canonical\long_content_retrieval_questions.jsonl `
  --qdrant-url http://localhost:6333 `
  --collection nowcoder_interview_questions_v1 `
  --embedding-provider local_bge `
  --embedding-model BAAI/bge-m3 `
  --embedding-dimension 384 `
  --recreate `
  --output-manifest data\nowcoder\pipeline_runs_llm\qdrant\index_manifest.json
```

2) 运行混合检索

```powershell
C:\Users\32014\miniconda3\python.exe backend\scripts\run_retriever_v2.py `
  --resume-file data\resume.txt `
  --jd-file data\jd.txt `
  --top-k 8 `
  --qdrant-url http://localhost:6333 `
  --collection nowcoder_interview_questions_v1 `
  --embedding-provider local_bge `
  --embedding-model BAAI/bge-m3 `
  --target-company 快手 `
  --target-role 后端开发
```

