import asyncio
from contextlib import AsyncExitStack
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import agents
import os

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
# MCP 核心连接组件
from mcp import ClientSession
from mcp.client.sse import sse_client

app = FastAPI(title="JobCopilot API Backend")

class ProcessRequest(BaseModel):
    resume_text: str
    jd_text: str

class ChatRequest(BaseModel):
    message: str

# 这里我们手写一个轻量级的适配器桥梁：
# 将远程 MCP 工具映射为 LangChain 可以直接使用的函数结构。
# 随着未来 langchain对mcp生态完善，这块将可直接替代为一行 bind_mcp_tools。
from langchain.tools import StructuredTool
def create_langchain_tools_from_mcp(mcp_tools, session):
    lc_tools = []
    for t in mcp_tools:
        # 对每一个 MCP 工具生成一个绑定该 session 的代理函数
        def _invoke_tool(name=t.name, **kwargs):
            import asyncio
            # 因为 LangChain 默认同步执行 tool，而 MCP session 需要 await。
            # 这里通过事件循环阻塞调用远程 MCP
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果深层嵌套在 async 内部，用嵌套循环或者创建新任务
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(session.call_tool(name, arguments=kwargs))
            else:
                return loop.run_until_complete(session.call_tool(name, arguments=kwargs))

        lc_tools.append(StructuredTool.from_function(
            func=_invoke_tool,
            name=t.name,
            description=t.description or f"MCP Tool: {t.name}",
            # 注意：实际生产中可通过 pydantic 动态构建 Schema，此处对 Demo 简化，依赖大模型自我补齐参数
        ))
    return lc_tools

@app.post("/api/v1/process")
async def process_job_application(req: ProcessRequest):
    """一键处理流水线：并发解析 -> 并发生成"""
    try:
        # ==========================================
        # ⚡ 速度优化：第一阶段 (并发解析输入数据)
        # ==========================================
        # 使用 asyncio.to_thread 将同步的 requests 网络请求放入后台线程池中，实现兵分两路同时调用 API
        user_info_task = asyncio.to_thread(agents.parse_resume_to_json, req.resume_text)
        jd_info_task = asyncio.to_thread(agents.parse_jd_to_json, req.jd_text)
        
        # 等待两个解析兵同时完成并带回结果
        user_info, jd_info = await asyncio.gather(user_info_task, jd_info_task)

        # ==========================================
        # ⚡ 速度优化：第二阶段 (并发生成输出数据)
        # ==========================================
        # 拿到组装好基础数据的 JSON 后，再兵分两路，分别交给两个不同的 Agent 撰写对应内容
        opt_resume_task = asyncio.to_thread(agents.optimize_resume, user_info, jd_info)
        cover_letter_task = asyncio.to_thread(agents.write_cover_letter, user_info, jd_info)
        
        # 等待两个撰写兵同时完工
        optimized_resume, cover_letter = await asyncio.gather(opt_resume_task, cover_letter_task)

        return {
            "status": "success",
            "data": {
                "user_info": user_info.model_dump(),
                "jd_info": jd_info.model_dump(),
                "optimized_resume": optimized_resume.model_dump(),
                "cover_letter": cover_letter.model_dump()
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/chat")
async def chat_with_agent(req: ChatRequest):
    """能够主动使用爬虫 MCP Server 的聊天接口"""
    try:
        # 获取环境变量中 MCP 爬虫服务器的地址
        mcp_url = os.environ.get("MCP_SERVER_URL", "http://mcp_crawler:8001/sse")
        
        async with AsyncExitStack() as stack:
            # 1. 建立与爬虫微服务节点的 SSE 远程连接！
            sse_transport = await stack.enter_async_context(sse_client(mcp_url))
            session = await stack.enter_async_context(ClientSession(sse_transport[0], sse_transport[1]))
            await session.initialize()
            
            # 2. 动态发现远程能用的绝技 (Tools)
            mcp_response = await session.list_tools()
            tools = create_langchain_tools_from_mcp(mcp_response.tools, session)
            
            # 3. 初始化绑定了 DeepSeek 的大模型
            llm = ChatOpenAI(
                api_key=os.environ.get('DEEPSEEK_API_KEY'),
                base_url="https://api.deepseek.com",
                model="deepseek-chat"
            )
            # 4. 设计调度 Prompt
            sys_msg = '你是 JobCopilot 智能求职助手。遇到找工作需求时，必须先通过 get_user_location 获取用户城市，再传给 crawl_nearby_jobs 进行全网真实职位爬取。若提取成功，整理成好看的列表回复给用户。'

            # 5. 执行对话
            agent_executor = create_react_agent(llm, tools=tools, prompt=sys_msg)
            
            response = await asyncio.to_thread(
                agent_executor.invoke, 
                {'messages': [('user', req.message)]}
            )
            
            final_ans = response['messages'][-1].content
            return {'reply': final_ans}

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

