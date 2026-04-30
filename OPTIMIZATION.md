# JobCopilot 项目优化建议

> 适用版本：当前 `main` 分支快照（backend FastAPI + 前端 Vite/React + Docker Compose）。
> 本文档基于对仓库的静态阅读，按"风险/收益"排序，并给出具体改造方向与原因。优先级：🔴 高｜🟡 中｜🟢 低。

---

## 1. 工程结构与模块化

### 🔴 1.1 拆分巨型模块 `api.py` (68KB) 与 `agents.py` (53KB)
- **现状**：`backend/api.py` 单文件包含 18 个路由 + 限流逻辑 + Redis 客户端 + 鉴权依赖 + Pydantic Schema + Startup Hook + PDF 解析；`agents.py` 包含 52 个函数（LLM 客户端构造、Prompt 模板、解析、评估、Mapping、Rewrite 全部混在一起）。
- **原因**：
  - 单文件超过 1500 行后，IDE 跳转、Code Review、合并冲突、单元测试隔离都会显著恶化。
  - 路由、限流、领域服务、数据访问没有分层，新人无法只读"用户域"或"面试域"，必须扫全文。
  - 目前 `from db import …`、`from models import …`、`import agents` 在文件顶部，循环依赖风险高，未来拆分成本会越来越大。
- **建议**：
  ```
  backend/
    app/
      main.py                # FastAPI 实例 + lifespan + middleware
      core/
        config.py            # pydantic-settings 集中环境变量
        security.py          # JWT、密码哈希
        rate_limit.py        # Redis + local fallback
      api/v1/
        auth.py              # /auth/*
        users.py             # /users/me/*
        resumes.py           # /resumes/*
        jds.py               # /jds/*
        process.py           # /process/*
        interview.py         # /interview/sessions/*
        chat.py
      services/
        resume_service.py    # 业务编排（调用 agents + DB）
        interview_service.py
      agents/
        clients.py           # OpenAI/Deepseek/Mimo client factory
        resume_parser.py
        jd_parser.py
        rewriter.py
        interviewer.py
      db/
        base.py, session.py, models/  # 一表一文件
        migrations/          # Alembic
      schemas/               # Pydantic 按领域拆分
  ```
- **收益**：可单独为 `services` 写单测；上线时只重新构建受影响层；新功能 PR 不再触碰 4000 行的 `api.py`。

### 🟡 1.2 配置集中化（`pydantic-settings`）
- **现状**：`api.py` 里手写了 `_safe_int_from_env`、`_safe_float_from_env`、`_feature_enabled` 三个解析函数，并散落 20+ 个 `os.getenv()` 调用；`db.py`、`auth.py`、`agents.py` 又各自再读一次。
- **原因**：
  1. 同一个 env（如 `RATE_LIMIT_WINDOW_SECONDS`）在多处出现时，单元测试很难 mock。
  2. 启动时无法一次性校验配置——错配的 env 要等运行时第一个请求才报错。
  3. `JWT_SECRET` 默认值是 `"jobcopilot-dev-secret"`，生产若忘配会静默使用，存在严重安全风险（详见 §4.2）。
- **建议**：
  ```python
  # core/config.py
  from pydantic_settings import BaseSettings, SettingsConfigDict

  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", extra="ignore")
      database_url: str
      jwt_secret: str               # 必填，无默认
      jwt_expire_minutes: int = 10080
      rate_limit_window_seconds: int = 60
      ...

  settings = Settings()             # 启动失败 = 配置不全
  ```

---

## 2. 数据库与持久化层

### 🔴 2.1 引入 Alembic，删掉 `_ensure_user_profile_columns` 这种"DDL 补丁"
- **现状**：`api.py:84-103` 在 startup 中用 `inspect()` + 原生 `ALTER TABLE` 给 `users` 表"自动加列"；同时启动时还在调用 `Base.metadata.create_all()`。
- **原因**：
  - `create_all` 只会"建表不存在的表"，对已有表的列变更完全无感——这就是为什么不得不手写 ALTER 补丁。
  - 这种模式无法回滚、无法做数据迁移（比如重命名列、回填数据）；多实例同时启动会出现并发 DDL 竞态。
  - 一旦字段类型/约束需要变（例如 `source_text` 改 `MEDIUMTEXT`），又要再补一段 if-else，长期不可维护。
- **建议**：接入 Alembic，`alembic init`，把现有表生成 baseline，后续所有 schema 变化都走 `alembic revision --autogenerate`。CI 里加一步 `alembic upgrade head` 验证 migration 可重放。

