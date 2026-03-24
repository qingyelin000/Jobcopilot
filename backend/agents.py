import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from schemas import UserInfo, JDInfo, OptimizedResume, CoverLetter

# 初始化大模型，建议把 OPENAI_API_KEY 配置在 .env 或者 docker-compose 的环境变量中
# 如果你使用的是其他兼容 OpenAI 接口的模型（如 DeepSeek/Qwen），可以添加 base_url 参数
def get_llm():
    return ChatOpenAI(model="gpt-4o", temperature=0.2)

def parse_resume_to_json(resume_text: str) -> UserInfo:
    """简历解析官：从原始简历提取结构化信息"""
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个资深HR，请从文本中绝对客观地提取结构化信息，不遗漏任何技术栈。缺失的信息请留空或合理推测为空列表。"),
        ("user", "原始简历内容：\n{resume_text}")
    ])
    # 强制大模型以 UserInfo 的结构输出 JSON
    chain = prompt | llm.with_structured_output(UserInfo)
    return chain.invoke({"resume_text": resume_text})

def parse_jd_to_json(jd_text: str) -> JDInfo:
    """岗位分析官：从 JD 文本抽取技能与考察偏好"""
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "分析这份技术JD，提取必须技能、加分项，并推测可能考察的系统设计/算法方向等业务场景。"),
        ("user", "JD要求：\n{jd_text}")
    ])
    chain = prompt | llm.with_structured_output(JDInfo)
    return chain.invoke({"jd_text": jd_text})

def optimize_resume(user_info: UserInfo, jd_info: JDInfo) -> OptimizedResume:
    """简历优化师：基于 JD 重写项目经历"""
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个专业的简历优化师。请基于用户的经历和JD需求，重写用户的每一个项目描述。要求：使用 STAR 法则，将 JD 中的核心技能等关键词自然融入，突出匹配度。"),
        ("user", "【候选人信息】\n{user_info}\n\n【目标岗位(JD)需求】\n{jd_info}")
    ])
    chain = prompt | llm.with_structured_output(OptimizedResume)
    return chain.invoke({
        "user_info": user_info.model_dump_json(indent=2),
        "jd_info": jd_info.model_dump_json(indent=2)
    })

def write_cover_letter(user_info: UserInfo, jd_info: JDInfo) -> CoverLetter:
    """求职信撰写师：生成匹配的求职信"""
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "写一封简短、自信的技术求职信。核心目标是强调该候选人的XX核心技能如何能切实解决该JD和公司业务场景中的YY痛点。语气要专业、不卑不亢。"),
        ("user", "【候选人信息】\n{user_info}\n\n【目标岗位(JD)需求】\n{jd_info}")
    ])
    chain = prompt | llm.with_structured_output(CoverLetter)
    return chain.invoke({
        "user_info": user_info.model_dump_json(indent=2),
        "jd_info": jd_info.model_dump_json(indent=2)
    })
