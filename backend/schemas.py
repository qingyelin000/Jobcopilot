from pydantic import BaseModel, Field
from typing import List, Optional

# ==========================================
# 1. 结构化简历信息 (用于 简历解析官 输出)
# ==========================================
class ProjectExperience(BaseModel):
    project_name: str = Field(description="项目名称")
    role: str = Field(description="担任角色")
    description: str = Field(description="项目描述或职责")
    tech_stack: List[str] = Field(description="该项目使用的主要技术栈")

class UserInfo(BaseModel):
    name: Optional[str] = Field(None, description="候选人姓名")
    education: str = Field(description="教育背景摘要")
    global_tech_stack: List[str] = Field(description="候选人掌握的总体技术栈/技能关键词集合")
    projects: List[ProjectExperience] = Field(description="项目经历列表")

# ==========================================
# 2. 结构化岗位要求 (用于 岗位分析官 输出)
# ==========================================
class JDInfo(BaseModel):
    job_title: str = Field(description="岗位名称")
    company_name: Optional[str] = Field(None, description="公司名称")
    must_have_skills: List[str] = Field(description="必须具备的核心技能")
    nice_to_have_skills: List[str] = Field(description="加分项技能")
    business_domain: str = Field(description="业务场景或行业领域概括")

# ==========================================
# 3. 简历优化师 和 求职信撰写师 输出
# ==========================================
class OptimizedProject(BaseModel):
    original_project_name: str = Field(description="原项目名称")
    optimized_description: str = Field(description="基于STAR法则和JD关键词重写后的项目描述（高亮或无缝融入关键词）")

class OptimizedResume(BaseModel):
    optimized_projects: List[OptimizedProject] = Field(description="优化后的项目经历对照表")

class CoverLetter(BaseModel):
    content: str = Field(description="生成的简短、自信的技术求职信正文，强调候选人技能如何解决JD痛点")