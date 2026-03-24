import asyncio
from contextlib import AsyncExitStack
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
import agents
import os
import json
import requests
from sqlalchemy.orm import Session

from langchain_openai import ChatOpenAI
# MCP 核心连接组件
from mcp import ClientSession
from mcp.client.sse import sse_client
from intent_agent import llm_build_intent_plan, llm_evaluate_completion
from auth import create_access_token, decode_access_token, get_password_hash, verify_password
from auth_schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPreferenceUpdate,
    UserProfileResponse,
)
from db import Base, engine, get_db
from models import User

app = FastAPI(title="JobCopilot API Backend")


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)

class ProcessRequest(BaseModel):
    resume_text: str
    jd_text: str

class ChatRequest(BaseModel):
    message: str
    location_consent: bool | None = None
    consent_scope: str | None = None
    user_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def _normalize_city(city: str | None) -> str:
    if not city:
        return ""
    return str(city).strip().replace("市", "")


def _reverse_geocode_by_amap(latitude: float, longitude: float) -> str:
    key = os.getenv("GEO_AMAP_KEY", "").strip()
    if not key:
        return ""
    try:
        response = requests.get(
            "https://restapi.amap.com/v3/geocode/regeo",
            params={
                "key": key,
                "location": f"{longitude},{latitude}",
                "extensions": "base",
            },
            timeout=5,
        )
        data = response.json()
        if data.get("status") != "1":
            return ""
        address = data.get("regeocode", {}).get("addressComponent", {})
        city = _normalize_city(address.get("city"))
        if city:
            return city
        return _normalize_city(address.get("province"))
    except Exception:
        return ""


def _reverse_geocode_by_tencent(latitude: float, longitude: float) -> str:
    key = os.getenv("GEO_TENCENT_KEY", "").strip()
    if not key:
        return ""
    try:
        response = requests.get(
            "https://apis.map.qq.com/ws/geocoder/v1/",
            params={
                "key": key,
                "location": f"{latitude},{longitude}",
            },
            timeout=5,
        )
        data = response.json()
        if data.get("status") != 0:
            return ""
        address = data.get("result", {}).get("address_component", {})
        city = _normalize_city(address.get("city"))
        if city:
            return city
        return _normalize_city(address.get("province"))
    except Exception:
        return ""


def _resolve_city_from_coordinates(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""
    city = _reverse_geocode_by_amap(latitude, longitude)
    if city:
        return city
    return _reverse_geocode_by_tencent(latitude, longitude)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):].strip()
    return None


def get_current_user_optional(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None

    username = decode_access_token(token)
    if not username:
        return None

    return db.query(User).filter(User.username == username).first()


def get_current_user(
    current_user: User | None = Depends(get_current_user_optional),
) -> User:
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return current_user

def _mcp_result_to_text(result) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        if texts:
            return "\n".join(texts)
    return str(result)


async def call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict) -> str:
    result = await session.call_tool(tool_name, arguments=arguments)
    return _mcp_result_to_text(result)


@app.post("/api/v1/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == req.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    new_user = User(
        username=req.username,
        password_hash=get_password_hash(req.password),
        location_consent=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(new_user.username)
    return TokenResponse(access_token=token)


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.username)
    return TokenResponse(access_token=token)


@app.get("/api/v1/users/me", response_model=UserProfileResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return UserProfileResponse(
        id=current_user.id,
        username=current_user.username,
        location_consent=current_user.location_consent,
    )


@app.patch("/api/v1/users/me/preferences", response_model=UserProfileResponse)
def update_preferences(
    req: UserPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.location_consent = req.location_consent
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    return UserProfileResponse(
        id=current_user.id,
        username=current_user.username,
        location_consent=current_user.location_consent,
    )

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
async def chat_with_agent(
    req: ChatRequest,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """意图驱动聊天接口：先判意图，再决定调用哪些工具与顺序"""
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
            available_tools = {tool.name for tool in mcp_response.tools}
            
            # 3. 初始化绑定了 DeepSeek 的大模型
            llm = ChatOpenAI(
                api_key=os.environ.get('DEEPSEEK_API_KEY'),
                base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.deepseek.com'),
                model='deepseek-chat',
                temperature=0.2,
            )

            # 4. 意图Agent(LLM)先判断：调用什么工具、如何执行、执行顺序
            intent_plan = await llm_build_intent_plan(req.message, sorted(list(available_tools)), llm)

            execution_steps = [step for step in intent_plan.execution_steps if step.tool_name in available_tools]
            tool_outputs = []
            city_name = _normalize_city(req.user_city)
            if not city_name:
                city_name = _resolve_city_from_coordinates(req.latitude, req.longitude)

            scope = (req.consent_scope or "").strip().lower()
            should_persist_consent = scope == "always"
            effective_location_consent = (
                req.location_consent
                if req.location_consent is not None
                else (current_user.location_consent if current_user else False)
            )

            if req.location_consent is not None and current_user and should_persist_consent:
                current_user.location_consent = req.location_consent
                db.add(current_user)
                db.commit()
                db.refresh(current_user)

            # 5. 按计划执行工具，并在每步后评估是否完成
            max_steps = min(len(execution_steps), 5)
            for index in range(max_steps):
                step = execution_steps[index]
                if step.tool_name == 'get_user_location':
                    location_text = await call_mcp_tool(
                        session,
                        'get_user_location',
                        {
                            'consent': effective_location_consent,
                            'user_city': req.user_city or '',
                        },
                    )
                    tool_outputs.append({'tool': 'get_user_location', 'output': location_text})

                    if '未获得用户定位授权' in location_text:
                        return {'reply': location_text, 'need_location_consent': True}

                    if not city_name:
                        city_name = location_text.strip().replace('市', '')

                elif step.tool_name == 'crawl_nearby_jobs':
                    if not city_name:
                        return {'reply': '要帮你找附近工作，我需要你的城市信息或定位授权。'}

                    step_args = dict(step.arguments or {})
                    step_args.setdefault('keyword', 'Python')
                    step_args.setdefault('num_pages', 1)
                    step_args['city_name'] = city_name

                    crawler_text = await call_mcp_tool(
                        session,
                        'crawl_nearby_jobs',
                        step_args,
                    )
                    tool_outputs.append({'tool': 'crawl_nearby_jobs', 'output': crawler_text})

                else:
                    generic_text = await call_mcp_tool(
                        session,
                        step.tool_name,
                        dict(step.arguments or {}),
                    )
                    tool_outputs.append({'tool': step.tool_name, 'output': generic_text})

                evaluation = await llm_evaluate_completion(req.message, intent_plan, tool_outputs, llm)
                if evaluation.is_complete or not evaluation.should_continue:
                    break

            # 6. 组织最终回复
            if tool_outputs:
                final_prompt = (
                    '你是 JobCopilot 求职助手。请根据用户问题、意图计划和工具结果给出简洁、结构化回答。\n'
                    f'用户问题: {req.message}\n'
                    f'意图计划: {intent_plan.model_dump_json(ensure_ascii=False)}\n'
                    f'工具输出: {json.dumps(tool_outputs, ensure_ascii=False)}\n'
                    '请输出中文结果，若工具失败需给出下一步建议。'
                )
                final_msg = await llm.ainvoke(final_prompt)
                return {'reply': final_msg.content}

            # 无需工具时直接聊天回答
            normal_msg = await llm.ainvoke(
                f"你是 JobCopilot 求职助手，请直接回答用户问题：{req.message}"
            )
            return {'reply': normal_msg.content}

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

