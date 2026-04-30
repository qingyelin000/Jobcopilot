from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


CandidateLevel = Literal[
    "intern",
    "campus",
    "1-3y",
    "3-5y",
    "5-10y",
    "10y+",
    "unknown",
]

JobTrack = Literal[
    "backend",
    "frontend",
    "mobile",
    "fullstack",
    "algorithm",
    "data",
    "devops",
    "qa",
    "product",
    "design",
    "operations",
    "general",
]

LanguageCode = Literal["zh", "en", "auto"]


class WorkExperience(BaseModel):
    company: str = Field(default="", description="Company name.")
    title: str = Field(default="", description="Job title.")
    start: str = Field(default="", description="Start month, YYYY-MM if known.")
    end: Optional[str] = Field(default=None, description="End month, YYYY-MM, or null when current.")
    team_size: Optional[int] = Field(default=None, description="Team size if mentioned.")
    individual_contribution: str = Field(default="", description="Individual contribution summary.")
    quantified_results: List[str] = Field(
        default_factory=list,
        description="Quantified outcome bullets, e.g. 'P99 from 1.2s to 240ms'.",
    )


class ProjectExperience(BaseModel):
    project_name: str = Field(description="Project name.")
    role: str = Field(default="", description="Role on the project.")
    description: str = Field(default="", description="Project description.")
    tech_stack: List[str] = Field(default_factory=list, description="Project tech stack.")
    start: str = Field(default="", description="Start month, YYYY-MM if known.")
    end: Optional[str] = Field(default=None, description="End month, YYYY-MM, or null when current.")
    team_size: Optional[int] = Field(default=None, description="Team size if mentioned.")
    individual_contribution_pct: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Self-reported contribution percentage if available.",
    )
    quantified_results: List[str] = Field(
        default_factory=list,
        description="Quantified result bullets extracted verbatim from resume.",
    )


class UserInfo(BaseModel):
    name: Optional[str] = Field(default=None, description="Candidate name.")
    education: str = Field(default="", description="Education summary.")
    global_tech_stack: List[str] = Field(default_factory=list, description="Global skill list.")
    projects: List[ProjectExperience] = Field(default_factory=list, description="Project experiences.")

    target_role: str = Field(default="", description="Self-declared target role.")
    target_cities: List[str] = Field(default_factory=list, description="Preferred work cities.")
    expected_salary_kk: Optional[Tuple[int, int]] = Field(
        default=None,
        description="Expected monthly salary range in CNY 1k unit, e.g. (25, 40).",
    )
    years_of_experience: float = Field(default=0.0, ge=0.0, description="Total years of experience.")
    level: CandidateLevel = Field(default="unknown", description="Inferred seniority bucket.")
    languages: List[str] = Field(default_factory=list, description="Spoken/written languages.")
    certificates: List[str] = Field(default_factory=list, description="Certificates and awards.")
    work_experience: List[WorkExperience] = Field(
        default_factory=list,
        description="Formal work history (excluding projects).",
    )
    track: JobTrack = Field(default="general", description="Inferred career track.")
    resume_language: LanguageCode = Field(default="auto", description="Detected resume language.")


class JDInfo(BaseModel):
    job_title: str = Field(default="", description="Job title.")
    company_name: Optional[str] = Field(default=None, description="Company name.")
    must_have_skills: List[str] = Field(default_factory=list, description="Required skills.")
    nice_to_have_skills: List[str] = Field(default_factory=list, description="Preferred skills.")
    core_responsibilities: List[str] = Field(default_factory=list, description="Core responsibilities.")
    business_domain: str = Field(default="", description="Business domain context.")

    salary_range_kk: Optional[Tuple[int, int]] = Field(
        default=None,
        description="Posted monthly salary range in CNY 1k unit if available.",
    )
    education_min: str = Field(default="", description="Minimum education requirement (raw text).")
    years_min: Optional[float] = Field(default=None, ge=0.0, description="Minimum years of experience.")
    years_max: Optional[float] = Field(default=None, ge=0.0, description="Maximum years of experience.")
    location: List[str] = Field(default_factory=list, description="Work locations / cities.")
    headcount: Optional[int] = Field(default=None, ge=1, description="Open headcount if posted.")
    industry: str = Field(default="", description="Industry tag, e.g. 互联网/金融/外企/国企.")
    style_profile: str = Field(
        default="",
        description="Recommended resume narrative style profile (互联网激进/外企正式/...).",
    )
    track: JobTrack = Field(default="general", description="Inferred role track.")
    jd_language: LanguageCode = Field(default="auto", description="Detected JD language.")


class ResumeProjectHighlight(BaseModel):
    project_name: str = Field(description="Project name.")
    role: str = Field(default="", description="Role on the project.")
    summary: str = Field(default="", description="Compact project summary.")
    tech_stack: List[str] = Field(default_factory=list, description="Key project technologies.")


class ResumeInterviewProfile(BaseModel):
    name: Optional[str] = Field(default=None, description="Candidate name.")
    education: str = Field(default="", description="Education summary.")
    top_skills: List[str] = Field(default_factory=list, description="Top candidate skills.")
    project_highlights: List[ResumeProjectHighlight] = Field(
        default_factory=list,
        description="Compact project highlights for interview setup.",
    )


class JDInterviewProfile(BaseModel):
    job_title: str = Field(default="", description="Job title.")
    company_name: Optional[str] = Field(default=None, description="Company name.")
    must_have_skills: List[str] = Field(default_factory=list, description="Core required skills.")
    nice_to_have_skills: List[str] = Field(default_factory=list, description="Additional skills.")
    core_responsibilities: List[str] = Field(
        default_factory=list,
        description="Compact responsibility summary.",
    )
    business_domain: str = Field(default="", description="Business domain.")


