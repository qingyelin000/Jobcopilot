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
        f"寰呬慨澶嶆枃鏈?\n{broken_output}"
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
        "鍙繑鍥炰竴涓悎娉?JSON 瀵硅薄锛屼笉瑕佽緭鍑?markdown 浠ｇ爜鍧楁垨棰濆瑙ｉ噴銆俓n"
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
        "鍙繑鍥炰竴涓悎娉?JSON 瀵硅薄锛屼笉瑕佽緭鍑?markdown 浠ｇ爜鍧楁垨棰濆瑙ｉ噴銆俓n"
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
浣犳槸绠€鍘嗙粨鏋勫寲淇℃伅鎻愬彇鍔╂墜銆?璇蜂粠鍘熷涓枃绠€鍘嗘枃鏈腑鎻愬彇缁撴瀯鍖栨暟鎹€?
瑙勫垯锛?1. 鍙繚鐣欒緭鍏ユ枃鏈腑鏈夎瘉鎹殑淇℃伅銆?2. 涓嶈鏉滄挵椤圭洰鍚嶇О銆佹寚鏍囥€佹妧鏈爤鎴栬亴璐ｃ€?3. 鎶€鑳介」灏介噺鏍囧噯鍖栥€佺畝娲佽〃杈俱€?4. 缂哄け瀛楁鎸?schema 榛樿鍊艰繑鍥烇紙绌哄瓧绗︿覆鎴栫┖鏁扮粍锛夈€?5. `projects` 浼樺厛鎻愬彇鏈夊疄璐ㄥ唴瀹圭殑椤圭洰缁忓巻銆?"""

    user_prompt = f"鍘熷绠€鍘嗘枃鏈細\n{resume_text}"
    raw_data = call_deepseek_structured(
        system_prompt,
        user_prompt,
        UserInfo.model_json_schema(),
        model=_resolve_stage_model("parse", model),
    )
    return UserInfo.model_validate(raw_data)


def parse_jd_to_json(jd_text: str, model: str | None = None) -> JDInfo:
    system_prompt = """
