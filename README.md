# 🚀 JobCopilot - AI 求职辅导与定制投递引擎

## 📖 项目背景与愿景

在技术求职过程中，候选人往往面临**“海投效率低”**、**“简历匹配度差”**以及**“面试八股/场景题缺乏真实反馈”**的痛点。
**JobCopilot** 旨在通过构建一个局部 RAG + 确定性状态机（LangGraph）驱动的多智能体协同系统，为候选人提供从**“JD 解析与简历定制”**到**“AI 模拟面试”**的端到端闭环服务。

### 🌟 核心设计理念
- **拒绝“爬虫焦虑”**：数据源完全依赖用户自主输入（PDF/文本）及高质量开源技术文档（GitHub 纯文本库），彻底规避反爬风险。
- **结构化优先**：所有智能体之间的通信严格遵守预定义的 JSON Schema（基于 Pydantic），保证 Multi-Agent 工作流的工程稳定性。
- **前后端分离**：后端使用 FastAPI 承载 LangGraph AI 编排层，前端使用 Streamlit 提供极简的极客交互界面。
- **容器化原生部署**：使用 Docker Compose 一键启动所有服务，保障数据持久化和环境一致性。

---

## 🏗️ 系统架构

整个系统分为三大层级结构：
1. **Frontend (展示层)**: 基于 Streamlit，提供表单输入、实时渲染（Tabs/JSON预览）。
2. **Backend (中枢控制层)**: FastAPI 分发请求，集成 LangChain 和 LangGraph 执行大模型请求和多智能体流转。
3. **Data & AI (数据与智能层)**: OpenAI/Claude 驱动的 LLM，配合 Pydantic 结构化约束，以及后续加入的 ChromaDB 向量检索、SQLite 持久化。

---

## 🛠️ 核心工作流 (Multi-Agent Flow)

### 💼 阶段一：简历定制流水线 (Resume Customization Graph) 
- **简历解析官**：读取原始简历，提取结构化技能、教育与项目经历 (UserInfo Schema)。
- **岗位分析官**：读取 JD 文本，提取核心技能、加分项与业务场景预测 (JDInfo Schema)。
- **简历优化师**：基于上述信息，使用 STAR 法则，将 JD 关键词自然融入候选人原有项目。

### 🎙️ 阶段二：模拟面试流水线 (Mock Interview Graph)
- **知识库 (RAG)**：基于开源的高质量八股文笔记（如 JavaGuide / CS-Notes），为 AI 提供严谨的技术基座。
- **出题官 / 追问官**：基于 JD 偏好出硬核题目；利用 LangGraph 循环状态机与用户进行 Human-in-the-loop 多轮切磋打分。

---

##  快速开始运行 (Docker 部署)

本项目采用了前后端分离的双容器架构。环境依赖全权由 Docker 处理，无需在本地配置繁杂的 Python 环境。

### 前置要求
- 已安装并启动 **Docker Desktop** (或 Docker Engine)。

### 1. 配置环境变量
在项目根目录（`docker-compose.yml` 同级目录）新建/确认存在 `.env` 文件：
```env
DEEPSEEK_API_KEY=sk-xxxxxx...
OPENAI_BASE_URL=https://api.deepseek.com

# Docker 内 backend 访问你本机 MySQL（Windows/Mac 推荐 host.docker.internal）
DATABASE_URL=mysql+pymysql://root:你的密码@host.docker.internal:3306/jobcopilot?charset=utf8mb4

JWT_SECRET=请替换成随机强密钥
JWT_EXPIRE_MINUTES=10080

# 定位服务配置（可选）
# auto: 依次尝试 amap -> ip-api -> ipinfo
GEO_PROVIDER=auto
GEO_AMAP_KEY=
GEO_IPINFO_TOKEN=
GEO_DEFAULT_CITY=北京

# 后端反向地理编码（浏览器坐标 -> 城市）
GEO_TENCENT_KEY=
```

首次运行前请先在 MySQL 中创建数据库：
```sql
CREATE DATABASE jobcopilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. 构建与启动
打开你的终端 (PowerShell 等)，运行以下命令：
```bash
docker compose up -d --build
```
> *首次运行需要拉取官方基础镜像和各种依赖，请耐心等待几分钟。*

### 3. 访问应用
- **用户前端入口** (Streamlit): [http://localhost:8501](http://localhost:8501)
- **后端 API 文档** (Swagger): [http://localhost:8000/docs](http://localhost:8000/docs)

### 4. 登录与定位授权（新增）
- 前端侧边栏支持注册/登录，登录后会持有 JWT。
- 当聊天命中“附近职位检索”且未授权定位时，系统会提示授权。
- 用户可选“仅本次允许”或“始终允许”：
	- 仅本次允许：只对当前请求生效，不持久化。
	- 始终允许：持久化授权策略到 MySQL。
- 系统优先使用浏览器定位坐标，并在后端通过地图 API 反向解析城市；不持久化城市，避免用户跨城后的陈旧数据。

---

## 🗺️ 开发路线计划 (Roadmap)

- [x] **Phase 1-MVP**: 初始化项目，制定 Pydantic Schema，通过 LangChain 完成基本静态文本的 Agent (解析+优化)。
- [x] **DevOps**: 设置 Docker 隔离开发环境与前后端解耦。
- [ ] **Phase 2-RAG**: 抓取 Markdown 语料，搭建 ChromaDB 向量数据库。
- [ ] **Phase 2-LangGraph**: 建立带有循环边（Conditional Edges）的“提出问题->用户回答->审视评分->深度追问”的模拟面试图。
- [ ] **Phase 3-Engineering**: 集成 SQLite，增加一个“投递小助手”记录用户的岗位投递进度；增加一键导出 Markdown 简历功能。

---
*Powered by LangGraph & FastAPI & Streamlit*