### 🔴 2.2 SQLAlchemy 引擎参数与会话生命周期
- **现状**：`db.py` 只配了 `pool_pre_ping=True`，没有 `pool_size` / `max_overflow` / `pool_recycle`。MySQL 默认 `wait_timeout=28800s`，连接被服务端断开后再用就报错；高并发时默认池只有 5 + 10。
- **建议**：
  ```python
  engine = create_engine(
      DATABASE_URL,
      pool_pre_ping=True,
      pool_size=10,
      max_overflow=20,
      pool_recycle=1800,      # 防 MySQL 断开
      pool_timeout=30,
      future=True,
  )
  ```

### 🟡 2.3 异步化 DB 访问
- **现状**：FastAPI 异步路由里使用同步 `Session`（`SessionLocal`）。每次 DB 调用会阻塞事件循环线程，限制了并发，尤其叠加 LLM 长耗时请求时更明显。
- **建议**：换 `sqlalchemy.ext.asyncio.AsyncSession` + `aiomysql`/`asyncmy`；或者明确把 DB 调用包到 `run_in_threadpool`。两者择一即可，但不要混用。

### 🟢 2.4 大文本字段类型
- `ResumeDocument.source_text` 用 `Text`（MySQL 64KB）；用户上传长简历或 JD 时容易截断。建议显式 `Text(length=4_000_000)` 或 `LONGTEXT`。同理 `parsed_json`、`evaluation_json` 用 `JSON` 时确认 MySQL 8.x 已开启正确编码。

---

## 3. API 与依赖注入

