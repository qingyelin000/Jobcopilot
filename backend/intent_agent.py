from __future__ import annotations

import json
import re
from typing import Literal
from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    tool_name: str = ""
    purpose: str = ""
    arguments: dict = Field(default_factory=dict)


class IntentPlan(BaseModel):
    intent: Literal[
        "nearby_job_search",
        "job_search",
        "general_chat",
        "career_advice",
        "unknown",
    ] = "unknown"
    reason: str = ""
    requires_location: bool = False
    tools: list[str] = Field(default_factory=list)
    execution_steps: list[PlanStep] = Field(default_factory=list)
    done_definition: str = "给出对用户问题的明确答案或下一步建议"


class TaskEvaluation(BaseModel):
    is_complete: bool = False
    reason: str = ""
    should_continue: bool = False
    suggested_next_tool: str = ""


def _extract_json_object(text: str) -> dict:
    if not text:
        return {}
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", stripped)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _fallback_plan(message: str, available_tools: list[str]) -> IntentPlan:
    lowered = (message or "").lower()
    needs_location = any(k in lowered for k in ["附近", "同城", "周边", "nearby"])
    has_job = any(k in lowered for k in ["工作", "岗位", "职位", "job"])

    steps: list[PlanStep] = []
    tools = []
    if has_job and needs_location and "get_user_location" in available_tools:
        tools.append("get_user_location")
        steps.append(PlanStep(tool_name="get_user_location", purpose="获取用户城市", arguments={}))
    if has_job and "crawl_nearby_jobs" in available_tools:
        tools.append("crawl_nearby_jobs")
        steps.append(PlanStep(tool_name="crawl_nearby_jobs", purpose="检索岗位", arguments={"keyword": "Python", "num_pages": 1}))

    if not has_job:
        return IntentPlan(intent="general_chat", reason="普通咨询", requires_location=False, tools=[], execution_steps=[])

    return IntentPlan(
        intent="nearby_job_search" if needs_location else "job_search",
        reason="回退规则生成",
        requires_location=needs_location,
        tools=tools,
        execution_steps=steps,
    )


async def llm_build_intent_plan(message: str, available_tools: list[str], llm) -> IntentPlan:
    prompt = f"""
你是任务规划Agent。请理解用户请求，拆分子任务并给出工具执行顺序。

可用工具: {available_tools}
用户问题: {message}

输出严格JSON，格式如下：
{{
  "intent": "nearby_job_search|job_search|general_chat|career_advice|unknown",
  "reason": "简短解释",
  "requires_location": true,
  "tools": ["get_user_location", "crawl_nearby_jobs"],
  "execution_steps": [
    {{"tool_name": "get_user_location", "purpose": "获取城市", "arguments": {{}}}},
    {{"tool_name": "crawl_nearby_jobs", "purpose": "检索岗位", "arguments": {{"keyword": "Python", "num_pages": 1}}}}
  ],
  "done_definition": "任务何时算完成"
}}

规则：
1) 如果用户要求“附近/同城”，优先先定位再抓岗位。
2) 只能使用可用工具名。
3) 无需工具时 execution_steps 为空数组。
"""
    msg = await llm.ainvoke(prompt)
    data = _extract_json_object(getattr(msg, "content", ""))
    if not data:
        return _fallback_plan(message, available_tools)
    try:
        plan = IntentPlan.model_validate(data)
    except Exception:
        return _fallback_plan(message, available_tools)

    plan.tools = [tool for tool in plan.tools if tool in available_tools]
    plan.execution_steps = [step for step in plan.execution_steps if step.tool_name in available_tools]
    return plan


async def llm_evaluate_completion(message: str, plan: IntentPlan, tool_outputs: list[dict], llm) -> TaskEvaluation:
    prompt = f"""
你是任务评估Agent。请判断当前任务是否完成。

用户问题: {message}
计划: {plan.model_dump_json(ensure_ascii=False)}
工具输出: {json.dumps(tool_outputs, ensure_ascii=False)}

输出严格JSON：
{{
  "is_complete": true,
  "reason": "简短原因",
  "should_continue": false,
  "suggested_next_tool": ""
}}

规则：
1) 如果已得到可直接回答用户的问题信息，则 is_complete=true。
2) 如果需要继续调用工具，则 should_continue=true 并给 suggested_next_tool。
3) 若遇到“未获得用户定位授权”，应判定可结束并提示用户授权。
"""
    msg = await llm.ainvoke(prompt)
    data = _extract_json_object(getattr(msg, "content", ""))
    if not data:
        return TaskEvaluation(is_complete=False, reason="评估失败，按默认继续", should_continue=True)
    try:
        return TaskEvaluation.model_validate(data)
    except Exception:
        return TaskEvaluation(is_complete=False, reason="评估解析失败", should_continue=True)
