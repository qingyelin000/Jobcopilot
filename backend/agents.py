import json
import os

from openai import OpenAI

from schemas import JDInfo, OptimizedResume, ResumeJDMapping, UserInfo


def call_openrouter_structured(system_prompt: str, user_prompt: str, response_schema: dict):
    client = OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )

    strict_system_prompt = (
        f"{system_prompt}\n\n"
        "You must return valid JSON only. Do not include markdown code fences or extra commentary.\n"
        f"JSON Schema:\n{json.dumps(response_schema, ensure_ascii=False)}"
    )

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": strict_system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        stream=False,
    )

    content = response.choices[0].message.content or "{}"

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)


def parse_resume_to_json(resume_text: str) -> UserInfo:
    system_prompt = """
你是严格的简历信息抽取器，只能提取原始简历文本中明确出现的事实。

抽取要求：
1. 不要杜撰项目、技能、指标、职责。
2. 尽量保留项目描述中的动作、结果、数字、业务场景和优化动作。
3. 全局技术栈放到 global_tech_stack，项目内明确出现的技术栈放到对应 project.tech_stack。
4. PDF 解析后的文本可能有断行或顺序错乱，需要尽量恢复语义，但不能脑补不存在的信息。
5. 如果字段缺失，用空字符串或空数组，不要猜测。
"""

    user_prompt = f"原始简历文本：\n{resume_text}"
    raw_data = call_openrouter_structured(system_prompt, user_prompt, UserInfo.model_json_schema())
    return UserInfo(**raw_data)


def parse_jd_to_json(jd_text: str) -> JDInfo:
    system_prompt = """
你是岗位 JD 结构化分析器。

抽取要求：
1. 提取岗位名称、公司名称（若出现）、硬性技能、加分项技能、核心职责、业务场景。
2. must_have_skills 只放硬要求或高频明确要求。
3. nice_to_have_skills 放加分项、优先项或非硬性要求。
4. core_responsibilities 用简洁短语概括候选人真正要做的事情。
5. 只基于 JD 文本，不要扩写。
"""

    user_prompt = f"岗位 JD 文本：\n{jd_text}"
    raw_data = call_openrouter_structured(system_prompt, user_prompt, JDInfo.model_json_schema())
    return JDInfo(**raw_data)


def map_resume_to_jd(user_info: UserInfo, jd_info: JDInfo) -> ResumeJDMapping:
    system_prompt = """
你是简历优化流程的第一阶段：匹配映射分析器。
你的任务不是直接改写简历，而是先判断“候选人的哪些真实经历可以支撑 JD 的哪些要求”，并指出不能强写的地方。

输出要求：
1. candidate_positioning：用一句中文总结候选人面向该 JD 的真实定位。
2. strong_match_points：列出最有说服力的匹配点。
3. risk_points：列出简历中欠缺、证据弱、或不应硬写的点。
4. keyword_strategy：只保留适合自然融入简历的关键词，不要堆术语。
5. project_mappings：逐个项目给出匹配分析。

项目映射规则：
1. matched_requirements 只写这个项目能真实支撑的 JD 要求。
2. evidence_points 只写能从原简历中直接找到的证据。
3. missing_or_unsupported_points 要明确指出缺口，避免后续幻觉改写。
4. rewrite_focus 说明该项目后续应突出的问题、动作、结果、业务场景或技术判断。
5. narrative_strategy 用一句话说明该项目应该如何讲。
6. honesty_risks 标注可能被面试追问穿帮的说法。

必须遵守：
1. 不要杜撰数字、指标、系统规模、职责边界。
2. 如果项目只是罗列技术栈，就引导后续改写聚焦“做了什么、解决了什么、产出了什么”。
3. 只有在项目证据明确体现了工具调用、规划决策、循环控制、失败恢复等特征时，才允许把项目定位为 Agent；否则按普通 LLM 应用或系统写。
4. 如果项目像课程作业或教程项目，要优先寻找其中真实存在的额外优化、实验对比、性能改进、问题排查；没有就如实标注，不要硬包装。
5. 技能和经历表达必须能经得起追问。
"""

    user_prompt = (
        f"候选人信息：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"目标 JD：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    raw_data = call_openrouter_structured(system_prompt, user_prompt, ResumeJDMapping.model_json_schema())
    return ResumeJDMapping(**raw_data)


def rewrite_resume_bullets(
    user_info: UserInfo,
    jd_info: JDInfo,
    mapping: ResumeJDMapping,
) -> OptimizedResume:
    system_prompt = """
你是简历优化流程的第二阶段：项目 bullet 改写器。
你的任务是基于原始简历事实和匹配映射，把项目经历改写成更能投递目标 JD 的中文简历 bullets。

输出要求：
1. summary_hook：一句简历顶部定位语，必须真实、克制、可落地。
2. skills_rewrite_suggestions：给出 2-5 条技能区表达建议，要求真实，不要写无法支撑的“熟悉/精通”。
3. optimized_projects：逐个项目输出可直接使用的 bullets。

bullet 改写规则：
1. 每个项目输出 2-4 条 bullets，每条只表达一个核心信息。
2. 尽量采用“问题/场景 -> 动作/方案 -> 结果/产出”的写法；如果没有量化结果，可以写具体产出或效果，但不能编数字。
3. 技术栈要自然融入动作描述，不要单独罗列成一串名词。
4. 避免“参与、协助、负责日常开发、熟悉”等空话，改成具体动作和判断。
5. 不能把基础 LLM 应用硬写成 Agent，除非映射里明确允许。
6. 对课程/教程型项目，优先突出真实存在的优化、实验、对比、性能改进或问题排查。
7. 保持中文简洁、像真实投递简历，不要写成解释文或宣传文案。
"""

    user_prompt = (
        f"候选人信息：\n{user_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"目标 JD：\n{jd_info.model_dump_json(indent=2, ensure_ascii=False)}\n\n"
        f"匹配映射：\n{mapping.model_dump_json(indent=2, ensure_ascii=False)}"
    )

    raw_data = call_openrouter_structured(system_prompt, user_prompt, OptimizedResume.model_json_schema())
    return OptimizedResume(**raw_data)
