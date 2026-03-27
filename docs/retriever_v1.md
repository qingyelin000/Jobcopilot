# Retriever V1

## 1. 为什么先做这一版

`retriever v1` 不是最终形态，而是模拟面试能力的第一版检索原型。

先做这一版有三个目的：

1. 验证题库是否已经足够支撑“按简历 + JD 选题”。
2. 验证检索策略是否合理，而不是一上来先堆向量数据库和更重的 embedding 模型。
3. 给后续“大模型充当面试官”提供一个稳定的候选题来源，避免模型完全凭空出题。

当前阶段我们最需要回答的问题不是：

- 用哪种向量数据库
- embedding 模型要不要一步到位上更大版本

而是：

- 现有题库能不能召回相关题
- `简历 + JD` 这种输入方式是否有效
- 返回的 `top-k` 候选题是否足够像真实面试会问的问题

所以 `retriever v1` 的定位是：

> 用最小成本验证 mock interview 的检索链路是否成立。

## 2. 为什么暂时不用 embedding / 向量数据库

不是后面不做，而是当前不急着做。

原因：

1. 题库还小，当前高质量题目只有几十条，先做纯 Python 检索更容易快速验证。
2. 检索效果的主要风险还在“数据和策略”，不在“存储介质”。
3. 先跑通上层接口，后面再把底层替换成 embedding 检索，迁移成本更低。

换句话说：

- `retriever` 是策略层
- `embedding` / `vector db` 是实现层

当前先把策略层做对。

## 3. 这一版具体做什么

数据源：

- [`data/nowcoder/clean/long_content_retrieval_questions.jsonl`](/D:/study%20resources/Jobcopilot/data/nowcoder/clean/long_content_retrieval_questions.jsonl)

输入：

- 用户简历文本
- 岗位 JD 文本
- 可选的额外查询文本

输出：

- `top-k` 候选面试题
- 每题的分数
- 命中的关键词
- 评分拆解

## 4. 当前实现策略

`retriever v1` 采用纯 Python 的轻量混合评分：

1. `BM25` 风格的词项匹配分
2. 技术关键词命中加分
3. 岗位字段命中加分
4. 题型偏好加分
5. 公司显式命中加分
6. 结果多样性控制，避免 top-k 被同一类基础题刷满

它不是“智能到最终可上线”的版本，而是一个可解释、可调试、可快速迭代的版本。

这样做的价值是：

- 当结果不对时，可以看见是哪一部分打分出了问题
- 可以快速修改规则和字段，而不需要先训练或部署更复杂的向量链路

## 5. 成功标准

这一版主要看下面几个问题能不能回答清楚：

1. 给一份 Java 后端简历和 AI 应用开发 JD，召回的题是否明显偏后端 / 项目 / 系统设计。
2. 给一份偏 LLM / Agent / RAG 的 JD，是否能优先召回相关题，而不是随机基础题。
3. 同一份输入多次运行，结果是否稳定。
4. 检索结果是否足够好，能直接交给大模型面试官选题和追问。

如果这四点大致成立，下一步才值得引入 embedding。

## 6. 下一阶段怎么升级

当 `retriever v1` 跑通后，再进入下一版：

1. 引入 embedding 模型，例如 `BAAI/bge-small-zh-v1.5`
2. 做稠密召回
3. 保留 metadata 过滤和规则加分
4. 逐步过渡到“关键词 + embedding”的混合检索

到那时，`retriever` 的上层接口不变，只替换底层召回实现。

## 7. 代码位置

- 检索模块：
  [`backend/interview/retriever_v1.py`](/D:/study%20resources/Jobcopilot/backend/interview/retriever_v1.py)
- 运行脚本：
  [`backend/scripts/run_retriever_v1.py`](/D:/study%20resources/Jobcopilot/backend/scripts/run_retriever_v1.py)