### 🟡 3.1 用 `lifespan` 替换 `@app.on_event`
- `@app.on_event("startup"/"shutdown")` 已被 FastAPI 标记 deprecated，未来版本会移除。改成：
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      Base.metadata.create_all(bind=engine)  # 暂时保留，迁 Alembic 后删
      yield
      await _reset_rate_limit_redis_client(0.0)

  app = FastAPI(lifespan=lifespan)
  ```

### 🟡 3.2 限流模块抽离 + 增加 Lua 原子操作
- 当前 Redis 限流用 `INCR + EXPIRE` 两步，第一步成功、第二步失败会留下"永不过期 Key"。建议改成 Redis Lua 脚本或 `SET key 0 EX window NX` + `INCR` 模式，单原子。
- 同时把限流封装为 `Depends(RateLimiter("chat", user_per_window=20))`，让路由声明式声明，避免在每个 endpoint 顶部再写一遍 `_enforce_rate_limit(...)`。

### 🟢 3.3 OpenAPI/响应模型一致性
- 部分 endpoint 返回 `dict`，前端 `client.ts` 自己定义 TS 类型（`shared/api/types.ts` 5KB）。建议在后端用 `response_model=` 约束所有路由，然后用 `openapi-typescript` 自动生成前端类型，消除"两边手抄"漂移。

---

## 4. 安全

### 🔴 4.1 移除内置默认 `JWT_SECRET`
- `auth.py:7` `SECRET_KEY = os.getenv("JWT_SECRET", "jobcopilot-dev-secret")`。一旦生产环境忘记设置该变量，所有 JWT 都用公开默认值签发，**任何人都可以伪造任意用户登录**。
- **建议**：启动时强制校验（在 `Settings` 中声明 `jwt_secret: str` 不带默认值），缺失则启动失败。同时把 token 过期默认值从 7 天（10080 分钟）调小或拆成 access+refresh。

### 🔴 4.2 `data/`、`debug.log`、QA 目录治理
- 仓库根存在 `debug.log`（看起来是 Chromium crashpad 输出，与本项目无关），但已被 git 跟踪。建议：
  1. 删除 `debug.log` 并在 `.gitignore` 里加上。
  2. `data/`、`docs/`、`QA/` 已 ignore，但请确认本地 `.env` 不会因开发误操作 `git add -f` 提交（CI 加一个 secrets scan，例如 `gitleaks`）。

### 🟡 4.3 CORS 收紧
- `allow_methods=["*"], allow_headers=["*"]` + `allow_credentials=True`。生产应明确只 allow 实际用到的方法和 header，避免未来出现新接口被跨站滥用。

### 🟡 4.4 PDF / 上传文件硬性限制
- `nginx.conf` 仅 `client_max_body_size 20m`；后端 `_extract_text_from_pdf_bytes` 没有页数与字符上限，恶意 PDF（高页数/嵌套对象）会让 PyPDF2 占满 worker。
  - 加 `max_pages`、`max_chars`、`max_filesize_kb` 校验。
  - 解析放进 `run_in_executor`，并设超时；大文件考虑用 `pdfminer.six` 或 `pypdfium2`。

### 🟢 4.5 bcrypt 版本
- `requirements.txt` 钉了 `bcrypt==3.2.2`，是因为 passlib 1.7 与 bcrypt 4.x 的兼容性问题。可以改用 `argon2-cffi` + `passlib[argon2]`（Argon2id），或升级到 `passlib==1.7.5` + `bcrypt>=4`，并在测试中验证 hash/verify 通过。

---

## 5. 可观测性

### 🔴 5.1 引入结构化日志（目前 0 处 `logging`）
- `backend/api.py` 与 `agents.py` 都没有 `import logging`，主流程出错只会冒到 uvicorn 默认 stderr。线上排障难度极高。
- **建议**：
  ```python
  import logging, sys
  logging.basicConfig(
      level=os.getenv("LOG_LEVEL", "INFO"),
      format='%(asctime)s %(levelname)s %(name)s %(message)s',
      stream=sys.stdout,
  )
  ```
  关键路径（LLM 调用、Redis 失败、限流命中、JWT 解码失败、DB 异常）打 INFO/WARN/ERROR；用 `structlog` 输出 JSON，Docker 端通过 stdout 采集。

### 🟡 5.2 Tracing & Metrics
- 既然是多 Agent + RAG + 外部 LLM/Embedding/Rerank/Qdrant/MySQL/Redis 的复杂链路，**强烈建议接入 OpenTelemetry**：
  - `opentelemetry-instrumentation-fastapi`、`...sqlalchemy`、`...redis`、`...httpx`，外加 LangSmith 或自建 Phoenix 看板观察 LLM 调用 token & latency。
  - Prometheus 指标导出 `/metrics`（每个限流分桶命中数、LLM 平均耗时、Qdrant 召回耗时）。

### 🟢 5.3 Healthcheck 完整化
- backend 容器在 `docker-compose.yml` 没有 `healthcheck`；前端 nginx 也没有 `/healthz`。增加 `GET /healthz`（DB ping + Redis ping + Qdrant ping）后，K8s/ECS 健康探针、自动重启、优雅滚动才能正确工作。

---

## 6. LLM 与 RAG 工作流

### 🟡 6.1 `agents.py` 集中管理 Prompt 与重试
- 52 个函数硬编码了模型名（`deepseek-chat`、`mimo-v2-pro`）和温度。建议：
  - Prompt 抽到 `prompts/` 目录，用 `jinja2`/`langchain.prompts` 模板化；可以做 A/B 与版本回滚。
  - 所有 LLM 调用走统一封装（重试、超时、token 计数、失败降级到备用模型），目前看起来每个函数自己 try/except，错误响应不统一。

### 🟡 6.2 Embedding / Rerank 调用并发与缓存
- `INTERVIEW_TOP_K=20`，每轮面试都会触发 embedding 与 rerank。建议对 `query → embedding`、`(query, candidate) → rerank_score` 做 LRU 内存缓存或 Redis 缓存（key 用 sha256(query+model)），缩短二次召回延时。

### 🟡 6.3 LangGraph 状态机
- README 说"Phase 2-LangGraph"未完成，但 `requirements.txt` 中也没看到 `langgraph` 依赖。如果阶段已落地，建议补依赖；如未落地，README 应同步勾选状态。

---

## 7. 测试与 CI

### 🔴 7.1 测试覆盖严重不足
- 仅 `tests/test_auth_and_interview_api.py` 12KB + `conftest.py` 1.7KB。`agents.py`（核心 LLM 编排）、`interview/retriever_v2.py`（26KB）、`document_assets.py`（22KB）几乎无单测。
- **建议**：
  - 给 `agents.py` 的纯函数（schema 验证、prompt 拼接、JSON 解析）加快速单元测试，LLM 网络部分用 `respx`/`pytest-mock` 打桩。
  - `retriever_v2` 用 fixture 数据 + 内存 Qdrant（或 mock）跑 smoke。
  - 已有 `locustfile.py`，可在 CI 跑 baseline，输出 P95 趋势。

### 🔴 7.2 没有 CI 配置
- 仓库内未见 `.github/workflows/`。建议至少加：
  - `lint.yml`：`ruff` + `mypy`（严格模式可逐步开）+ `eslint` + `tsc --noEmit`。
  - `test.yml`：`pytest -q`、前端 `npm run build`。
  - `docker.yml`：构建并推到 GHCR/阿里云镜像仓库；用 `docker buildx` 缓存层。
  - PR 必须绿灯才能合并。

### 🟡 7.3 依赖锁定
- `requirements.txt` 大多没钉版本（`fastapi`、`sqlalchemy`、`pydantic` 都没指定）。同一份 Dockerfile 不同时间构建会得到不同依赖树，难复现 bug。
- 建议改用 [`uv`](https://github.com/astral-sh/uv) 或 `pip-tools` 生成 `requirements.lock`，CI 用 `pip install --require-hashes`。
- 前端 `package.json` 也没有 `package-lock.json`/`pnpm-lock.yaml` 提交（注意：在 Dockerfile 里 `COPY package.json` 后 `npm install` 会拉到飘忽版本）。建议提交 `package-lock.json` 并改用 `npm ci`。

---

## 8. Docker / 部署

### 🟡 8.1 backend Dockerfile 多阶段 + 非 root
- 当前单阶段 + root 用户，且 `requirements.txt` 不分层（每次代码变更都会重装依赖）。建议：
  ```dockerfile
  FROM python:3.12-slim AS builder
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

  FROM python:3.12-slim
  RUN useradd -m -u 1000 app
  COPY --from=builder /install /usr/local
  COPY --chown=app:app . /app
  USER app
  WORKDIR /app
  HEALTHCHECK --interval=30s CMD curl -f http://127.0.0.1:8000/healthz || exit 1
  CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- 还可以从 `python:3.10-slim` 升到 `3.12-slim`（社区 wheel 更全），并切到 `gunicorn -k uvicorn.workers.UvicornWorker -w 4`。

