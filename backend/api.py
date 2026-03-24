from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import agents

app = FastAPI(title="JobCopilot API Backend")

class ProcessRequest(BaseModel):
    resume_text: str
    jd_text: str

@app.post("/api/v1/process")
async def process_job_application(req: ProcessRequest):
    """一键处理流水线：解析简历 -> 解析JD -> 优化简历 -> 写求职信"""
    try:
        # 1. 并发执行基础解析 (这里先简单串行处理)
        user_info = agents.parse_resume_to_json(req.resume_text)
        jd_info = agents.parse_jd_to_json(req.jd_text)

        # 2. 生成优化内容
        optimized_resume = agents.optimize_resume(user_info, jd_info)
        cover_letter = agents.write_cover_letter(user_info, jd_info)

        return {
            "status": "success",
            "data": {
                "user_info": user_info.model_dump(),
                "jd_info": jd_info.model_dump(),
                "optimized_resume": optimized_resume.model_dump(),
                "cover_letter": cover_letter.model_dump()
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
