import json
import os
import re
from statistics import mean
from typing import Iterable

from openai import OpenAI

from schemas import (
    JDInfo,
    JDInterviewProfile,
    MappingQualityScore,
    OptimizedResume,
    ResumeInterviewProfile,
    ResumeJDMapping,
    ResumeProjectHighlight,
    RewriteQualityScore,
    UserInfo,
)


DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_REASONER_MODEL = "deepseek-reasoner"
DEFAULT_MIMO_MODEL = "mimo-v2-pro"
DEFAULT_MIMO_TOP_P = 0.95
DEFAULT_MIMO_TEMPERATURE = 1.0
DEFAULT_MAX_TITLE_LEN = 80
DEFAULT_MAX_DOMAIN_LEN = 80


def _build_deepseek_client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY")

    return OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").strip(),
    )


def _build_mimo_client() -> OpenAI:
    api_key = (
        os.environ.get("MIMO_API_KEY", "").strip()
        or os.environ.get("MIMO_V2_PRO_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError("Missing MIMO_API_KEY or MIMO_V2_PRO_API_KEY")

    return OpenAI(
        api_key=api_key,
        base_url=os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip(),
    )


def _env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _default_chat_model() -> str:
    return _env_or_default("DEEPSEEK_CHAT_MODEL", DEFAULT_CHAT_MODEL)


def _default_reasoner_model() -> str:
    return _env_or_default("DEEPSEEK_REASONER_MODEL", DEFAULT_REASONER_MODEL)


def _default_mimo_model() -> str:
    return _env_or_default("MIMO_MODEL", DEFAULT_MIMO_MODEL)


def _resolve_model_alias(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip().lower()
    if normalized == "chat":
        return _default_chat_model()
    if normalized == "reasoner":
        return _default_reasoner_model()
    if normalized in {"mimo", "mimo_v2", "mimo-v2", "mimo-v2-pro"}:
        return _default_mimo_model()
    return model.strip()


def _resolve_stage_model(stage: str, override: str | None = None) -> str:
    resolved_override = _resolve_model_alias(override)
    if resolved_override:
        return resolved_override

    stage_name = (stage or "").strip().lower()
    if stage_name == "parse":
        return _env_or_default("DEEPSEEK_PARSE_MODEL", _default_chat_model())
    if stage_name == "map":
        return _env_or_default("MIMO_MAP_MODEL", _default_mimo_model())
    if stage_name == "rewrite":
        return _env_or_default("MIMO_REWRITE_MODEL", _default_mimo_model())
    if stage_name == "score":
        return _env_or_default("DEEPSEEK_SCORE_MODEL", _default_reasoner_model())
    return _default_chat_model()


def _extract_first_json_object(text: str) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None

    start_index = value.find("{")
    if start_index < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start_index, len(value)):
        char = value[index]

        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return value[start_index : index + 1]

    return None


def _parse_json_candidates(content: str) -> tuple[dict | None, str | None]:
    raw = str(content or "").strip()
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    extracted = _extract_first_json_object(cleaned)

    candidates: list[str] = []
    for candidate in (raw, cleaned, extracted):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as exc:
            last_error = exc

    if last_error is None:
        return None, "模型未返回可解析内容。"
    return None, str(last_error)


def _mimo_chat_completion(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
):
    request_kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
        "stop": None,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }

    try:
        return client.chat.completions.create(
            **request_kwargs,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        exc_text = str(exc).lower()
        unsupported_json_mode = any(
            keyword in exc_text
            for keyword in ("response_format", "json_object", "unsupported", "invalid parameter")
        )
        if not unsupported_json_mode:
            raise
        return client.chat.completions.create(**request_kwargs)


def _repair_mimo_json_output(
    client: OpenAI,
    *,
    model: str,
    broken_output: str,
    response_schema: dict,
    max_completion_tokens: int,
) -> str:
    repair_system_prompt = (
        "你是 JSON 修复器。你的任务是把给定文本修复成合法 JSON 对象。"
        "必须严格符合给定 JSON Schema，且仅输出 JSON。"
    )
    repair_user_prompt = (
        f"JSON Schema:\n{json.dumps(response_schema, ensure_ascii=False)}\n\n"
        f"待修复文本:\n{broken_output}"
    )

    response = _mimo_chat_completion(
        client,
        model=model,
        messages=[
            {"role": "system", "content": repair_system_prompt},
            {"role": "user", "content": repair_user_prompt},
        ],
        max_completion_tokens=max_completion_tokens,
        temperature=0.0,
        top_p=DEFAULT_MIMO_TOP_P,
    )
    return response.choices[0].message.content or "{}"


def call_deepseek_structured(
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    *,
    model: str | None = None,
    temperature: float = 0.1,
):
    client = _build_deepseek_client()
    resolved_model = _resolve_model_alias(model) or _default_chat_model()
    strict_system_prompt = (
        f"{system_prompt}\n\n"
        "只返回一个合法 JSON 对象，不要输出 markdown 代码块或额外解释。\n"
        f"JSON Schema:\n{json.dumps(response_schema, ensure_ascii=False)}"
    )

    response = client.chat.completions.create(
        model=resolved_model,
        messages=[
            {"role": "system", "content": strict_system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
        stream=False,
    )

    content = response.choices[0].message.content or "{}"
    parsed_data, parse_error = _parse_json_candidates(content)
    if parsed_data is not None:
        return parsed_data

    raise RuntimeError(f"DeepSeek 返回的 JSON 无法解析：{parse_error}")


def call_mimo_structured(
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    *,
    model: str | None = None,
    temperature: float = DEFAULT_MIMO_TEMPERATURE,
    top_p: float = DEFAULT_MIMO_TOP_P,
    max_completion_tokens: int = 2048,
):
    client = _build_mimo_client()
    resolved_model = _resolve_model_alias(model) or _default_mimo_model()
    strict_system_prompt = (
        f"{system_prompt}\n\n"
        "只返回一个合法 JSON 对象，不要输出 markdown 代码块或额外解释。\n"
        f"JSON Schema:\n{json.dumps(response_schema, ensure_ascii=False)}"
    )

    response = _mimo_chat_completion(
        client,
        model=resolved_model,
        messages=[
            {"role": "system", "content": strict_system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    content = response.choices[0].message.content or "{}"
    parsed_data, parse_error = _parse_json_candidates(content)
    if parsed_data is not None:
        return parsed_data

    repaired_content = _repair_mimo_json_output(
        client,
        model=resolved_model,
        broken_output=content,
        response_schema=response_schema,
        max_completion_tokens=max_completion_tokens,
    )
    repaired_data, repaired_error = _parse_json_candidates(repaired_content)
    if repaired_data is not None:
        return repaired_data

    raise RuntimeError(
        "MIMO 返回的 JSON 无法解析，且自动修复失败。"
        f" 原始错误：{parse_error}；修复后错误：{repaired_error}"
    )


def parse_resume_to_json(resume_text: str, model: str | None = None) -> UserInfo:
    system_prompt = """
你是简历结构化信息提取助手。
请从原始中文简历文本中提取结构化数据。

规则：
1. 只保留输入文本中有证据的信息。
2. 不要杜撰项目名称、指标、技术栈或职责。
3. 技能项尽量标准化、简洁表达。
4. 缺失字段按 schema 默认值返回（空字符串或空数组）。
5. `projects` 优先提取有实质内容的项目经历。
"""

    user_prompt = f"原始简历文本：\n{resume_text}"
    raw_data = call_deepseek_structured(
        system_prompt,
        user_prompt,
        UserInfo.model_json_schema(),
        model=_resolve_stage_model("parse", model),
    )
    return UserInfo.model_validate(raw_data)


def parse_jd_to_json(jd_text: str, model: str | None = None) -> JDInfo:
    system_prompt = """
你是岗位 JD 结构化信息提取助手。
请从原始中文 JD 文本中提取招聘要求。

规则：
1. `must_have_skills` 仅放硬性要求技能。
2. 加分项放入 `nice_to_have_skills`。
3. `core_responsibilities` 用动作导向短语总结。
4. 不要补充 JD 中未出现的信息。
5. 缺失字段按 schema 默认值返回。
"""

    user_prompt = f"原始 JD 文本：\n{jd_text}"
    raw_data = call_deepseek_structured(
        system_prompt,
        user_prompt,
        JDInfo.model_json_schema(),
        model=_resolve_stage_model("parse", model),
    )
    jd_info = JDInfo.model_validate(raw_data)
    return enrich_jd_info(jd_info, jd_text)


def _dedupe_keep_order(values: Iterable[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(item)
        if limit is not None and len(result) >= limit:
            break
    return result


def _compact_text(text: str, limit: int = 160) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3].rstrip()}..."


def _first_non_empty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return ""


def _extract_label_value(text: str, labels: list[str], *, max_len: int) -> str:
    if not labels:
        return ""
    escaped = [re.escape(item) for item in labels]
    pattern = rf"(?:{'|'.join(escaped)})\s*[:：]\s*([^\n\r]{{1,{max_len}}})"
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()[:max_len]


def _guess_job_title_from_text(jd_text: str) -> str:
    text = str(jd_text or "")
    title_from_label = _extract_label_value(
        text,
        ["岗位", "岗位名称", "职位", "职位名称", "招聘岗位", "Job Title"],
        max_len=DEFAULT_MAX_TITLE_LEN,
    )
    if title_from_label:
        return title_from_label

    first_line = _first_non_empty_line(text)
    if first_line and len(first_line) <= 40 and re.search(
        r"(工程师|开发|算法|产品|经理|专家|实习|设计师|架构师|分析师)",
        first_line,
    ):
        return first_line[:DEFAULT_MAX_TITLE_LEN]

    bracket_match = re.search(r"[【\[]([^】\]\n]{2,40})[】\]]", text)
    if bracket_match:
        bracket_value = bracket_match.group(1).strip()
        if re.search(r"(工程师|开发|算法|产品|经理|专家|实习|设计师|架构师|分析师)", bracket_value):
            return bracket_value[:DEFAULT_MAX_TITLE_LEN]

    generic_match = re.search(
        r"([A-Za-z0-9+\-/·\u4e00-\u9fa5]{2,30}(?:工程师|开发|算法|产品|经理|专家|实习生|设计师|架构师|分析师))",
        text,
    )
    if generic_match:
        return generic_match.group(1).strip()[:DEFAULT_MAX_TITLE_LEN]

    return ""


def _guess_business_domain_from_text(jd_text: str) -> str:
    text = str(jd_text or "")
    domain_from_label = _extract_label_value(
        text,
        ["业务方向", "业务领域", "所属行业", "行业方向", "业务简介", "业务场景", "Domain"],
        max_len=DEFAULT_MAX_DOMAIN_LEN,
    )
    if domain_from_label:
        return domain_from_label

    lowered = text.lower()
    domain_keywords = [
        ("金融科技", ["金融", "银行", "保险", "证券", "支付", "风控"]),
        ("电商零售", ["电商", "零售", "商城", "交易", "商品"]),
        ("企业服务", ["saas", "crm", "企业服务", "b2b", "oa", "erp"]),
        ("教育", ["教育", "教培", "在线学习", "课堂"]),
        ("医疗健康", ["医疗", "健康", "医院", "制药", "生物"]),
        ("游戏", ["游戏", "玩家", "赛事"]),
        ("内容社区", ["内容", "社区", "社交", "短视频", "直播", "推荐"]),
        ("物流供应链", ["物流", "仓储", "供应链", "配送", "履约"]),
        ("智能制造", ["制造", "工厂", "工业", "iot", "物联网"]),
    ]

    for domain_name, keywords in domain_keywords:
        if any(keyword in lowered or keyword in text for keyword in keywords):
            return domain_name

    return ""


def enrich_jd_info(jd_info: JDInfo, jd_text: str, *, title_hint: str | None = None) -> JDInfo:
    current_title = str(jd_info.job_title or "").strip()
    current_domain = str(jd_info.business_domain or "").strip()

    fallback_title = str(title_hint or "").strip()
    guessed_title = _guess_job_title_from_text(jd_text)
    guessed_domain = _guess_business_domain_from_text(jd_text)

    next_title = current_title or fallback_title or guessed_title
    next_domain = current_domain or guessed_domain

    if next_title == current_title and next_domain == current_domain:
        return jd_info

    return jd_info.model_copy(
        update={
            "job_title": next_title[:DEFAULT_MAX_TITLE_LEN],
            "business_domain": next_domain[:DEFAULT_MAX_DOMAIN_LEN],
        }
    )


def build_resume_interview_profile(user_info: UserInfo) -> ResumeInterviewProfile:
    merged_skills = list(user_info.global_tech_stack)
    project_highlights: list[ResumeProjectHighlight] = []

    for project in user_info.projects[:5]:
        merged_skills.extend(project.tech_stack)
        project_highlights.append(
            ResumeProjectHighlight(
                project_name=project.project_name,
                role=project.role,
                summary=_compact_text(project.description, limit=180),
                tech_stack=_dedupe_keep_order(project.tech_stack, limit=8),
            )
        )

    return ResumeInterviewProfile(
        name=user_info.name,
        education=_compact_text(user_info.education, limit=120),
        top_skills=_dedupe_keep_order(merged_skills, limit=12),
        project_highlights=project_highlights,
    )


def build_jd_interview_profile(jd_info: JDInfo) -> JDInterviewProfile:
    return JDInterviewProfile(
        job_title=jd_info.job_title,
        company_name=jd_info.company_name,
        must_have_skills=_dedupe_keep_order(jd_info.must_have_skills, limit=12),
        nice_to_have_skills=_dedupe_keep_order(jd_info.nice_to_have_skills, limit=8),
        core_responsibilities=_dedupe_keep_order(jd_info.core_responsibilities, limit=8),
        business_domain=_compact_text(jd_info.business_domain, limit=120),
    )


def map_resume_to_jd(user_info: UserInfo, jd_info: JDInfo, model: str | None = None) -> ResumeJDMapping:
    system_prompt = """
你是资深技术招聘专家和简历策略顾问。你的任务是深度分析候选人简历与职位描述（JD）的匹配程度，并输出严格结构化的结果。

请遵循以下核心原则进行分析：
1. 证据支撑为主：每个匹配点都必须有简历中的具体事实和项目证据支撑，甄别仅停留在“关键词罗列”层面的无效匹配 。
2. 识别过度包装：警惕将简单的基础API调用过度包装为复杂系统（如将基础对话拼接包装成“Agent”）的行为 。如果匹配点经不起深度追问，必须降级评估。
3. 风险点客观诚实：明确指出简历中缺乏区分度（如标准的教程项目无额外探索 ）或经历描述宽泛（如只写“参与/负责”而无具体动作和结果）的风险，不要过度乐观。
4. 聚焦核心区分度：识别并重点评估简历中展示了独立思考、做过深度方案对比或性能优化的部分。
5. 优化焦点（rewrite_focus）：针对匹配短板，提供具体、可执行的建议，避免空话套话。
6. 输出要求：保持客观、专业，输出完整但保持简洁。
"""

    user_prompt = (
        f"结构化简历数据：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"结构化 JD 数据：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    selected_model = _resolve_stage_model("map", model)
    raw_data = call_mimo_structured(
        system_prompt,
        user_prompt,
        ResumeJDMapping.model_json_schema(),
        model=selected_model,
        temperature=DEFAULT_MIMO_TEMPERATURE,
        top_p=DEFAULT_MIMO_TOP_P,
    )
    return ResumeJDMapping.model_validate(raw_data)


def rewrite_resume_bullets(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
    model: str | None = None,
) -> OptimizedResume:
    system_prompt = """
你是资深技术简历优化顾问。你的任务是帮助候选人重构和优化简历，使其在筛选和面试中具备极高的区分度。

请严格遵循以下优化规则处理用户的简历：
1. 采用三段式结构（破除技术栈罗列）：项目描述必须采用“问题 → 方案 → 量化结果”的三段式结构。将技术栈自然融入具体的做法描述中，不要单独罗列毫无意义的框架名称 。
2. 强化动作与量化结果（破除工作日志写法）：将“参与”、“负责”、“协助”等模糊词替换为“设计”、“实现”、“对比”、“优化”、“修复”等具体动作词 。所有结果必须有具体数字支撑（如“准确率从 62% 提升到 81%”），严禁使用“提升了效果”等模糊表述 。
3. 拒绝过度包装（实事求是显深度）：你是什么水平就写什么水平，但要把做到的层次写深。若只是基础LLM应用，重点写Prompt工程、切分策略或上下文控制 ；若是Agent，必须突出其特有机制（如ReAct循环、工具调用失败恢复、防止无限循环设计）。
4. 挖掘项目差异化（破除教程项目充数）：对于基础的课程或教程项目，必须展示“在教程之外你还做了什么” 。重点补充推理阶段的性能优化、边界问题（如短文本）的解决过程或对不同方案的深入对比 。
5. 技能列表“去水”：只保留候选人能在面试中撑住15分钟追问的技术。明确分层标注熟练度，“熟悉”意味着能白板手写，“了解”意味着懂原理 。坚决删减为了充数而堆砌的关键词 。
6. 用事实替代形容词（破除空洞自我评价）：直接删除“学习能力强”、“沟通能力好”等零信息量的性格描述 。必须替换为可验证的事实，如开源贡献（PR）、技术博客阅读量、复现的论文数量或竞赛成绩 。
"""

    user_prompt = (
        f"结构化简历数据：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"结构化 JD 数据：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"简历-JD 映射结果：\n{mapping.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    selected_model = _resolve_stage_model("rewrite", model)
    raw_data = call_mimo_structured(
        system_prompt,
        user_prompt,
        OptimizedResume.model_json_schema(),
        model=selected_model,
        temperature=DEFAULT_MIMO_TEMPERATURE,
        top_p=DEFAULT_MIMO_TOP_P,
    )
    return OptimizedResume.model_validate(raw_data)


def _clamp_score(value: float | int) -> int:
    return max(0, min(100, int(round(float(value)))))


def _join_text_blocks(items: Iterable[str]) -> str:
    return " ".join(str(item or "").strip() for item in items if str(item or "").strip())


def _feedback_list(values: Iterable[str], limit: int = 6) -> list[str]:
    return _dedupe_keep_order(values, limit=limit)


def _blend_score_fields(
    rule_scores: dict,
    llm_scores: dict | None,
    fields: list[str],
    *,
    rule_weight: float,
    llm_weight: float,
) -> dict:
    final_scores: dict[str, int] = {}
    for field in fields:
        rule_value = _clamp_score(rule_scores.get(field, 0))
        if llm_scores is None:
            final_scores[field] = rule_value
            continue
        llm_value = _clamp_score(llm_scores.get(field, rule_value))
        final_scores[field] = _clamp_score(rule_value * rule_weight + llm_value * llm_weight)
    return final_scores


def _mapping_rule_score(jd_info: JDInfo, mapping: ResumeJDMapping) -> MappingQualityScore:
    must_have_skills = _dedupe_keep_order(jd_info.must_have_skills)

    map_text_parts: list[str] = [
        mapping.candidate_positioning,
        *mapping.strong_match_points,
        *mapping.risk_points,
        *mapping.keyword_strategy,
    ]
    for project_map in mapping.project_mappings:
        map_text_parts.extend(project_map.matched_requirements)
        map_text_parts.extend(project_map.evidence_points)
        map_text_parts.extend(project_map.missing_or_unsupported_points)
        map_text_parts.extend(project_map.rewrite_focus)

    joined_map_text = _join_text_blocks(map_text_parts).lower()
    matched_must_have_count = sum(1 for skill in must_have_skills if skill.lower() in joined_map_text)
    coverage_score = 100 if not must_have_skills else (matched_must_have_count / len(must_have_skills)) * 100

    project_mapping_count = len(mapping.project_mappings)
    project_with_evidence_count = sum(1 for item in mapping.project_mappings if item.evidence_points)
    if project_mapping_count > 0:
        evidence_score = (project_with_evidence_count / project_mapping_count) * 100
    else:
        evidence_score = 72 if mapping.strong_match_points else 35

    missing_point_count = len(mapping.risk_points) + sum(
        len(item.missing_or_unsupported_points) for item in mapping.project_mappings
    )
    if missing_point_count >= 4:
        gap_score = 95
    elif missing_point_count >= 2:
        gap_score = 85
    elif missing_point_count == 1:
        gap_score = 72
    else:
        gap_score = 45

    rewrite_focus_count = sum(len(item.rewrite_focus) for item in mapping.project_mappings)
    actionable_score = 35
    if mapping.candidate_positioning.strip():
        actionable_score += 20
    actionable_score += min(25, len(mapping.keyword_strategy) * 7)
    actionable_score += min(20, rewrite_focus_count * 5)
    if mapping.project_mappings:
        actionable_score += 10
    actionable_score = _clamp_score(actionable_score)

    overall_score = _clamp_score(
        coverage_score * 0.35
        + evidence_score * 0.25
        + gap_score * 0.20
        + actionable_score * 0.20
    )

    strengths: list[str] = []
    issues: list[str] = []

    if coverage_score >= 75:
        strengths.append("Must-have 要求覆盖较完整。")
    else:
        issues.append("Must-have 要求覆盖不足。")

    if evidence_score >= 70:
        strengths.append("项目级证据相对完整。")
    else:
        issues.append("部分映射点缺少明确简历证据。")

    if gap_score >= 70:
        strengths.append("风险与缺口识别较明确。")
    else:
        issues.append("风险分析偏弱或过于乐观。")

    if actionable_score >= 70:
        strengths.append("改写重点与关键词策略可执行性较好。")
    else:
        issues.append("改写重点不够具体，执行性不足。")

    summary = (
        f"Must-have 覆盖 {matched_must_have_count}/{len(must_have_skills)}；"
        f"项目证据 {project_with_evidence_count}/{project_mapping_count}。"
    )

    return MappingQualityScore(
        coverage_score=_clamp_score(coverage_score),
        evidence_score=_clamp_score(evidence_score),
        gap_score=_clamp_score(gap_score),
        actionable_score=actionable_score,
        overall_score=overall_score,
        strengths=_feedback_list(strengths),
        issues=_feedback_list(issues),
        summary=summary,
    )


def _rewrite_rule_score(user_info: UserInfo, jd_info: JDInfo, optimized_resume: OptimizedResume) -> RewriteQualityScore:
    user_project_names = {project.project_name.strip().lower() for project in user_info.projects if project.project_name.strip()}
    rewritten_project_names = [
        project.original_project_name.strip().lower()
        for project in optimized_resume.optimized_projects
        if project.original_project_name.strip()
    ]

    if not optimized_resume.optimized_projects:
        faithfulness_score = 30
    else:
        unknown_projects = 0
        if user_project_names:
            unknown_projects = sum(1 for name in rewritten_project_names if name not in user_project_names)
        faithfulness_score = _clamp_score(95 - unknown_projects * 28)
        if not optimized_resume.summary_hook.strip():
            faithfulness_score = _clamp_score(faithfulness_score - 8)

    all_bullets: list[str] = []
    for project in optimized_resume.optimized_projects:
        all_bullets.extend(project.optimized_bullets)

    rewrite_text = _join_text_blocks(
        [
            optimized_resume.summary_hook,
            *optimized_resume.skills_rewrite_suggestions,
            *(project.project_positioning for project in optimized_resume.optimized_projects),
            *all_bullets,
        ]
    ).lower()

    jd_terms = _dedupe_keep_order([*jd_info.must_have_skills, *jd_info.nice_to_have_skills])
    jd_term_hit_count = sum(1 for term in jd_terms if term.lower() in rewrite_text)
    jd_alignment_score = 100 if not jd_terms else (jd_term_hit_count / len(jd_terms)) * 100

    if not all_bullets:
        impact_score = 32
    else:
        quantified_count = sum(1 for bullet in all_bullets if re.search(r"\d|%", bullet))
        impact_score = 40 + 60 * (quantified_count / len(all_bullets))

    if not all_bullets:
        readability_score = 45
    else:
        bullet_lengths = [len(item.strip()) for item in all_bullets if item.strip()]
        if not bullet_lengths:
            readability_score = 45
        else:
            avg_length = mean(bullet_lengths)
            readability_score = 84 if 35 <= avg_length <= 130 else 68
            too_long_count = sum(1 for size in bullet_lengths if size > 220)
            too_short_count = sum(1 for size in bullet_lengths if size < 12)
            readability_score -= min(18, too_long_count * 6)
            readability_score -= min(12, too_short_count * 4)

    must_have_terms = _dedupe_keep_order(jd_info.must_have_skills)
    must_have_hit_count = sum(1 for term in must_have_terms if term.lower() in rewrite_text)
    ats_score = 100 if not must_have_terms else (must_have_hit_count / len(must_have_terms)) * 100

    overall_score = _clamp_score(
        faithfulness_score * 0.30
        + jd_alignment_score * 0.25
        + impact_score * 0.20
        + readability_score * 0.15
        + ats_score * 0.10
    )

    strengths: list[str] = []
    issues: list[str] = []

    if faithfulness_score >= 75:
        strengths.append("改写与原始项目事实一致性较好。")
    else:
        issues.append("存在事实漂移风险，或原始证据绑定偏弱。")

    if jd_alignment_score >= 70:
        strengths.append("改写对 JD 关键词覆盖较好。")
    else:
        issues.append("与 JD 对齐不足，关键要求覆盖不够。")

    if impact_score >= 65:
        strengths.append("bullet 中体现了影响力或可量化结果。")
    else:
        issues.append("bullet 缺少可量化的结果信号。")

    if readability_score >= 70:
        strengths.append("bullet 可读性整体稳定。")
    else:
        issues.append("bullet 长度分布影响可读性。")

    summary = (
        f"JD 关键词命中 {jd_term_hit_count}/{len(jd_terms)}；"
        f"must-have 命中 {must_have_hit_count}/{len(must_have_terms)}。"
    )

    return RewriteQualityScore(
        faithfulness_score=_clamp_score(faithfulness_score),
        jd_alignment_score=_clamp_score(jd_alignment_score),
        impact_score=_clamp_score(impact_score),
        readability_score=_clamp_score(readability_score),
        ats_score=_clamp_score(ats_score),
        overall_score=overall_score,
        strengths=_feedback_list(strengths),
        issues=_feedback_list(issues),
        summary=summary,
    )


def review_mapping_quality(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
    model: str | None = None,
) -> MappingQualityScore:
    system_prompt = """
你是严格的“简历-JD 映射质量评审员”。
请对映射结果打分（0-100）。

评分维度：
- coverage_score：must-have 要求覆盖度。
- evidence_score：证据质量与具体程度。
- gap_score：缺口/风险识别完整性与真实性。
- actionable_score：对后续改写的可执行性。
- overall_score：综合评分。

`strengths` 和 `issues` 要简洁、具体。
"""

    user_prompt = (
        f"简历数据：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"JD 数据：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"映射输出：\n{mapping.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    raw_data = call_deepseek_structured(
        system_prompt,
        user_prompt,
        MappingQualityScore.model_json_schema(),
        model=_resolve_stage_model("score", model),
        temperature=0.0,
    )
    return MappingQualityScore.model_validate(raw_data)


def review_rewrite_quality(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
    optimized_resume: OptimizedResume,
    model: str | None = None,
) -> RewriteQualityScore:
    system_prompt = """
你是严格的“简历改写质量评审员”。
请对改写结果打分（0-100）。

评分维度：
- faithfulness_score：与原始简历事实一致性。
- jd_alignment_score：与 JD 要求和职责的对齐程度。
- impact_score：动作-结果表达与可量化程度。
- readability_score：可读性与简洁度。
- ats_score：关键词覆盖与 ATS 友好度。
- overall_score：综合评分。

`strengths` 和 `issues` 要简洁、具体。
"""

    user_prompt = (
        f"简历数据：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"JD 数据：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"映射输出：\n{mapping.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"改写结果：\n{optimized_resume.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    raw_data = call_deepseek_structured(
        system_prompt,
        user_prompt,
        RewriteQualityScore.model_json_schema(),
        model=_resolve_stage_model("score", model),
        temperature=0.0,
    )
    return RewriteQualityScore.model_validate(raw_data)


def score_mapping_quality(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
    model: str | None = None,
) -> dict:
    rule_score = _mapping_rule_score(jd_info, mapping)
    score_model = _resolve_stage_model("score", model)
    llm_score: MappingQualityScore | None = None
    llm_error: str | None = None

    try:
        llm_score = review_mapping_quality(user_info, jd_info, mapping, model=score_model)
    except Exception as exc:
        llm_error = str(exc)[:500]

    use_hybrid = llm_score is not None
    rule_weight = 0.5 if use_hybrid else 1.0
    llm_weight = 0.5 if use_hybrid else 0.0

    numeric_fields = [
        "coverage_score",
        "evidence_score",
        "gap_score",
        "actionable_score",
        "overall_score",
    ]
    rule_payload = rule_score.model_dump()
    llm_payload = llm_score.model_dump() if llm_score is not None else None
    final_numeric = _blend_score_fields(
        rule_payload,
        llm_payload,
        numeric_fields,
        rule_weight=rule_weight,
        llm_weight=llm_weight,
    )

    final_strengths = _feedback_list(
        [*(llm_score.strengths if llm_score else []), *rule_score.strengths],
        limit=8,
    )
    final_issues = _feedback_list(
        [*(llm_score.issues if llm_score else []), *rule_score.issues],
        limit=8,
    )
    final_summary = (
        llm_score.summary.strip()
        if llm_score is not None and llm_score.summary.strip()
        else rule_score.summary
    )

    return {
        "version": "v1",
        "score_model": score_model,
        "weights": {
            "rule": rule_weight,
            "llm": llm_weight,
        },
        "rule": rule_payload,
        "llm": llm_payload,
        "final": {
            **final_numeric,
            "strengths": final_strengths,
            "issues": final_issues,
            "summary": final_summary,
        },
        "error": llm_error,
    }


def score_rewrite_quality(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
    optimized_resume: OptimizedResume,
    model: str | None = None,
) -> dict:
    rule_score = _rewrite_rule_score(user_info, jd_info, optimized_resume)
    score_model = _resolve_stage_model("score", model)
    llm_score: RewriteQualityScore | None = None
    llm_error: str | None = None

    try:
        llm_score = review_rewrite_quality(
            user_info,
            jd_info,
            mapping,
            optimized_resume,
            model=score_model,
        )
    except Exception as exc:
        llm_error = str(exc)[:500]

    use_hybrid = llm_score is not None
    rule_weight = 0.5 if use_hybrid else 1.0
    llm_weight = 0.5 if use_hybrid else 0.0

    numeric_fields = [
        "faithfulness_score",
        "jd_alignment_score",
        "impact_score",
        "readability_score",
        "ats_score",
        "overall_score",
    ]
    rule_payload = rule_score.model_dump()
    llm_payload = llm_score.model_dump() if llm_score is not None else None
    final_numeric = _blend_score_fields(
        rule_payload,
        llm_payload,
        numeric_fields,
        rule_weight=rule_weight,
        llm_weight=llm_weight,
    )

    final_strengths = _feedback_list(
        [*(llm_score.strengths if llm_score else []), *rule_score.strengths],
        limit=8,
    )
    final_issues = _feedback_list(
        [*(llm_score.issues if llm_score else []), *rule_score.issues],
        limit=8,
    )
    final_summary = (
        llm_score.summary.strip()
        if llm_score is not None and llm_score.summary.strip()
        else rule_score.summary
    )

    return {
        "version": "v1",
        "score_model": score_model,
        "weights": {
            "rule": rule_weight,
            "llm": llm_weight,
        },
        "rule": rule_payload,
        "llm": llm_payload,
        "final": {
            **final_numeric,
            "strengths": final_strengths,
            "issues": final_issues,
            "summary": final_summary,
        },
        "error": llm_error,
    }
