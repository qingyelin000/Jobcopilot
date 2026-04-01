import json
import os
import re
from statistics import mean
from typing import Any, Iterable

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
        return None, "Model did not return parseable JSON content."
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
        "You are a JSON repair assistant. Repair the provided text into valid JSON. "
        "The output must strictly follow the JSON Schema and return JSON only."
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

    raise RuntimeError(f"DeepSeek returned invalid JSON: {parse_error}")


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
        "MIMO returned invalid JSON and automatic repair failed. "
        f"Original parse error: {parse_error}; repaired parse error: {repaired_error}"
    )


def parse_resume_to_json(resume_text: str, model: str | None = None) -> UserInfo:
    system_prompt = """
你是简历结构化信息提取助手。请从原始中文简历文本中提取结构化数据。
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
你是岗位 JD 结构化信息提取助手。请从原始中文 JD 文本中提取招聘要求。
规则：
1. `must_have_skills` 只放硬性要求技能。
2. 加分项放入 `nice_to_have_skills`。
3. `core_responsibilities` 使用动作导向短语总结。
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
你是资深技术招聘专家和简历策略顾问。你的任务是分析候选人简历与职位描述（JD）的匹配程度，并输出严格结构化结果。
请遵循以下原则：
1. 证据驱动：每个匹配点必须有简历中的事实支撑，避免只做关键词对齐。
2. 识别过度包装：警惕把基础 API 调用包装为复杂系统（例如把普通调用描述成 Agent）。
3. 客观指出风险：明确缺少区分度或描述空泛的内容，不要一味乐观。
4. 关注差异化信号：优先识别独立思考、方案对比、性能优化等高价值内容。
5. `rewrite_focus` 要可执行：给出具体改写建议，避免空话套话。
6. 输出保持专业、简洁、可落地。
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
你是资深技术简历优化顾问。请在不捏造经历的前提下优化简历表达，使其更有区分度和可验证性。
请严格遵循以下规则：
1. 项目描述尽量采用“问题 -> 方案 -> 量化结果”结构。
2. 用具体动作词（设计/实现/优化/排障）替代“参与/负责”等空泛描述。
3. 结果尽量量化；无法量化时给出可验证事实。
4. 禁止过度包装：基础功能不要夸大为复杂系统；涉及 Agent 必须写清机制与边界。
5. 优先突出教程项目之外的增量工作（对比实验、性能优化、异常处理、工程化改造等）。
6. 技能只保留可在面试中展开证明的内容，并体现熟练度。
7. 删除空洞软性评价，改成事实证据（PR、博客、竞赛、复现等）。
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
        strengths.append("Must-have requirements are broadly covered.")
    else:
        issues.append("Must-have requirement coverage is insufficient.")

    if evidence_score >= 70:
        strengths.append("Project-level evidence is relatively complete.")
    else:
        issues.append("Some mapping points lack explicit resume evidence.")

    if gap_score >= 70:
        strengths.append("Risks and gaps are identified clearly.")
    else:
        issues.append("Risk analysis is weak or overly optimistic.")

    if actionable_score >= 70:
        strengths.append("Rewrite focus and keyword strategy are actionable.")
    else:
        issues.append("Rewrite focus is not specific enough to execute.")

    summary = (
        f"Must-have coverage {matched_must_have_count}/{len(must_have_skills)}; "
        f"project evidence {project_with_evidence_count}/{project_mapping_count}."
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
        strengths.append("Rewrites remain faithful to original project facts.")
    else:
        issues.append("There is risk of factual drift or weak evidence binding.")

    if jd_alignment_score >= 70:
        strengths.append("Rewrite has good JD keyword alignment.")
    else:
        issues.append("JD alignment is insufficient on key requirements.")

    if impact_score >= 65:
        strengths.append("Bullets include impact-oriented or measurable outcomes.")
    else:
        issues.append("Bullets lack measurable result signals.")

    if readability_score >= 70:
        strengths.append("Bullets are consistently readable.")
    else:
        issues.append("Bullet length distribution hurts readability.")

    summary = (
        f"JD keyword hits {jd_term_hit_count}/{len(jd_terms)}; "
        f"must-have hits {must_have_hit_count}/{len(must_have_terms)}."
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
你是严格的“简历-JD 映射质量评审员”。请对映射结果打分（0-100）。
评分维度：
- coverage_score：must-have 要求覆盖度。
- evidence_score：证据质量与具体程度。
- gap_score：缺口/风险识别的完整性与真实性。
- actionable_score：对后续改写的可执行性。
- overall_score：综合评分。
`strengths` 和 `issues` 必须简洁、具体、可验证。
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
你是严格的“简历改写质量评审员”。请对改写结果打分（0-100）。
评分维度：
- faithfulness_score：与原始简历事实一致性。
- jd_alignment_score：与 JD 要求和职责的对齐程度。
- impact_score：动作与结果表达及量化程度。
- readability_score：可读性与简洁度。
- ats_score：关键词覆盖与 ATS 友好度。
- overall_score：综合评分。
`strengths` 和 `issues` 必须简洁、具体、可验证。
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


def _mimo_interviewer_model() -> str:
    return _env_or_default("MIMO_INTERVIEWER_MODEL", _default_mimo_model())


def _mimo_evaluator_model() -> str:
    return _env_or_default("MIMO_EVALUATOR_MODEL", _default_mimo_model())


def _clamp_numeric_score(value: Any, default: float = 60.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(100.0, number))


_INTERVIEW_ALLOWED_TYPES = {
    "project_or_system_design",
    "backend_foundation",
    "coding",
    "behavioral",
    "general",
}
_INTERVIEW_SCENARIO_KEYWORDS = (
    "scenario",
    "production",
    "incident",
    "latency",
    "error rate",
    "rollback",
    "stability",
    "high concurrency",
    "if",
    "suppose",
)
_INTERVIEW_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,24}")


def _normalize_question_type(value: Any) -> str:
    question_type = str(value or "").strip().lower()
    if question_type in _INTERVIEW_ALLOWED_TYPES:
        return question_type
    return "general"


def _question_text_has_scenario_hint(text: str) -> bool:
    probe = str(text or "").lower()
    return any(keyword.lower() in probe for keyword in _INTERVIEW_SCENARIO_KEYWORDS)


def _extract_project_anchors(resume_text: str, limit: int = 4) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()
    for raw_line in str(resume_text or "").splitlines():
        line = raw_line.strip().lstrip("-*0123456789. ")
        if not line:
            continue
        if "\u9879\u76ee" not in line and "project" not in line.lower():
            continue
        head = re.split(r"[:：|｜,，。；;（）()]", line, maxsplit=1)[0].strip()
        if not head:
            continue
        candidate = head.replace("\u9879\u76ee\u7ecf\u5386", "").replace("\u9879\u76ee", "").strip()
        if not candidate:
            candidate = head
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        anchors.append(candidate[:40])
        if len(anchors) >= limit:
            break
    return anchors


def _extract_skill_anchors(query: str, resume_text: str, jd_text: str, limit: int = 10) -> list[str]:
    merged = " ".join(part for part in (query, resume_text, jd_text) if str(part or "").strip())
    fixed_keywords = [
        "Java",
        "Python",
        "Go",
        "MySQL",
        "Redis",
        "Kafka",
        "JVM",
        "HTTP",
        "TCP",
        "SQL",
        "Docker",
        "K8s",
        "Kubernetes",
        "FastAPI",
        "React",
        "RAG",
        "Agent",
        "Prompt",
        "LangChain",
        "LangGraph",
        "Microservice",
        "Distributed",
        "Cache",
        "RateLimit",
        "CircuitBreaker",
        "MQ",
        "Index",
        "Transaction",
        "Concurrency",
        "Lock",
        "VectorDB",
    ]

    anchors: list[str] = []
    seen: set[str] = set()

    for keyword in fixed_keywords:
        if keyword.lower() not in merged.lower():
            continue
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        anchors.append(keyword)
        if len(anchors) >= limit:
            return anchors

    for token in _INTERVIEW_ASCII_TOKEN_PATTERN.findall(merged):
        lowered = token.lower()
        if lowered in seen:
            continue
        if lowered in {"and", "the", "with", "from", "project", "resume", "jd"}:
            continue
        if len(token) < 3:
            continue
        seen.add(lowered)
        anchors.append(token)
        if len(anchors) >= limit:
            break
    return anchors


def _infer_interview_stage(
    *,
    turn_index: int,
    history_turns: list[dict[str, Any]],
    follow_up_hint: str | None,
) -> str:
    if str(follow_up_hint or "").strip():
        return "follow_up"
    if turn_index <= 1:
        return "project_kickoff"
    if turn_index == 2:
        return "project_deep_dive"

    asked_types = {
        _normalize_question_type((turn.get("question") or {}).get("question_type"))
        for turn in history_turns
    }
    asked_scenario = any(
        _question_text_has_scenario_hint(str((turn.get("question") or {}).get("question_text") or ""))
        for turn in history_turns
    )

    if turn_index == 3 and "backend_foundation" not in asked_types:
        return "fundamental_interleave"
    if turn_index >= 4 and not asked_scenario:
        return "scenario_drill"
    if "backend_foundation" not in asked_types:
        return "fundamental_interleave"
    return "mixed_deepening"


def _stage_priority(stage: str) -> tuple[str, ...]:
    if stage in {"project_kickoff", "project_deep_dive", "follow_up"}:
        return ("project_or_system_design", "backend_foundation", "coding", "behavioral", "general")
    if stage == "fundamental_interleave":
        return ("backend_foundation", "project_or_system_design", "coding", "behavioral", "general")
    if stage == "scenario_drill":
        return ("project_or_system_design", "backend_foundation", "coding", "general", "behavioral")
    return ("project_or_system_design", "backend_foundation", "coding", "behavioral", "general")


def _build_reference_candidates(
    *,
    stage: str,
    candidate_questions: list[dict[str, Any]],
    limit: int = 12,
) -> list[dict[str, Any]]:
    priorities = _stage_priority(stage)
    priority_rank = {question_type: index for index, question_type in enumerate(priorities)}

    def _score(item: dict[str, Any]) -> float:
        question_type = _normalize_question_type(item.get("question_type"))
        type_score = 8.0 - float(priority_rank.get(question_type, len(priorities)))
        scenario_bonus = 2.0 if (stage == "scenario_drill" and _question_text_has_scenario_hint(str(item.get("question_text") or ""))) else 0.0
        try:
            retrieval_score = float(item.get("score") or 0.0)
        except (TypeError, ValueError):
            retrieval_score = 0.0
        retrieval_score = max(0.0, min(retrieval_score, 20.0))
        return type_score + scenario_bonus + retrieval_score * 0.1

    ranked = sorted(candidate_questions, key=_score, reverse=True)
    selected: list[dict[str, Any]] = []
    type_buckets: dict[str, int] = {}
    for item in ranked:
        question_id = str(item.get("question_id") or "").strip()
        question_text = str(item.get("question_text") or "").strip()
        if not question_id or not question_text:
            continue
        question_type = _normalize_question_type(item.get("question_type"))
        if type_buckets.get(question_type, 0) >= 5:
            continue
        selected.append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "question_text": question_text,
                "company": str(item.get("company") or "").strip(),
                "role": str(item.get("role") or "").strip(),
                "score": item.get("score"),
            }
        )
        type_buckets[question_type] = type_buckets.get(question_type, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def _default_interviewer_question(stage: str, project_anchors: list[str]) -> tuple[str, str]:
    project_name = project_anchors[0] if project_anchors else "your most representative project"
    if stage in {"project_kickoff", "project_deep_dive", "follow_up"}:
        return (
            "project_or_system_design",
            f"We start from projects. In {project_name}, what was the hardest technical decision and how did you make the trade-off?",
        )
    if stage == "fundamental_interleave":
        return (
            "backend_foundation",
            "Based on your project implementation, explain how one index or cache design affected both performance and consistency.",
        )
    if stage == "scenario_drill":
        return (
            "project_or_system_design",
            "Suppose production latency and error rate spike on a core API. How would you diagnose, mitigate, and run a postmortem?",
        )
    return (
        "general",
        "Back to your project history: pick the module that best shows your depth and explain design trade-offs plus extensibility.",
    )


def interviewer_agent_pick_question(
    *,
    query: str,
    target_company: str,
    target_role: str,
    resume_text: str = "",
    jd_text: str = "",
    candidate_questions: list[dict[str, Any]],
    history_turns: list[dict[str, Any]],
    follow_up_hint: str | None = None,
    turn_index: int = 1,
    model: str | None = None,
) -> dict[str, Any]:
    stage = _infer_interview_stage(
        turn_index=max(int(turn_index), 1),
        history_turns=history_turns,
        follow_up_hint=follow_up_hint,
    )
    project_anchors = _extract_project_anchors(resume_text=resume_text, limit=4)
    skill_anchors = _extract_skill_anchors(query=query, resume_text=resume_text, jd_text=jd_text, limit=10)
    reference_candidates = _build_reference_candidates(
        stage=stage,
        candidate_questions=candidate_questions,
        limit=12,
    )

    stage_goal = {
        "follow_up": "Prioritize follow-up on vague, weak, or unsupported points from the last answer.",
        "project_kickoff": "Start from resume projects and probe context, architecture, decisions, and outcomes.",
        "project_deep_dive": "Continue project deep-dive and require trade-offs, failed attempts, and boundaries.",
        "fundamental_interleave": "Interleave fundamentals (backend basics) inside project context.",
        "scenario_drill": "Ask realistic production scenarios: diagnose, mitigate, root cause, and long-term fixes.",
        "mixed_deepening": "Continue depth-first probing while keeping project narrative as the mainline.",
    }.get(stage, "Continue depth-first probing while keeping project narrative as the mainline.")

    system_prompt = (
        "You are a senior technical interviewer. Use this interview rhythm: project deep-dive first, "
        "then interleave fundamentals, then scenario questions. Retrieved questions are references only, "
        "never copied verbatim. Generate one high-signal question for this turn and output JSON only."
    )
    response_schema = {
        "type": "object",
        "properties": {
            "question_id": {"type": "string"},
            "question_text": {"type": "string"},
            "mode": {"type": "string", "enum": ["new_question", "follow_up"]},
            "reason": {"type": "string"},
            "question_type": {
                "type": "string",
                "enum": [
                    "project_or_system_design",
                    "backend_foundation",
                    "coding",
                    "behavioral",
                    "general",
                ],
            },
            "reference_question_id": {"type": "string"},
        },
        "required": ["question_id", "question_text", "mode", "reason", "question_type"],
    }
    payload = {
        "turn_index": max(int(turn_index), 1),
        "stage": stage,
        "stage_goal": stage_goal,
        "query": query,
        "target_company": target_company,
        "target_role": target_role,
        "project_anchors": project_anchors,
        "skill_anchors": skill_anchors,
        "follow_up_hint": follow_up_hint or "",
        "history_turns": history_turns[-4:],
        "reference_candidates": reference_candidates,
        "constraints": [
            "Ask exactly one question in Chinese, 25-100 characters.",
            "Prioritize candidate projects; do not copy reference questions verbatim.",
            "If follow_up_hint is non-empty, mode must be follow_up.",
            "The question must probe implementation details, trade-offs, and outcomes.",
        ],
    }
    user_prompt = (
        "Generate the next interview question using the context below. "
        "reference_candidates are references only, not templates.\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = call_mimo_structured(
        system_prompt,
        user_prompt,
        response_schema,
        model=_resolve_model_alias(model) or _mimo_interviewer_model(),
        temperature=0.3,
        top_p=0.9,
        max_completion_tokens=1200,
    )
    question_id = str(raw.get("question_id") or "").strip()
    question_text = str(raw.get("question_text") or "").strip()
    mode = str(raw.get("mode") or "new_question").strip().lower()
    reason = str(raw.get("reason") or "").strip()
    question_type = _normalize_question_type(raw.get("question_type"))
    reference_question_id = str(raw.get("reference_question_id") or "").strip()

    if mode not in {"new_question", "follow_up"}:
        mode = "new_question"
    if str(follow_up_hint or "").strip():
        mode = "follow_up"
    if not question_text:
        default_type, default_question = _default_interviewer_question(stage, project_anchors)
        question_type = question_type if question_type != "general" else default_type
        question_text = default_question
    if not question_id:
        question_id = f"generated::{mode}::{max(int(turn_index), 1)}"
    if not reason:
        reason = f"stage={stage}; retrieval_as_reference=True"

    return {
        "question_id": question_id,
        "question_text": question_text,
        "mode": mode,
        "reason": reason,
        "question_type": question_type,
        "reference_question_id": reference_question_id,
    }


def evaluator_agent_evaluate_answer(
    *,
    question_text: str,
    answer_text: str,
    resume_text: str,
    jd_text: str,
    turn_index: int,
    max_rounds: int,
    target_company: str = "",
    target_role: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    system_prompt = (
        "You are an interview evaluator. Score this answer by accuracy, depth, structure, and resume fit, "
        "then provide concise feedback and the next-step decision. Output JSON only."
    )
    response_schema = {
        "type": "object",
        "properties": {
            "scores": {
                "type": "object",
                "properties": {
                    "accuracy": {"type": "number"},
                    "depth": {"type": "number"},
                    "structure": {"type": "number"},
                    "resume_fit": {"type": "number"},
                    "overall": {"type": "number"},
                },
                "required": ["accuracy", "depth", "structure", "resume_fit", "overall"],
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
            "improvements": {"type": "array", "items": {"type": "string"}},
            "feedback": {"type": "string"},
            "decision": {"type": "string", "enum": ["follow_up", "next_question", "finish"]},
            "follow_up_hint": {"type": "string"},
        },
        "required": ["scores", "strengths", "improvements", "feedback", "decision", "follow_up_hint"],
    }
    payload = {
        "question_text": question_text,
        "answer_text": answer_text,
        "resume_text": resume_text,
        "jd_text": jd_text,
        "turn_index": turn_index,
        "max_rounds": max_rounds,
        "target_company": target_company,
        "target_role": target_role,
    }
    user_prompt = f"Evaluate this answer and return JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    raw = call_mimo_structured(
        system_prompt,
        user_prompt,
        response_schema,
        model=_resolve_model_alias(model) or _mimo_evaluator_model(),
        temperature=0.2,
        top_p=0.9,
        max_completion_tokens=1400,
    )
    scores_payload = raw.get("scores") or {}
    scores = {
        "accuracy": _clamp_numeric_score(scores_payload.get("accuracy")),
        "depth": _clamp_numeric_score(scores_payload.get("depth")),
        "structure": _clamp_numeric_score(scores_payload.get("structure")),
        "resume_fit": _clamp_numeric_score(scores_payload.get("resume_fit")),
        "overall": _clamp_numeric_score(scores_payload.get("overall")),
    }
    decision = str(raw.get("decision") or "next_question").strip().lower()
    if decision not in {"follow_up", "next_question", "finish"}:
        decision = "next_question"
    return {
        "scores": scores,
        "strengths": [str(item).strip() for item in (raw.get("strengths") or []) if str(item).strip()][:5],
        "improvements": [str(item).strip() for item in (raw.get("improvements") or []) if str(item).strip()][:5],
        "feedback": str(raw.get("feedback") or "").strip(),
        "decision": decision,
        "follow_up_hint": str(raw.get("follow_up_hint") or "").strip(),
    }


def evaluator_agent_build_summary(
    *,
    turns: list[dict[str, Any]],
    target_company: str = "",
    target_role: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    system_prompt = (
        "You are an interview evaluator. Based on all turns, generate a structured final summary in JSON."
    )
    response_schema = {
        "type": "object",
        "properties": {
            "overall_score": {"type": "number"},
            "dimension_scores": {
                "type": "object",
                "properties": {
                    "accuracy": {"type": "number"},
                    "depth": {"type": "number"},
                    "structure": {"type": "number"},
                    "resume_fit": {"type": "number"},
                },
                "required": ["accuracy", "depth", "structure", "resume_fit"],
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
            "improvements": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["overall_score", "dimension_scores", "strengths", "improvements", "summary"],
    }
    payload = {
        "target_company": target_company,
        "target_role": target_role,
        "turns": turns,
    }
    user_prompt = f"Return final interview summary as JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    raw = call_mimo_structured(
        system_prompt,
        user_prompt,
        response_schema,
        model=_resolve_model_alias(model) or _mimo_evaluator_model(),
        temperature=0.2,
        top_p=0.9,
        max_completion_tokens=1400,
    )
    dimension_payload = raw.get("dimension_scores") or {}
    return {
        "overall_score": _clamp_numeric_score(raw.get("overall_score")),
        "dimension_scores": {
            "accuracy": _clamp_numeric_score(dimension_payload.get("accuracy")),
            "depth": _clamp_numeric_score(dimension_payload.get("depth")),
            "structure": _clamp_numeric_score(dimension_payload.get("structure")),
            "resume_fit": _clamp_numeric_score(dimension_payload.get("resume_fit")),
        },
        "strengths": [str(item).strip() for item in (raw.get("strengths") or []) if str(item).strip()][:8],
        "improvements": [str(item).strip() for item in (raw.get("improvements") or []) if str(item).strip()][:8],
        "summary": str(raw.get("summary") or "").strip(),
    }

