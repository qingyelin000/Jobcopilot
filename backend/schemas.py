from typing import List, Optional

from pydantic import BaseModel, Field


class ProjectExperience(BaseModel):
    project_name: str = Field(description="项目名称")
    role: str = Field(default="", description="候选人在该项目中的角色")
    description: str = Field(default="", description="项目描述、职责或成果原文")
    tech_stack: List[str] = Field(default_factory=list, description="该项目明确出现的技术栈")


class UserInfo(BaseModel):
    name: Optional[str] = Field(default=None, description="候选人姓名")
    education: str = Field(default="", description="教育背景概述")
    global_tech_stack: List[str] = Field(default_factory=list, description="候选人整体技术栈")
    projects: List[ProjectExperience] = Field(default_factory=list, description="项目经历列表")


class JDInfo(BaseModel):
    job_title: str = Field(default="", description="岗位名称")
    company_name: Optional[str] = Field(default=None, description="公司名称")
    must_have_skills: List[str] = Field(default_factory=list, description="硬性要求技能")
    nice_to_have_skills: List[str] = Field(default_factory=list, description="加分项技能")
    core_responsibilities: List[str] = Field(default_factory=list, description="岗位核心职责")
    business_domain: str = Field(default="", description="岗位所属业务场景")


class ProjectMatchMapping(BaseModel):
    project_name: str = Field(description="项目名称")
    matched_requirements: List[str] = Field(default_factory=list, description="该项目可支撑的 JD 要求")
    evidence_points: List[str] = Field(default_factory=list, description="来自原简历的可用证据")
    missing_or_unsupported_points: List[str] = Field(
        default_factory=list,
        description="该项目无法支撑或不应强写的要求",
    )
    rewrite_focus: List[str] = Field(default_factory=list, description="项目改写时应突出哪些信息")
    narrative_strategy: str = Field(default="", description="该项目的叙事策略")
    honesty_risks: List[str] = Field(default_factory=list, description="需要避免的夸大风险")


class ResumeJDMapping(BaseModel):
    candidate_positioning: str = Field(default="", description="面向目标 JD 的候选人定位")
    strong_match_points: List[str] = Field(default_factory=list, description="最强匹配点")
    risk_points: List[str] = Field(default_factory=list, description="风险项或缺口")
    keyword_strategy: List[str] = Field(default_factory=list, description="建议自然融入的关键词")
    project_mappings: List[ProjectMatchMapping] = Field(default_factory=list, description="项目级匹配映射")


class OptimizedProject(BaseModel):
    original_project_name: str = Field(description="原始项目名称")
    project_positioning: str = Field(default="", description="该项目面向目标 JD 的定位说明")
    optimized_bullets: List[str] = Field(default_factory=list, description="可直接用于简历的项目 bullets")


class OptimizedResume(BaseModel):
    summary_hook: str = Field(default="", description="简历整体定位句")
    skills_rewrite_suggestions: List[str] = Field(default_factory=list, description="技能区改写建议")
    optimized_projects: List[OptimizedProject] = Field(default_factory=list, description="优化后的项目经历")
