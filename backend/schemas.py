from typing import List, Optional

from pydantic import BaseModel, Field


class ProjectExperience(BaseModel):
    project_name: str = Field(description="Project name.")
    role: str = Field(default="", description="Role on the project.")
    description: str = Field(default="", description="Project description.")
    tech_stack: List[str] = Field(default_factory=list, description="Project tech stack.")


class UserInfo(BaseModel):
    name: Optional[str] = Field(default=None, description="Candidate name.")
    education: str = Field(default="", description="Education summary.")
    global_tech_stack: List[str] = Field(default_factory=list, description="Global skill list.")
    projects: List[ProjectExperience] = Field(default_factory=list, description="Project experiences.")


class JDInfo(BaseModel):
    job_title: str = Field(default="", description="Job title.")
    company_name: Optional[str] = Field(default=None, description="Company name.")
    must_have_skills: List[str] = Field(default_factory=list, description="Required skills.")
    nice_to_have_skills: List[str] = Field(default_factory=list, description="Preferred skills.")
    core_responsibilities: List[str] = Field(default_factory=list, description="Core responsibilities.")
    business_domain: str = Field(default="", description="Business domain context.")


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
