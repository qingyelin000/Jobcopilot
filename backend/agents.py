import os
import json
import requests
from schemas import UserInfo, JDInfo, OptimizedResume, CoverLetter
from openai import OpenAI

def call_openrouter_structured(system_prompt: str, user_prompt: str, response_schema: dict):
    """
    通用函数：调用 DeepSeek 官方 API 获取符合特定 JSON Schema 的回答
    """
    # 按照 DeepSeek 官方文档初始化 client
    client = OpenAI(
        api_key=os.environ.get('DEEPSEEK_API_KEY'),
        base_url="https://api.deepseek.com"
    )
    
    # 强制增强的 System Prompt，要求只输出 JSON
    strong_system_prompt = f"{system_prompt}\n\n⚠️ 你必须仅返回完全符合以下 JSON Schema 的纯 JSON 数据结果，不需要任何思考过程，也不要包含任何 markdown 代码块（如 ```json ），不要有任何多余解释：\n{json.dumps(response_schema, ensure_ascii=False)}"
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": strong_system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},  # DeepSeekV3 支持强依赖输出 JSON
        temperature=0.1,  # 降低温度，提高信息提取的确定性
        stream=False
    )
    
    content = response.choices[0].message.content
    
    try:
        # 解析返回的 JSON
        parsed_data = json.loads(content)
        return parsed_data
    except json.JSONDecodeError:
        # 作为兜底：如果模型依然带了 markdown code block，清理它
        cleaned = content.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)

def parse_resume_to_json(resume_text: str) -> UserInfo:
    """
    智能体：简历解析官 (Resume Extract Agent)
    职责：从非结构化的纯文本/乱码文本中，如同外科手术般精准切分出个人信息、技术栈和项目经历。
    """
    system_prompt = """你是一个冷酷无情、极其严谨的资深技术HR与数据提取引擎。
你的唯一任务是：从用户提供的【原始简历文本】中，提取并构建出标准的 JSON 结构。

 【提取铁律 - 必须严格遵守】：
1. 绝对忠于原文：不要编造、脑补或推测用户没有写出的技能或经历。如果原文没有提到该字段对应的内容，请输出空字符串("")或空列表([])。
2. 全局技术栈聚合：请仔细扫描全文的“技能清单”或“掌握技能”板块，提取出所有的技术关键词，整理到 global_tech_stack 中。
3. 项目技术栈分离：在分析每个项目时，必须从该项目的描述中，分离出该项目具体使用到的技术栈。
4. 客观剥离：原始文本可能由于 PDF 解析出现换行错乱、冗余字符，请发挥你的推理能力，还原句子的本意。"""

    user_prompt = f"【原始简历内容】:\n{resume_text}"
    
    print("🤖 [Agent] 简历解析官正在执行切片与重组任务...")
    
    # 获取 Pydantic Schema 的 JSON 描述
    schema_dict = UserInfo.model_json_schema()
    
    raw_data = call_openrouter_structured(system_prompt, user_prompt, schema_dict)
    
    # 转换回 Pydantic 对象以利用其验证机制
    return UserInfo(**raw_data)

def parse_jd_to_json(jd_text: str) -> JDInfo:
    """岗位分析官：从 JD 文本抽取技能与考察偏好"""
    system_prompt = "分析这份技术JD，提取必须技能、加分项，并推测可能考察的系统设计/算法方向等业务场景。"
    user_prompt = f"JD要求：\n{jd_text}"
    
    schema_dict = JDInfo.model_json_schema()
    raw_data = call_openrouter_structured(system_prompt, user_prompt, schema_dict)
    return JDInfo(**raw_data)

def optimize_resume(user_info: UserInfo, jd_info: JDInfo) -> OptimizedResume:
    """简历优化师：基于 JD 重写项目经历"""
    system_prompt = "你是一个专业的简历优化师。请基于用户的经历和JD需求，对用户的简历进行优化。要求：使用 STAR 法则，将 JD 中的核心技能等关键词自然融入，而不是简单地拼接，突出匹配度。"
    user_prompt = f"【候选人信息】\n{user_info.model_dump_json(indent=2)}\n\n【目标岗位(JD)需求】\n{jd_info.model_dump_json(indent=2)}"
    
    schema_dict = OptimizedResume.model_json_schema()
    raw_data = call_openrouter_structured(system_prompt, user_prompt, schema_dict)
    return OptimizedResume(**raw_data)

def write_cover_letter(user_info: UserInfo, jd_info: JDInfo) -> CoverLetter:
    """求职信撰写师：生成匹配的求职信"""
    system_prompt = "写一封简短、自信的技术求职信。核心目标是强调该候选人的核心技能如何能切实解决该JD和公司业务场景中的痒点痛点。语气要专业、不卑不亢。"
    user_prompt = f"【候选人信息】\n{user_info.model_dump_json(indent=2)}\n\n【目标岗位(JD)需求】\n{jd_info.model_dump_json(indent=2)}"
    
    schema_dict = CoverLetter.model_json_schema()
    raw_data = call_openrouter_structured(system_prompt, user_prompt, schema_dict)
    return CoverLetter(**raw_data)