class ProjectMatchMapping(BaseModel):
    project_name: str = Field(description="Project name.")
    matched_requirements: List[str] = Field(default_factory=list, description="Matched JD requirements.")
    evidence_points: List[str] = Field(default_factory=list, description="Evidence from resume.")
    missing_or_unsupported_points: List[str] = Field(
        default_factory=list,
        description="Missing or unsupported requirements.",
    )
    rewrite_focus: List[str] = Field(default_factory=list, description="Rewrite focus for this project.")
    narrative_strategy: str = Field(default="", description="Narrative strategy suggestion.")
    honesty_risks: List[str] = Field(default_factory=list, description="Potential honesty risks.")


class ResumeJDMapping(BaseModel):
    candidate_positioning: str = Field(default="", description="High-level candidate positioning against JD.")
    strong_match_points: List[str] = Field(default_factory=list, description="Strong match points.")
    risk_points: List[str] = Field(default_factory=list, description="Main risk points.")
    keyword_strategy: List[str] = Field(default_factory=list, description="Keyword strategy suggestions.")
    project_mappings: List[ProjectMatchMapping] = Field(
        default_factory=list,
        description="Project-level mapping results.",
    )
    ats_coverage: Optional["AtsCoverageReport"] = Field(
        default=None,
        description="ATS keyword coverage report computed deterministically.",
    )


class OptimizedProject(BaseModel):
    original_project_name: str = Field(description="Original project name.")
    project_positioning: str = Field(default="", description="Project positioning against JD.")
    optimized_bullets: List[str] = Field(default_factory=list, description="Optimized project bullets.")


class OptimizedResume(BaseModel):
    summary_hook: str = Field(default="", description="Top summary hook.")
    skills_rewrite_suggestions: List[str] = Field(default_factory=list, description="Skills rewrite suggestions.")
    optimized_projects: List[OptimizedProject] = Field(default_factory=list, description="Optimized projects.")


class MappingQualityScore(BaseModel):
    coverage_score: int = Field(default=0, ge=0, le=100, description="Coverage of must-have requirements.")
    evidence_score: int = Field(default=0, ge=0, le=100, description="Quality of evidence support.")
    gap_score: int = Field(default=0, ge=0, le=100, description="Gap and risk identification quality.")
    actionable_score: int = Field(default=0, ge=0, le=100, description="Actionability for rewriting.")
    overall_score: int = Field(default=0, ge=0, le=100, description="Overall mapping quality.")
    strengths: List[str] = Field(default_factory=list, description="Main strengths.")
    issues: List[str] = Field(default_factory=list, description="Main issues.")
    summary: str = Field(default="", description="One-line summary.")


class RewriteQualityScore(BaseModel):
    faithfulness_score: int = Field(default=0, ge=0, le=100, description="Faithfulness to source resume.")
    jd_alignment_score: int = Field(default=0, ge=0, le=100, description="Alignment to JD requirements.")
    impact_score: int = Field(default=0, ge=0, le=100, description="Impact and result orientation.")
    readability_score: int = Field(default=0, ge=0, le=100, description="Readability and clarity.")
    ats_score: int = Field(default=0, ge=0, le=100, description="ATS keyword friendliness.")
    overall_score: int = Field(default=0, ge=0, le=100, description="Overall rewrite quality.")
    strengths: List[str] = Field(default_factory=list, description="Main strengths.")
    issues: List[str] = Field(default_factory=list, description="Main issues.")
    summary: str = Field(default="", description="One-line summary.")


class AtsKeywordHit(BaseModel):
    keyword: str
    hit: bool
    evidence: str = Field(default="", description="Where in the resume this keyword surfaced.")


class AtsCoverageReport(BaseModel):
    must_have: List[AtsKeywordHit] = Field(default_factory=list)
    nice_to_have: List[AtsKeywordHit] = Field(default_factory=list)
    must_have_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    nice_to_have_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_keywords: List[str] = Field(default_factory=list)


class FactCheckFinding(BaseModel):
    bullet: str = Field(description="The rewritten bullet that triggered the finding.")
    suspicious_claims: List[str] = Field(default_factory=list)
    severity: Literal["info", "warning", "block"] = Field(default="warning")
    suggestion: str = Field(default="")


class FactCheckReport(BaseModel):
    is_safe: bool = Field(default=True)
    findings: List[FactCheckFinding] = Field(default_factory=list)
    blocked_bullet_count: int = Field(default=0, ge=0)
    summary: str = Field(default="")


class CandidateJobFit(BaseModel):
    hard_requirement_pass: bool = Field(default=True)
    skill_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    experience_gap_years: float = Field(default=0.0)
    domain_familiarity: float = Field(default=0.0, ge=0.0, le=1.0)
    location_match: bool = Field(default=True)
    salary_match: Optional[bool] = Field(default=None)
    overall_score: int = Field(default=0, ge=0, le=100)
    recommended_action: Literal["可冲刺", "可投", "建议补强后再投", "不建议"] = Field(default="可投")
    gap_explanation: List[str] = Field(default_factory=list)
    upskill_plan: List["UpskillItem"] = Field(default_factory=list)
    summary: str = Field(default="")


class UpskillItem(BaseModel):
    topic: str
    est_hours: int = Field(default=0, ge=0)
    resources: List[str] = Field(default_factory=list)
    why: str = Field(default="")


CandidateJobFit.model_rebuild()
ResumeJDMapping.model_rebuild()