浣犳槸宀椾綅 JD 缁撴瀯鍖栦俊鎭彁鍙栧姪鎵嬨€?璇蜂粠鍘熷涓枃 JD 鏂囨湰涓彁鍙栨嫑鑱樿姹傘€?
瑙勫垯锛?1. `must_have_skills` 浠呮斁纭€ц姹傛妧鑳姐€?2. 鍔犲垎椤规斁鍏?`nice_to_have_skills`銆?3. `core_responsibilities` 鐢ㄥ姩浣滃鍚戠煭璇€荤粨銆?4. 涓嶈琛ュ厖 JD 涓湭鍑虹幇鐨勪俊鎭€?5. 缂哄け瀛楁鎸?schema 榛樿鍊艰繑鍥炪€?"""

    user_prompt = f"鍘熷 JD 鏂囨湰锛歕n{jd_text}"
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
    pattern = rf"(?:{'|'.join(escaped)})\s*[:锛歖\s*([^\n\r]{{1,{max_len}}})"
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()[:max_len]


def _guess_job_title_from_text(jd_text: str) -> str:
    text = str(jd_text or "")
    title_from_label = _extract_label_value(
        text,
        ["宀椾綅", "宀椾綅鍚嶇О", "鑱屼綅", "鑱屼綅鍚嶇О", "鎷涜仒宀椾綅", "Job Title"],
        max_len=DEFAULT_MAX_TITLE_LEN,
    )
    if title_from_label:
        return title_from_label

    first_line = _first_non_empty_line(text)
    if first_line and len(first_line) <= 40 and re.search(
        r"(宸ョ▼甯坾寮€鍙憒绠楁硶|浜у搧|缁忕悊|涓撳|瀹炰範|璁捐甯坾鏋舵瀯甯坾鍒嗘瀽甯?",
        first_line,
    ):
        return first_line[:DEFAULT_MAX_TITLE_LEN]

    bracket_match = re.search(r"[銆怽[]([^銆慭]\n]{2,40})[銆慭]]", text)
    if bracket_match:
        bracket_value = bracket_match.group(1).strip()
        if re.search(r"(宸ョ▼甯坾寮€鍙憒绠楁硶|浜у搧|缁忕悊|涓撳|瀹炰範|璁捐甯坾鏋舵瀯甯坾鍒嗘瀽甯?", bracket_value):
            return bracket_value[:DEFAULT_MAX_TITLE_LEN]

    generic_match = re.search(
        r"([A-Za-z0-9+\-/路\u4e00-\u9fa5]{2,30}(?:宸ョ▼甯坾寮€鍙憒绠楁硶|浜у搧|缁忕悊|涓撳|瀹炰範鐢焲璁捐甯坾鏋舵瀯甯坾鍒嗘瀽甯?)",
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
浣犳槸璧勬繁鎶€鏈嫑鑱樹笓瀹跺拰绠€鍘嗙瓥鐣ラ【闂€備綘鐨勪换鍔℃槸娣卞害鍒嗘瀽鍊欓€変汉绠€鍘嗕笌鑱屼綅鎻忚堪锛圝D锛夌殑鍖归厤绋嬪害锛屽苟杈撳嚭涓ユ牸缁撴瀯鍖栫殑缁撴灉銆?
璇烽伒寰互涓嬫牳蹇冨師鍒欒繘琛屽垎鏋愶細
1. 璇佹嵁鏀拺涓轰富锛氭瘡涓尮閰嶇偣閮藉繀椤绘湁绠€鍘嗕腑鐨勫叿浣撲簨瀹炲拰椤圭洰璇佹嵁鏀拺锛岀攧鍒粎鍋滅暀鍦ㄢ€滃叧閿瘝缃楀垪鈥濆眰闈㈢殑鏃犳晥鍖归厤 銆?2. 璇嗗埆杩囧害鍖呰锛氳鎯曞皢绠€鍗曠殑鍩虹API璋冪敤杩囧害鍖呰涓哄鏉傜郴缁燂紙濡傚皢鍩虹瀵硅瘽鎷兼帴鍖呰鎴愨€淎gent鈥濓級鐨勮涓?銆傚鏋滃尮閰嶇偣缁忎笉璧锋繁搴﹁拷闂紝蹇呴』闄嶇骇璇勪及銆?3. 椋庨櫓鐐瑰瑙傝瘹瀹烇細鏄庣‘鎸囧嚭绠€鍘嗕腑缂轰箯鍖哄垎搴︼紙濡傛爣鍑嗙殑鏁欑▼椤圭洰鏃犻澶栨帰绱?锛夋垨缁忓巻鎻忚堪瀹芥硾锛堝鍙啓鈥滃弬涓?璐熻矗鈥濊€屾棤鍏蜂綋鍔ㄤ綔鍜岀粨鏋滐級鐨勯闄╋紝涓嶈杩囧害涔愯銆?4. 鑱氱劍鏍稿績鍖哄垎搴︼細璇嗗埆骞堕噸鐐硅瘎浼扮畝鍘嗕腑灞曠ず浜嗙嫭绔嬫€濊€冦€佸仛杩囨繁搴︽柟妗堝姣旀垨鎬ц兘浼樺寲鐨勯儴鍒嗐€?5. 浼樺寲鐒︾偣锛坮ewrite_focus锛夛細閽堝鍖归厤鐭澘锛屾彁渚涘叿浣撱€佸彲鎵ц鐨勫缓璁紝閬垮厤绌鸿瘽濂楄瘽銆?6. 杈撳嚭瑕佹眰锛氫繚鎸佸瑙傘€佷笓涓氾紝杈撳嚭瀹屾暣浣嗕繚鎸佺畝娲併€?"""

    user_prompt = (
        f"缁撴瀯鍖栫畝鍘嗘暟鎹細\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"缁撴瀯鍖?JD 鏁版嵁锛歕n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}"
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
浣犳槸璧勬繁鎶€鏈畝鍘嗕紭鍖栭【闂€備綘鐨勪换鍔℃槸甯姪鍊欓€変汉閲嶆瀯鍜屼紭鍖栫畝鍘嗭紝浣垮叾鍦ㄧ瓫閫夊拰闈㈣瘯涓叿澶囨瀬楂樼殑鍖哄垎搴︺€?
璇蜂弗鏍奸伒寰互涓嬩紭鍖栬鍒欏鐞嗙敤鎴风殑绠€鍘嗭細
1. 閲囩敤涓夋寮忕粨鏋勶紙鐮撮櫎鎶€鏈爤缃楀垪锛夛細椤圭洰鎻忚堪蹇呴』閲囩敤鈥滈棶棰?鈫?鏂规 鈫?閲忓寲缁撴灉鈥濈殑涓夋寮忕粨鏋勩€傚皢鎶€鏈爤鑷劧铻嶅叆鍏蜂綋鐨勫仛娉曟弿杩颁腑锛屼笉瑕佸崟鐙綏鍒楁鏃犳剰涔夌殑妗嗘灦鍚嶇О 銆?2. 寮哄寲鍔ㄤ綔涓庨噺鍖栫粨鏋滐紙鐮撮櫎宸ヤ綔鏃ュ織鍐欐硶锛夛細灏嗏€滃弬涓庘€濄€佲€滆礋璐ｂ€濄€佲€滃崗鍔┾€濈瓑妯＄硦璇嶆浛鎹负鈥滆璁♀€濄€佲€滃疄鐜扳€濄€佲€滃姣斺€濄€佲€滀紭鍖栤€濄€佲€滀慨澶嶁€濈瓑鍏蜂綋鍔ㄤ綔璇?銆傛墍鏈夌粨鏋滃繀椤绘湁鍏蜂綋鏁板瓧鏀拺锛堝鈥滃噯纭巼浠?62% 鎻愬崌鍒?81%鈥濓級锛屼弗绂佷娇鐢ㄢ€滄彁鍗囦簡鏁堟灉鈥濈瓑妯＄硦琛ㄨ堪 銆?3. 鎷掔粷杩囧害鍖呰锛堝疄浜嬫眰鏄樉娣卞害锛夛細浣犳槸浠€涔堟按骞冲氨鍐欎粈涔堟按骞筹紝浣嗚鎶婂仛鍒扮殑灞傛鍐欐繁銆傝嫢鍙槸鍩虹LLM搴旂敤锛岄噸鐐瑰啓Prompt宸ョ▼銆佸垏鍒嗙瓥鐣ユ垨涓婁笅鏂囨帶鍒?锛涜嫢鏄疉gent锛屽繀椤荤獊鍑哄叾鐗规湁鏈哄埗锛堝ReAct寰幆銆佸伐鍏疯皟鐢ㄥけ璐ユ仮澶嶃€侀槻姝㈡棤闄愬惊鐜璁★級銆?4. 鎸栨帢椤圭洰宸紓鍖栵紙鐮撮櫎鏁欑▼椤圭洰鍏呮暟锛夛細瀵逛簬鍩虹鐨勮绋嬫垨鏁欑▼椤圭洰锛屽繀椤诲睍绀衡€滃湪鏁欑▼涔嬪浣犺繕鍋氫簡浠€涔堚€?銆傞噸鐐硅ˉ鍏呮帹鐞嗛樁娈电殑鎬ц兘浼樺寲銆佽竟鐣岄棶棰橈紙濡傜煭鏂囨湰锛夌殑瑙ｅ喅杩囩▼鎴栧涓嶅悓鏂规鐨勬繁鍏ュ姣?銆?5. 鎶€鑳藉垪琛ㄢ€滃幓姘粹€濓細鍙繚鐣欏€欓€変汉鑳藉湪闈㈣瘯涓拺浣?5鍒嗛挓杩介棶鐨勬妧鏈€傛槑纭垎灞傛爣娉ㄧ啛缁冨害锛屸€滅啛鎮夆€濇剰鍛崇潃鑳界櫧鏉挎墜鍐欙紝鈥滀簡瑙ｂ€濇剰鍛崇潃鎳傚師鐞?銆傚潥鍐冲垹鍑忎负浜嗗厖鏁拌€屽爢鐮岀殑鍏抽敭璇?銆?6. 鐢ㄤ簨瀹炴浛浠ｅ舰瀹硅瘝锛堢牬闄ょ┖娲炶嚜鎴戣瘎浠凤級锛氱洿鎺ュ垹闄も€滃涔犺兘鍔涘己鈥濄€佲€滄矡閫氳兘鍔涘ソ鈥濈瓑闆朵俊鎭噺鐨勬€ф牸鎻忚堪 銆傚繀椤绘浛鎹负鍙獙璇佺殑浜嬪疄锛屽寮€婧愯础鐚紙PR锛夈€佹妧鏈崥瀹㈤槄璇婚噺銆佸鐜扮殑璁烘枃鏁伴噺鎴栫珵璧涙垚缁?銆?"""

    user_prompt = (
        f"缁撴瀯鍖栫畝鍘嗘暟鎹細\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"缁撴瀯鍖?JD 鏁版嵁锛歕n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"绠€鍘?JD 鏄犲皠缁撴灉锛歕n{mapping.model_dump_json(indent=2, ensure_ascii=False)}"
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
浣犳槸涓ユ牸鐨勨€滅畝鍘?JD 鏄犲皠璐ㄩ噺璇勫鍛樷€濄€?璇峰鏄犲皠缁撴灉鎵撳垎锛?-100锛夈€?
璇勫垎缁村害锛?- coverage_score锛歮ust-have 瑕佹眰瑕嗙洊搴︺€?- evidence_score锛氳瘉鎹川閲忎笌鍏蜂綋绋嬪害銆?- gap_score锛氱己鍙?椋庨櫓璇嗗埆瀹屾暣鎬т笌鐪熷疄鎬с€?- actionable_score锛氬鍚庣画鏀瑰啓鐨勫彲鎵ц鎬с€?- overall_score锛氱患鍚堣瘎鍒嗐€?
`strengths` 鍜?`issues` 瑕佺畝娲併€佸叿浣撱€?"""

    user_prompt = (
        f"绠€鍘嗘暟鎹細\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"JD 鏁版嵁锛歕n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"鏄犲皠杈撳嚭锛歕n{mapping.model_dump_json(indent=2, ensure_ascii=False)}"
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
浣犳槸涓ユ牸鐨勨€滅畝鍘嗘敼鍐欒川閲忚瘎瀹″憳鈥濄€?璇峰鏀瑰啓缁撴灉鎵撳垎锛?-100锛夈€?
璇勫垎缁村害锛?- faithfulness_score锛氫笌鍘熷绠€鍘嗕簨瀹炰竴鑷存€с€?- jd_alignment_score锛氫笌 JD 瑕佹眰鍜岃亴璐ｇ殑瀵归綈绋嬪害銆?- impact_score锛氬姩浣?缁撴灉琛ㄨ揪涓庡彲閲忓寲绋嬪害銆?- readability_score锛氬彲璇绘€т笌绠€娲佸害銆?- ats_score锛氬叧閿瘝瑕嗙洊涓?ATS 鍙嬪ソ搴︺€?- overall_score锛氱患鍚堣瘎鍒嗐€?
`strengths` 鍜?`issues` 瑕佺畝娲併€佸叿浣撱€?"""

    user_prompt = (
        f"绠€鍘嗘暟鎹細\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"JD 鏁版嵁锛歕n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"鏄犲皠杈撳嚭锛歕n{mapping.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"鏀瑰啓缁撴灉锛歕n{optimized_resume.model_dump_json(indent=2, ensure_ascii=False)}"
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

