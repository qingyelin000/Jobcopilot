"""Batch A/B/C 业务能力扩展（candidate-job fit / ATS coverage / fact-check /
渲染完整简历 / coding 静态审阅 / next_actions）。

放在独立模块以避免 agents.py 进一步膨胀。所有函数都是无状态 helper，
对 agents.py 的依赖通过运行时延迟 import 解决，避免循环。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from schemas import (
    AtsCoverageReport,
    AtsKeywordHit,
    CandidateJobFit,
    FactCheckFinding,
    FactCheckReport,
    JDInfo,
    OptimizedResume,
    UpskillItem,
    UserInfo,
)


# ---------------------------------------------------------------------------
# 工具：归一化关键字 / 抽 token
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[\s,，;；/、\\.()（）\[\]【】|｜]+")


def _norm(token: str) -> str:
    return _PUNCT_RE.sub(" ", str(token or "")).strip().lower()


def _tokenize_resume_corpus(user_info: UserInfo, optimized: OptimizedResume | None = None) -> str:
    parts: list[str] = []
    parts.extend(user_info.global_tech_stack or [])
    for proj in user_info.projects or []:
        parts.append(getattr(proj, "name", "") or "")
        parts.append(getattr(proj, "role", "") or "")
        parts.append(getattr(proj, "description", "") or "")
        parts.extend(getattr(proj, "tech_stack", []) or [])
        parts.extend(getattr(proj, "responsibilities", []) or [])
        parts.extend(getattr(proj, "quantified_results", []) or [])
    for we in getattr(user_info, "work_experience", []) or []:
        parts.append(getattr(we, "company", "") or "")
        parts.append(getattr(we, "title", "") or "")
        parts.extend(getattr(we, "responsibilities", []) or [])
        parts.extend(getattr(we, "achievements", []) or [])
    if optimized is not None:
        for hl in getattr(optimized, "project_highlights", []) or []:
            parts.append(getattr(hl, "name", "") or "")
            parts.extend(getattr(hl, "rewritten_bullets", []) or [])
            parts.extend(getattr(hl, "tech_keywords", []) or [])
        parts.extend(getattr(optimized, "skills_section", []) or [])
        parts.append(getattr(optimized, "candidate_summary", "") or "")
    return " " + _norm(" ".join(parts)) + " "


# ---------------------------------------------------------------------------
# §2.3 ATS keyword coverage（确定性，不依赖 LLM）
# ---------------------------------------------------------------------------


def compute_ats_coverage(
    user_info: UserInfo,
    jd_info: JDInfo,
    optimized: OptimizedResume | None = None,
) -> AtsCoverageReport:
    corpus = _tokenize_resume_corpus(user_info, optimized)
    must = list(jd_info.must_have_skills or [])
    nice = list(jd_info.nice_to_have_skills or [])

    def _hit(kw: str) -> AtsKeywordHit:
        nk = _norm(kw)
        present = bool(nk) and (f" {nk} " in corpus or nk in corpus)
        return AtsKeywordHit(keyword=kw, present=present)

    must_hits = [_hit(k) for k in must]
    nice_hits = [_hit(k) for k in nice]
    must_total = len(must_hits) or 1
    must_pass = sum(1 for h in must_hits if h.present)
    nice_pass = sum(1 for h in nice_hits if h.present)
    nice_total = len(nice_hits) or 1
    must_ratio = must_pass / must_total if must_hits else 1.0
    nice_ratio = nice_pass / nice_total if nice_hits else 1.0
    overall = round(0.7 * must_ratio + 0.3 * nice_ratio, 4)
    missing = [h.keyword for h in must_hits if not h.present] + [
        h.keyword for h in nice_hits if not h.present
    ]
    return AtsCoverageReport(
        must_have_hits=must_hits,
        nice_to_have_hits=nice_hits,
        must_have_coverage=round(must_ratio, 4),
        nice_to_have_coverage=round(nice_ratio, 4),
        overall_coverage=overall,
        missing_keywords=missing[:30],
    )


# ---------------------------------------------------------------------------
# §2.2 Fact-check rewriting against original resume
# ---------------------------------------------------------------------------


_NUM_RE = re.compile(r"(\d+(?:\.\d+)?\s*[%w万kKWw亿]?)")


def _extract_numbers(text: str) -> list[str]:
    return [m.group(1).strip() for m in _NUM_RE.finditer(text or "")]


def factcheck_rewrite(
    optimized: OptimizedResume,
    original_user_info: UserInfo,
    *,
    model: str | None = None,
) -> FactCheckReport:
    """对重写后的简历做事实一致性检查。

    1. 先用确定性规则做"数字出现过吗 / 技术栈在原简历提到过吗"快查；
    2. 再让 LLM 在确定性快查结果之上做语义级判定（是否夸大、是否引入未声明经历）。
    """

    from agents import call_deepseek_structured, _resolve_stage_model  # 延迟导入

    original_corpus = _tokenize_resume_corpus(original_user_info)
    original_numbers = set(
        _norm(n) for n in _extract_numbers(
            " ".join(
                [
                    *(original_user_info.global_tech_stack or []),
                    *[
                        " ".join(
                            [
                                p.description or "",
                                " ".join(p.responsibilities or []),
                                " ".join(getattr(p, "quantified_results", []) or []),
                            ]
                        )
                        for p in (original_user_info.projects or [])
                    ],
                ]
            )
        )
    )

    deterministic: list[FactCheckFinding] = []
    for hl in optimized.project_highlights or []:
        for bullet in hl.rewritten_bullets or []:
            for num in _extract_numbers(bullet):
                if _norm(num) and _norm(num) not in original_numbers:
                    deterministic.append(
                        FactCheckFinding(
                            project=hl.name,
                            field="rewritten_bullet",
                            evidence_required="原简历中未出现该数字",
                            issue=f"新增量化指标 {num}",
                            severity="high",
                            suggestion="改用原简历中确实出现过的数字，或删去量化",
                        )
                    )
        for tk in hl.tech_keywords or []:
            if _norm(tk) and f" {_norm(tk)} " not in original_corpus and _norm(tk) not in original_corpus:
                deterministic.append(
                    FactCheckFinding(
                        project=hl.name,
                        field="tech_keywords",
                        evidence_required="原简历未提及该技术",
                        issue=f"新增技术栈 {tk}",
                        severity="medium",
                        suggestion="移除或替换为原简历中存在的同类技术",
                    )
                )

    system_prompt = (
        "你是简历事实校验官。给你 (a) 原始候选人结构化简历, (b) 重写后的简历高亮, "
        "(c) 程序快查发现的疑点。你需要确认/补充疑点，输出 JSON FactCheckReport。"
        "规则：1) 不允许引入原简历未出现的工作经历/职责/项目。"
        "2) 不允许编造或夸大数字、规模、影响。"
        "3) 仅当确实存在事实性问题才记入 findings。"
        "4) overall_passed = findings 中没有 severity=high 时为 true。"
        "5) 如果 deterministic findings 误报，可在 findings 中标注 severity=low + suggestion 解释。"
    )
    payload = {
        "original_resume": original_user_info.model_dump(mode="json"),
        "rewritten_highlights": [hl.model_dump(mode="json") for hl in optimized.project_highlights or []],
        "candidate_summary": optimized.candidate_summary,
        "deterministic_findings": [f.model_dump(mode="json") for f in deterministic],
    }
    user_prompt = (
        "请基于以下数据返回 FactCheckReport JSON。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = call_deepseek_structured(
            system_prompt,
            user_prompt,
            FactCheckReport.model_json_schema(),
            model=_resolve_stage_model("factcheck", model),
        )
        report = FactCheckReport.model_validate(raw)
    except Exception:  # pragma: no cover - 容错：LLM 失败时回退到确定性结果
        high = any(f.severity == "high" for f in deterministic)
        report = FactCheckReport(
            overall_passed=not high,
            findings=deterministic,
            summary="LLM 校验失败，仅基于确定性规则给出。",
        )
    if not report.findings and deterministic:
        report = FactCheckReport(
            overall_passed=not any(f.severity == "high" for f in deterministic),
            findings=deterministic,
            summary=report.summary or "确定性规则发现存在事实性疑点。",
        )
    return report


# ---------------------------------------------------------------------------
# §2.1 渲染完整简历 Markdown
# ---------------------------------------------------------------------------


def render_resume_markdown(
    user_info: UserInfo,
    optimized: OptimizedResume | None = None,
) -> str:
    lines: list[str] = []
    name = user_info.name or "候选人"
    lines.append(f"# {name}")
    if user_info.target_role or user_info.target_cities:
        meta = []
        if user_info.target_role:
            meta.append(f"目标岗位：{user_info.target_role}")
        if user_info.target_cities:
            meta.append("意向城市：" + "、".join(user_info.target_cities))
        if user_info.years_of_experience is not None:
            meta.append(f"经验：{user_info.years_of_experience} 年")
        lines.append(" · ".join(meta))
    lines.append("")
    summary = (optimized.candidate_summary if optimized else "") or ""
    if summary:
        lines.append("## 个人简介")
        lines.append(summary)
        lines.append("")
    skills = (optimized.skills_section if optimized else None) or user_info.global_tech_stack or []
    if skills:
        lines.append("## 技能")
        lines.append("- " + "、".join(skills))
        lines.append("")
    if user_info.education:
        lines.append("## 教育背景")
        lines.append(f"- {user_info.education}")
        lines.append("")
    we_list = getattr(user_info, "work_experience", []) or []
    if we_list:
        lines.append("## 工作经历")
        for we in we_list:
            head = f"### {we.company or ''} · {we.title or ''}".strip(" ·")
            lines.append(head)
            duration = " - ".join([x for x in [we.start, we.end] if x])
            if duration:
                lines.append(f"_时间：{duration}_")
            for r in we.responsibilities or []:
                lines.append(f"- {r}")
            for a in we.achievements or []:
                lines.append(f"- ✨ {a}")
            lines.append("")
    highlights = (optimized.project_highlights if optimized else None) or []
    project_section_emitted = False
    if highlights:
        lines.append("## 项目经历")
        project_section_emitted = True
        for hl in highlights:
            lines.append(f"### {hl.name}")
            if hl.summary:
                lines.append(hl.summary)
            for b in hl.rewritten_bullets or []:
                lines.append(f"- {b}")
            if hl.tech_keywords:
                lines.append("_技术栈：" + "、".join(hl.tech_keywords) + "_")
            lines.append("")
    if not project_section_emitted and (user_info.projects or []):
        lines.append("## 项目经历")
        for proj in user_info.projects or []:
            lines.append(f"### {proj.name}")
            if proj.role:
                lines.append(f"_角色：{proj.role}_")
            if proj.description:
                lines.append(proj.description)
            for r in proj.responsibilities or []:
                lines.append(f"- {r}")
            for q in getattr(proj, "quantified_results", []) or []:
                lines.append(f"- 📈 {q}")
            if proj.tech_stack:
                lines.append("_技术栈：" + "、".join(proj.tech_stack) + "_")
            lines.append("")
    if user_info.certificates:
        lines.append("## 证书")
        for c in user_info.certificates:
            lines.append(f"- {c}")
        lines.append("")
    if user_info.languages:
        lines.append("## 语言")
        lines.append("- " + "、".join(user_info.languages))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# §1.3 candidate <-> JD fit 评估
# ---------------------------------------------------------------------------


def compute_candidate_job_fit(
    user_info: UserInfo,
    jd_info: JDInfo,
    *,
    model: str | None = None,
) -> CandidateJobFit:
    from agents import call_deepseek_structured, _resolve_stage_model  # 延迟导入

    coverage = compute_ats_coverage(user_info, jd_info)
    skill_ratio = coverage.must_have_coverage
    years_user = user_info.years_of_experience or 0
    years_min = jd_info.years_min or 0
    experience_gap = max(0.0, float(years_min) - float(years_user))
    location_match = None
    if jd_info.location and user_info.target_cities:
        loc_norm = _norm(jd_info.location)
        location_match = any(loc_norm and loc_norm in _norm(c) for c in user_info.target_cities) or any(
            _norm(c) in loc_norm for c in user_info.target_cities if _norm(c)
        )
    salary_match = None
    if jd_info.salary_range_kk and user_info.expected_salary_kk:
        jd_lo, jd_hi = jd_info.salary_range_kk
        u_lo, u_hi = user_info.expected_salary_kk
        salary_match = not (u_lo > jd_hi or u_hi < jd_lo)
    hard_pass = experience_gap <= 1 and skill_ratio >= 0.5

    system_prompt = (
        "你是资深技术招聘顾问。结合候选人结构化信息、JD 与确定性命中数据，"
        "返回 CandidateJobFit JSON。要求：\n"
        "1. overall_score ∈ [0,100]，综合硬性条件 / 技能覆盖 / 经验差距 / 软性匹配。\n"
        "2. recommended_action 三选一: apply_now / improve_then_apply / not_recommended。\n"
        "3. upskill_plan 提供 3-6 条具体可执行项，每项给学习方向 + 预计周期 + 资源类型。\n"
        "4. 不要修改 hard_requirement_pass / skill_coverage_ratio / experience_gap_years 字段，"
        "若你不同意请在 gap_explanation 内说明。"
    )
    payload = {
        "user_info": user_info.model_dump(mode="json"),
        "jd_info": jd_info.model_dump(mode="json"),
        "deterministic": {
            "hard_requirement_pass": hard_pass,
            "skill_coverage_ratio": skill_ratio,
            "experience_gap_years": experience_gap,
            "location_match": location_match,
            "salary_match": salary_match,
            "missing_keywords": coverage.missing_keywords,
        },
    }
    user_prompt = "请返回 CandidateJobFit JSON：\n" + json.dumps(payload, ensure_ascii=False)
    try:
        raw = call_deepseek_structured(
            system_prompt,
            user_prompt,
            CandidateJobFit.model_json_schema(),
            model=_resolve_stage_model("fit", model),
        )
        fit = CandidateJobFit.model_validate(raw)
    except Exception:
        fit = CandidateJobFit(
            hard_requirement_pass=hard_pass,
            skill_coverage_ratio=skill_ratio,
            experience_gap_years=experience_gap,
            location_match=location_match,
            salary_match=salary_match,
            overall_score=round(skill_ratio * 100 * (0.7 if experience_gap > 0 else 1.0), 2),
            recommended_action="improve_then_apply" if not hard_pass else "apply_now",
            gap_explanation="LLM 不可用，使用确定性回退结果。",
            upskill_plan=[
                UpskillItem(
                    skill=k,
                    suggested_resources=[],
                    estimated_weeks=2,
                    rationale="JD 必备但简历未覆盖",
                )
                for k in coverage.missing_keywords[:5]
            ],
            summary="基于规则回退，建议补齐缺失关键字后再投。",
        )
    fit.hard_requirement_pass = hard_pass
    fit.skill_coverage_ratio = round(skill_ratio, 4)
    fit.experience_gap_years = round(experience_gap, 2)
    if location_match is not None:
        fit.location_match = location_match
    if salary_match is not None:
        fit.salary_match = salary_match
    return fit


# ---------------------------------------------------------------------------
# §3.4 Coding 静态审阅 stub（非真沙箱，仅启发式）
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)


def extract_code_blocks(answer_text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for m in _CODE_FENCE_RE.finditer(answer_text or ""):
        lang = (m.group(1) or "").strip().lower() or "unknown"
        code = m.group(2)
        blocks.append({"language": lang, "code": code})
    return blocks


def static_code_review(answer_text: str) -> dict[str, Any]:
    """非常轻量的代码静态启发式 review，作为真沙箱（Judge0/Docker）落地前的占位。"""

    blocks = extract_code_blocks(answer_text)
    if not blocks:
        return {
            "has_code": False,
            "blocks": [],
            "complexity_hint": "no_code_detected",
            "risks": ["未在答案中检测到代码块"],
        }
    risks: list[str] = []
    complexity_hint = "unknown"
    for b in blocks:
        code = b["code"]
        if re.search(r"for\s*\(.*\)\s*\{[^}]*for\s*\(", code) or code.count("for ") >= 2:
            complexity_hint = "likely_O(n^2)_or_higher"
        if "while True" in code or "while(true)" in code:
            risks.append("存在无显式退出条件的 while 循环，可能死循环")
        if not re.search(r"if\s|None|null|empty|len\(|\.length", code):
            risks.append("未见边界/空值处理")
        if "try" not in code and "except" not in code and "catch" not in code:
            risks.append("未见异常处理")
    return {
        "has_code": True,
        "blocks": blocks,
        "complexity_hint": complexity_hint,
        "risks": risks or ["未检测到明显问题（启发式，非真执行）"],
        "note": "这是占位静态审阅，未真正执行代码。生产环境需接入 Judge0/Docker 沙箱。",
    }


# ---------------------------------------------------------------------------
# §3.7 next_actions：根据 improvements 给复练建议
# ---------------------------------------------------------------------------


def derive_next_actions(improvements: Iterable[str], track: str | None = None) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for imp in improvements:
        text = (imp or "").strip()
        if not text:
            continue
        topic = text[:40]
        items.append(
            {
                "gap": text,
                "suggested_practice": f"针对「{topic}」做 2 道相关题或写一段 200 字复盘",
                "resource_hint": (
                    "推荐资源：内部题库 + 高赞博客（占位，后续接入题源服务）"
                ),
                "track": track or "general",
            }
        )
    return items[:8]
