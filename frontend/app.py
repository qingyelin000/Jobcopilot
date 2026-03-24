import streamlit as st
import requests
import json
import os

st.set_page_config(page_title="JobCopilot", page_icon="🚀", layout="wide")

st.title("🚀 JobCopilot - AI 定制简历与求职信")
st.write("将简历和 JD 贴在这里，AI 会为您提取结构化信息并生成专属投递素材。")

# 获取后端 API 地址，如果环境变量没有设置，则默认使用本地端口
API_URL = os.getenv("API_URL", "http://localhost:8000")

# 创建左右两栏的多栏布局
col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.header("1. 输入源数据")
    resume_text = st.text_area("📋 请粘贴您的原始简历内容 (纯文本)：", height=300, placeholder="例如：姓名 张三\n经验 Java开发 3年...")
    jd_text = st.text_area("🎯 请粘贴目标岗位 JD (纯文本)：", height=300, placeholder="例如：岗位职责... 任职要求...")
    
    submit_btn = st.button("✨ 一键生成定制投递材料", use_container_width=True, type="primary")

with col2:
    st.header("2. AI 处理结果")
    if submit_btn:
        if not resume_text or not jd_text:
            st.warning("⚠️ 请先输入简历和 JD 内容。")
        else:
            with st.spinner("AI 正在疯狂运转中... (可能需要30-60秒)"):
                try:
                    # 发起 HTTP 请求调用后端 API
                    response = requests.post(f"{API_URL}/api/v1/process", json={
                        "resume_text": resume_text,
                        "jd_text": jd_text
                    })
                    response.raise_for_status()
                    result = response.json()
                    
                    data = result["data"]
                    
                    # 结果展示区 (使用 Tabs 分类)
                    tab1, tab2, tab3, tab4 = st.tabs(["📝 定制简历", "✉️ 求职信", "🔍 简历解析", "🔍 JD解析"])
                    
                    with tab1:
                        st.subheader("💡 优化后的项目经历 (融会贯通 JD 关键词)")
                        for proj in data["optimized_resume"]["optimized_projects"]:
                            with st.expander(f"项目：{proj['original_project_name']}", expanded=True):
                                st.markdown(f"**优化后的描述 (STAR法则):**\n\n{proj['optimized_description']}")
                                
                    with tab2:
                        st.subheader("💌 专属 Cover Letter")
                        st.write(data["cover_letter"]["content"])
                        
                    with tab3:
                        st.json(data["user_info"])
                        
                    with tab4:
                        st.json(data["jd_info"])
                        
                except Exception as e:
                    st.error(f"❌ 请求后端服务失败:\n{str(e)}")
    else:
        st.info("👈 请在左侧输入信息并点击开始按钮，结果将呈现在这里。")