### 🟡 8.2 dev compose 与 prod compose 区分
- `docker-compose.yml` 里 backend 用 `--reload` 并把 `./backend:/app` 挂进容器——这是开发模式。请确认 `docker-compose.prod.yml` 没有同样配置（mount 源码到生产是常见事故）。
- 建议命名为 `docker-compose.dev.yml`，并默认让 README 用 `docker compose -f docker-compose.dev.yml up`，避免误解。

### 🟢 8.3 前端 Dockerfile 优化
- 现在 `COPY package.json … src` 一次性复制后 `npm install`，缓存命中差。建议：
  ```dockerfile
  COPY package.json package-lock.json /app/
  RUN npm ci --legacy-peer-deps
  COPY . /app
  RUN npm run build
  ```
  这样改 src 不会触发 npm 重装。

---

## 9. 前端

### 🟡 9.1 大文件拆分
- `frontend/src/app/styles.css` 48.8KB、`features/resume/ResumePage.tsx` 32.8KB、`features/profile/ProfilePage.tsx` 20.4KB。
- styles 建议拆按页面/组件 + 引入 CSS Modules 或 Tailwind；TSX 单页超过 500 行后强烈建议拆 hooks（`useResumeUpload`、`useResumeForm`）+ 子组件。

### 🟡 9.2 数据请求层
- `client.ts` 是手写 `fetch` 包装。已经依赖 `@tanstack/react-query`，但缺少：
  - 401 自动重试/触发登出。
  - `AbortController` 取消（输入即时搜索时浪费请求）。
  - 全局错误 toast。
  - 类型自动同步（见 §3.3）。

### 🟢 9.3 React 19 + Router 7 注意事项
- 依赖较新（`react@19`、`react-router-dom@7`），建议在 README 写明 Node ≥ 20，否则 `vite@7` 在 Node 18 上行为不一致。

---

## 10. 文档与流程

### 🔴 10.1 README 与实际不符
- README 写"前端使用 Streamlit"，但实际仓库是 React + Vite + nginx；端口 8501 对应的也已是前端 nginx 而非 Streamlit。会严重误导新接入的开发者。
- 建议：
  - 更新技术栈描述。
  - 补一张架构图（FastAPI ↔ MySQL/Qdrant/Redis ↔ DeepSeek/Mimo/SiliconFlow）。
  - 列清所有环境变量（建议用表格 + 必填/可选 + 默认值）。
  - 补 `make dev`、`make test`、`make lint` 速查。

### 🟢 10.2 ADR / 决策记录
- 像"为什么用 Qdrant 而非 pgvector""为什么 RAG 取消 v1"这类决策，建议落 `docs/adr/000X-*.md`，避免日后讨论反复。

---

## 优先实施清单（建议两个迭代）

### 迭代 1（安全 & 稳定性）
1. 强校验 `JWT_SECRET`、清掉 `debug.log`、收紧 CORS、加请求体大小/PDF 页数限制（§4）。
2. 引入 Alembic + `pool_recycle`/`pool_size`（§2.1, §2.2）。
3. 引入 `logging` 与 `/healthz`（§5.1, §5.3）。
4. 加 GitHub Actions：lint + pytest + 前端 build（§7.2）。

### 迭代 2（结构 & 性能）
5. 拆 `api.py` 为 router 包，落地 `pydantic-settings`（§1.1, §1.2）。
6. `agents.py` 抽 prompts/clients/services，统一 LLM 调用与重试（§6.1）。
7. 异步化 DB 或 threadpool 化（§2.3）。
8. 锁定依赖、提交 `package-lock.json`、Dockerfile 多阶段+非 root（§7.3, §8）。

完成上述两轮，项目在可维护性、可观测性、安全性上的"地基"基本牢固，再去推进 LangGraph、RAG 评测等业务侧 Roadmap 时阻力会小得多。
