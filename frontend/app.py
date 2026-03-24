import streamlit as st
import requests
import json
import os
import PyPDF2
from io import BytesIO
from streamlit_js_eval import streamlit_js_eval

# ==========================================
# 页面基础配置 & 自定义 UI 样式
# ==========================================
st.set_page_config(page_title="JobCopilot Workstation", page_icon="🚀", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* 优化全局配色与卡片阴影 */
    div.stButton > button {
        border-radius: 8px;
        font-weight: 600;
        height: 3rem;
    }
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: -1rem;
    }
    .sub-header {
        color: #64748B;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        padding: 1rem;
        border-radius: 8px;
        border: 1px solid #E2E8F0;
        margin-bottom: 1rem;
    }
    .chat-hero {
        text-align: center;
        margin-top: 8vh;
        margin-bottom: 2rem;
    }
    .chat-hero-title {
        font-size: 2rem;
        font-weight: 700;
        color: #0F172A;
        margin-bottom: 0.4rem;
    }
    .chat-hero-sub {
        color: #64748B;
        font-size: 1rem;
    }
</style>
""", unsafe_allow_html=True)

API_URL = os.getenv("API_URL", "http://localhost:8000")


def init_auth_state():
    st.session_state.setdefault("token", "")
    st.session_state.setdefault("username", "")
    st.session_state.setdefault("location_consent", False)
    st.session_state.setdefault("need_location_consent", False)
    st.session_state.setdefault("pending_prompt", "")
    st.session_state.setdefault("consent_scope", "仅本次允许")


def get_auth_headers():
    token = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_me():
    if not st.session_state.get("token"):
        return False, "未登录"
    try:
        res = requests.get(f"{API_URL}/api/v1/users/me", headers=get_auth_headers())
        res.raise_for_status()
        user = res.json()
        st.session_state.username = user.get("username", "")
        st.session_state.location_consent = bool(user.get("location_consent", False))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def login_or_register(mode: str, username: str, password: str):
    path = "/api/v1/auth/login" if mode == "登录" else "/api/v1/auth/register"
    try:
        res = requests.post(f"{API_URL}{path}", json={"username": username, "password": password})
        res.raise_for_status()
        token = res.json().get("access_token", "")
        if not token:
            return False, "未获取到 token"
        st.session_state.token = token
        ok, msg = fetch_me()
        if not ok:
            return False, f"登录成功，但获取用户信息失败: {msg}"
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def update_location_consent(consent: bool):
    try:
        res = requests.patch(
            f"{API_URL}/api/v1/users/me/preferences",
            headers=get_auth_headers(),
            json={"location_consent": consent},
        )
        res.raise_for_status()
        st.session_state.location_consent = bool(res.json().get("location_consent", False))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def get_browser_coordinates():
    script = """
    await new Promise((resolve) => {
        if (!navigator.geolocation) {
            resolve(JSON.stringify({ok: false, error: '浏览器不支持地理定位'}));
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) => resolve(JSON.stringify({
                ok: true,
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude
            })),
            (err) => resolve(JSON.stringify({ok: false, error: err.message})),
            { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 }
        );
    })
    """
    try:
        raw = streamlit_js_eval(js_expressions=script, want_output=True, key="geo_request")
        if not raw:
            return None, "未获取到定位结果，请再次点击授权按钮"

        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict) or not payload.get("ok"):
            error_msg = payload.get("error") if isinstance(payload, dict) else "定位失败"
            return None, error_msg or "定位失败"

        return {
            "latitude": float(payload.get("latitude")),
            "longitude": float(payload.get("longitude")),
        }, "ok"
    except Exception as exc:
        return None, str(exc)


init_auth_state()

# ==========================================
# 侧边栏: 用户中心 & 导航菜单
# ==========================================
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3281/3281289.png", width=60)
    st.title("JobCopilot")
    st.markdown("---")
    
    # 真实登录状态墙
    if not st.session_state.token:
        st.warning("您尚未登录")
        auth_mode = st.radio("认证方式", ["登录", "注册"], horizontal=True)
        username = st.text_input("用户名", placeholder="请输入用户名")
        password = st.text_input("密码", type="password", placeholder="至少 6 位")
        if st.button(auth_mode, use_container_width=True):
            if not username.strip() or not password.strip():
                st.error("用户名和密码不能为空")
            else:
                ok, msg = login_or_register(auth_mode, username.strip(), password)
                if ok:
                    st.success(f"{auth_mode}成功")
                    st.rerun()
                else:
                    st.error(f"{auth_mode}失败: {msg}")
    else:
        st.success(f"欢迎回来，{st.session_state.username} 👋")
        if st.button("退出登录"):
            st.session_state.token = ""
            st.session_state.username = ""
            st.session_state.location_consent = False
            st.rerun()

    st.markdown("---")
    st.markdown("###  工作台导航")
    menu = st.radio("选择服务", ["智能简历定制", "模拟面试", "数据看板"])

    if menu == "模拟面试":
        st.markdown("---")
        if st.button("🧹 清空对话", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

# ==========================================
# 主页面: 智能简历定制引擎
# ==========================================
if menu == "智能简历定制":
    if not st.session_state.token:
        st.info("👋 请先在左侧边栏登录体验完整功能。")
        st.stop()

    st.markdown('<div class="main-header"> AI 简历与 JD 靶向分析引擎</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">上传您的简历并输入岗位描述，系统即将生成深度分析报告与定制化投递材料。</div>', unsafe_allow_html=True)

    # 第一部分：输入区 (横向布局)
    col1, col2 = st.columns(2, gap="large")
    
    resume_text = ""
    with col1:
        st.subheader(" 1. 提交个人简历")
        upload_mode = st.radio("简历录入方式：", ["📎 上传 PDF", "📝 纯文本粘贴"], horizontal=True)
        
        if upload_mode == "📎 上传 PDF":
            uploaded_file = st.file_uploader("点击或拖拽上传 PDF 简历文件", type=["pdf"])
            if uploaded_file is not None:
                # 解析 PDF 文本
                try:
                    reader = PyPDF2.PdfReader(uploaded_file)
                    resume_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
                    st.success(f"✅ PDF 解析成功！共提取 {len(resume_text)} 字符。")
                    
                    # 💡新增：通过折叠面板让用户直观看到从 PDF 提取出的纯文本内容
                    with st.expander("👀 点击查看 PDF 内部提取出的纯文本 (您可在此直接验证提取质量)", expanded=False):
                        st.text(resume_text)
                        
                except Exception as e:
                    st.error("解析 PDF 失败: " + str(e))
        else:
            resume_text = st.text_area("📋 粘贴原始简历：", height=250, placeholder="粘贴文字版简历...", label_visibility="collapsed")

    with col2:
        st.subheader("🏢 2. 目标岗位描述 (JD)")
        st.caption("粘贴公司发布的该岗位完整 JD 文本")
        jd_text = st.text_area("JD：", height=230, placeholder="【岗位职责】\n1. 负责...\n\n【任职要求】\n1. 精通...", label_visibility="collapsed")

    st.markdown("<br>", unsafe_allow_html=True)
    
    # 底部居中大按钮
    _, center_col, _ = st.columns([1, 2, 1])
    with center_col:
        submit_btn = st.button("✨ 生成简历深度分析报告与定制素材", type="primary", use_container_width=True)

    st.markdown("---")

    # 第二部分：分析报告与结果展示区
    if submit_btn:
        if not resume_text.strip() or not jd_text.strip():
            st.error("⚠️ 简历内容和 JD 都不能为空，请完整提交！")
        else:
            with st.spinner("🧠 智能体多机协同运算中 (解析简历 -> 解析 JD -> 匹配分析 -> STAR 法则重写)..."):
                try:
                    # 发起 HTTP 请求
                    response = requests.post(f"{API_URL}/api/v1/process", json={
                        "resume_text": resume_text,
                        "jd_text": jd_text
                    })
                    response.raise_for_status()
                    data = response.json()["data"]
                    
                    st.success("🎉 联合分析完成！")
                    
                    # 报表面板
                    tab_report, tab_resume, tab_cover, tab_json = st.tabs([
                        "📊 靶向匹配分析报告", 
                        "✨ STAR 深度定制简历", 
                        "✉️ 专属 Cover Letter", 
                        "⚙️ 底层数据矩阵"
                    ])
                    
                    # [Tab 1] 分析报告
                    with tab_report:
                        rep_col1, rep_col2 = st.columns(2)
                        with rep_col1:
                            st.markdown("### 👤 候选人画像 (被提取)")
                            st.info(f"**最高教育**: {data['user_info'].get('education', '未知')}")
                            st.markdown(f"**提取到的全局技术栈**:\n`{'` `'.join(data['user_info'].get('global_tech_stack', []))}`")
                        
                        with rep_col2:
                            st.markdown("### 🎯 JD 考察重点 (被提取)")
                            st.warning(f"**核心必需技能**:\n`{'` `'.join(data['jd_info'].get('must_have_skills', []))}`")
                            st.success(f"**业务场景 / 行业领域**:\n{data['jd_info'].get('business_domain', '未明确')}")

                        st.markdown('<div class="metric-card"><strong>💡 面试前瞻提示</strong>：重点关注【核心必需技能】与您【全局技术栈】的重合区，并在面试中准备相关【业务场景】的实战案例。</div>', unsafe_allow_html=True)

                    # [Tab 2] 定制简历
                    with tab_resume:
                        st.markdown("### 💡 您的项目经验已智能优化")
                        st.caption("系统已将 JD 关键词无缝融入您的项目描述中，并采用 STAR(情境、任务、行动、结果) 展现。")
                        for proj in data["optimized_resume"]["optimized_projects"]:
                            with st.expander(f"📦 优化后项目：{proj['original_project_name']}", expanded=True):
                                st.write(proj['optimized_description'])

                    # [Tab 3] 求职信
                    with tab_cover:
                        st.markdown("### 💌 匹配该岗位的专属求职信")
                        st.caption("复制此文本作为邮件正文或直接投递附言：")
                        st.text_area("Cover Letter 预览", value=data["cover_letter"]["content"], height=300, label_visibility="collapsed")
                        
                    # [Tab 4] 原始 JSON
                    with tab_json:
                        st.json(data)
                        
                except Exception as e:
                    st.error(f"❌ 服务器请求异常，请检查后端是否正常运行: {str(e)}")

elif menu == "模拟面试":
    st.markdown(
        '''
        <div class="chat-hero">
            <div class="chat-hero-title">今天想找什么工作？</div>
            <div class="chat-hero-sub">告诉我岗位、城市或偏好，我会联网帮你检索并整理结果。</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )
    
    # 初始化历史聊天记录
    if "messages" not in st.session_state:
        st.session_state.messages = []

    def call_chat_api(
        message: str,
        location_consent: bool | None = None,
        consent_scope: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ):
        try:
            payload = {"message": message}
            if location_consent is not None:
                payload["location_consent"] = location_consent
            if consent_scope:
                payload["consent_scope"] = consent_scope
            if latitude is not None and longitude is not None:
                payload["latitude"] = latitude
                payload["longitude"] = longitude

            res = requests.post(
                f"{API_URL}/api/v1/chat",
                headers=get_auth_headers(),
                json=payload,
            )
            res.raise_for_status()
            body = res.json()
            return body.get("reply", "没有任何返回"), bool(body.get("need_location_consent", False))
        except Exception as exc:
            return f"❌ 服务器请求异常: {str(exc)}", False

    # if not st.session_state.messages:
    #     col1, col2, col3 = st.columns(3)
    #     with col1:
    #         st.info("🔎 帮我在附近找找 Python 的工作")
    #     with col2:
    #         st.info("🔎 找上海外企 Java 后端岗位")
    #     with col3:
    #         st.info("🔎 给我一份后端面试准备清单")

    # 展示历史消息
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 聊天输入框
    if prompt := st.chat_input("给 JobCopilot 发消息..."):
        # 将用户输入存入状态并渲染
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # AI 思考与回答区
        with st.chat_message("assistant"):
            with st.spinner("🤖 正在联网检索并整理结果..."):
                reply_text, need_consent = call_chat_api(prompt)
                st.markdown(reply_text)
                st.session_state.messages.append({"role": "assistant", "content": reply_text})
                st.session_state.need_location_consent = need_consent
                st.session_state.pending_prompt = prompt if need_consent else ""

    if st.session_state.need_location_consent:
        st.warning("需要定位授权才能继续附近职位检索。")
        if not st.session_state.token:
            st.info("请先在左侧登录后再授权定位。")
        else:
            st.session_state.consent_scope = st.radio(
                "定位授权方式",
                ["仅本次允许", "始终允许"],
                horizontal=True,
                key="consent_scope_radio",
            )
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("✅ 同意定位并继续", use_container_width=True):
                    coords, geo_msg = get_browser_coordinates()
                    if not coords:
                        st.error(f"定位失败: {geo_msg}")
                    else:
                        scope = st.session_state.consent_scope
                        if scope == "始终允许":
                            ok, msg = update_location_consent(True)
                            if not ok:
                                st.error(f"授权失败: {msg}")
                                st.stop()

                        retry_prompt = st.session_state.pending_prompt or "帮我在附近找工作"
                        with st.chat_message("assistant"):
                            with st.spinner("🤖 已授权，正在重新检索..."):
                                retry_reply, need_consent_again = call_chat_api(
                                    retry_prompt,
                                    location_consent=True,
                                    consent_scope="always" if scope == "始终允许" else "once",
                                    latitude=coords["latitude"],
                                    longitude=coords["longitude"],
                                )
                                st.markdown(retry_reply)
                                st.session_state.messages.append({"role": "assistant", "content": retry_reply})
                                st.session_state.need_location_consent = need_consent_again
                                if not need_consent_again:
                                    st.session_state.pending_prompt = ""
            with col_no:
                if st.button("取消", use_container_width=True):
                    update_location_consent(False)
                    st.session_state.need_location_consent = False
                    st.session_state.pending_prompt = ""

else:
    st.title("📊 数据看板")
    st.info("🚧 此处未来将接入 SQLite，展示您的每日投递转化率及漏斗分析。")