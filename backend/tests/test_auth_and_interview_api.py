from __future__ import annotations

from typing import Any


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_get_token(client, *, username: str, password: str = "password123") -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "access_token" in payload
    return str(payload["access_token"])


def _seed_ready_documents(app_context, *, username: str) -> tuple[int, int]:
    db = app_context["db"]
    models = app_context["models"]

    session = db.SessionLocal()
    try:
        user = session.query(models.User).filter(models.User.username == username).first()
        assert user is not None

        resume = models.ResumeDocument(
            user_id=user.id,
            title="Resume Test",
            source_filename="resume.pdf",
            source_text="Project A: designed cache layer with Redis and MySQL.",
            content_hash="resume_test_hash_1",
            status="ready",
            parsed_json={"name": username},
            interview_profile_json={"top_skills": ["Redis", "MySQL"]},
            parser_model="unit-test",
            schema_version="v1",
            error=None,
            is_active=True,
        )
        jd = models.JDDocument(
            user_id=user.id,
            title="Backend Engineer",
            source_text="Build high-concurrency backend services with Redis and MySQL.",
            content_hash="jd_test_hash_1",
            status="ready",
            parsed_json={"company_name": "Acme", "job_title": "Backend Engineer"},
            interview_profile_json={"must_have_skills": ["Redis", "MySQL"]},
            parser_model="unit-test",
            schema_version="v1",
            error=None,
            is_active=True,
        )
        session.add(resume)
        session.add(jd)
        session.commit()
        session.refresh(resume)
        session.refresh(jd)
        return int(resume.id), int(jd.id)
    finally:
        session.close()


def _mock_retriever(monkeypatch, api_module, question_payload: dict[str, Any]) -> None:
    class DummyRetriever:
        def search(self, **_: Any) -> list[dict[str, Any]]:
            return [question_payload]

    monkeypatch.setattr(api_module, "_get_retriever", lambda _backend: DummyRetriever())
    monkeypatch.setattr(api_module, "serialize_retrieved_question", lambda item: item)


def test_auth_register_login_and_get_me(client):
    token = _register_and_get_token(client, username="alice")

    me_response = client.get("/api/v1/users/me", headers=_auth_headers(token))
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["username"] == "alice"
    assert me_payload["location_consent"] is False

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()


def test_auth_register_duplicate_username_returns_400(client):
    _register_and_get_token(client, username="duplicate_user")

    second_response = client.post(
        "/api/v1/auth/register",
        json={"username": "duplicate_user", "password": "password123"},
    )
    assert second_response.status_code == 400


def test_interview_retrieve_requires_non_empty_query_context(client):
    response = client.post(
        "/api/v1/interview/retrieve",
        json={"query": "", "resume_text": "", "jd_text": ""},
    )
    assert response.status_code == 400
    assert "At least one of query/resume_text/jd_text is required." in response.text


def test_interview_retrieve_invalid_backend_returns_400(client):
    response = client.post(
        "/api/v1/interview/retrieve",
        json={"query": "redis", "backend": "v3"},
    )
    assert response.status_code == 400
    assert "Invalid backend" in response.text


def test_interview_retrieve_success_with_mocked_retriever(client, app_context, monkeypatch):
    api = app_context["api"]
    sample_question = {
        "question_id": "q-ref-1",
        "source_content_id": "source-1",
        "company": "Acme",
        "role": "Backend Engineer",
        "section": "backend",
        "publish_time": "2024-01-01T00:00:00Z",
        "question_text": "How do you handle cache stampede and cache avalanche?",
        "question_type": "backend_foundation",
        "score": 0.93,
        "score_breakdown": {"text_relevance": 0.9},
        "matched_keywords": ["Redis"],
    }
    _mock_retriever(monkeypatch, api, sample_question)

    response = client.post(
        "/api/v1/interview/retrieve",
        json={"query": "redis high concurrency", "top_k": 5},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result_count"] == 1
    assert payload["results"][0]["question_id"] == "q-ref-1"


def test_interview_start_requires_authentication(client):
    response = client.post(
        "/api/v1/interview/sessions/start",
        json={"resume_id": 1, "jd_id": 1},
    )
    assert response.status_code == 401


def test_interview_start_and_answer_follow_up_flow(client, app_context, monkeypatch):
    api = app_context["api"]
    token = _register_and_get_token(client, username="candidate")
    resume_id, jd_id = _seed_ready_documents(app_context, username="candidate")

    sample_question = {
        "question_id": "q-ref-1",
        "source_content_id": "source-1",
        "company": "Acme",
        "role": "Backend Engineer",
        "section": "backend",
        "publish_time": "2024-01-01T00:00:00Z",
        "question_text": "Tell me about a cache design from your project.",
        "question_type": "project_or_system_design",
        "score": 0.88,
    }
    _mock_retriever(monkeypatch, api, sample_question)

    def fake_pick_question(**kwargs):
        if kwargs.get("follow_up_hint"):
            return {
                "question_id": "q-follow-up-2",
                "question_text": "What metrics and rollback plan did you use for that cache change?",
                "mode": "follow_up",
                "reason": "follow_up_on_metrics",
                "question_type": "backend_foundation",
                "reference_question_id": "q-ref-1",
            }
        return {
            "question_id": "q-ref-1",
            "question_text": "Tell me about a cache design from your project.",
            "mode": "new_question",
            "reason": "project_first",
            "question_type": "project_or_system_design",
            "reference_question_id": "q-ref-1",
        }

    monkeypatch.setattr(api.agents, "interviewer_agent_pick_question", fake_pick_question)
    monkeypatch.setattr(
        api.agents,
        "evaluator_agent_evaluate_answer",
        lambda **_kwargs: {
            "scores": {
                "accuracy": 76.0,
                "depth": 72.0,
                "structure": 78.0,
                "resume_fit": 80.0,
                "overall": 76.5,
            },
            "strengths": ["Good coverage of technical details"],
            "improvements": ["Need more quantified metrics"],
            "feedback": "Good structure, add measurable outcomes.",
            "decision": "follow_up",
            "follow_up_hint": "Ask for metrics and rollback details.",
        },
    )

    start_response = client.post(
        "/api/v1/interview/sessions/start",
        headers=_auth_headers(token),
        json={"resume_id": resume_id, "jd_id": jd_id},
    )
    assert start_response.status_code == 200, start_response.text
    start_payload = start_response.json()
    assert start_payload["current_round"] == 1
    assert start_payload["question"]["question_id"] == "q-ref-1"

    session_id = start_payload["session_id"]
    answer_response = client.post(
        f"/api/v1/interview/sessions/{session_id}/answer",
        headers=_auth_headers(token),
        json={"answer_text": "I tracked hit ratio, fallback traffic, and error rate with rollback guardrails."},
    )
    assert answer_response.status_code == 200, answer_response.text
    answer_payload = answer_response.json()
    assert answer_payload["current_round"] == 2
    assert answer_payload["next_question"] is not None
    assert answer_payload["next_question"]["ask_mode"] == "follow_up"
    assert answer_payload["evaluation"]["decision"] == "follow_up"


def test_interview_answer_finishes_when_max_rounds_reached(client, app_context, monkeypatch):
    api = app_context["api"]
    token = _register_and_get_token(client, username="finisher")
    resume_id, jd_id = _seed_ready_documents(app_context, username="finisher")

    sample_question = {
        "question_id": "q-ref-final",
        "source_content_id": "source-final",
        "company": "Acme",
        "role": "Backend Engineer",
        "section": "backend",
        "publish_time": "2024-01-01T00:00:00Z",
        "question_text": "Describe one high-concurrency system you designed.",
        "question_type": "project_or_system_design",
        "score": 0.90,
    }
    _mock_retriever(monkeypatch, api, sample_question)
    monkeypatch.setattr(api, "INTERVIEW_MAX_ROUNDS", 1)
    monkeypatch.setattr(
        api.agents,
        "interviewer_agent_pick_question",
        lambda **_kwargs: {
            "question_id": "q-ref-final",
            "question_text": "Describe one high-concurrency system you designed.",
            "mode": "new_question",
            "reason": "project_first",
            "question_type": "project_or_system_design",
            "reference_question_id": "q-ref-final",
        },
    )
    monkeypatch.setattr(
        api.agents,
        "evaluator_agent_evaluate_answer",
        lambda **_kwargs: {
            "scores": {
                "accuracy": 82.0,
                "depth": 80.0,
                "structure": 79.0,
                "resume_fit": 83.0,
                "overall": 81.0,
            },
            "strengths": ["Clear thought process"],
            "improvements": ["Add capacity estimation details"],
            "feedback": "Solid answer.",
            "decision": "next_question",
            "follow_up_hint": "",
        },
    )

    def fake_finalize_session(*, session, turns, db):
        summary = {
            "overall_score": 81.0,
            "dimension_scores": {
                "accuracy": 82.0,
                "depth": 80.0,
                "structure": 79.0,
                "resume_fit": 83.0,
            },
            "strengths": ["Clear thought process"],
            "improvements": ["Add capacity estimation details"],
            "summary": "Good engineering mindset.",
        }
        session.status = "done"
        session.summary_json = summary
        session.current_question_json = None
        db.add(session)
        db.commit()
        return summary

    monkeypatch.setattr(api, "_finalize_interview_session", fake_finalize_session)

    start_response = client.post(
        "/api/v1/interview/sessions/start",
        headers=_auth_headers(token),
        json={"resume_id": resume_id, "jd_id": jd_id},
    )
    assert start_response.status_code == 200
    session_id = start_response.json()["session_id"]

    answer_response = client.post(
        f"/api/v1/interview/sessions/{session_id}/answer",
        headers=_auth_headers(token),
        json={"answer_text": "I used cache, rate limiting, and graceful degradation layers."},
    )
    assert answer_response.status_code == 200, answer_response.text
    payload = answer_response.json()
    assert payload["status"] == "done"
    assert payload["next_question"] is None
    assert payload["summary"]["overall_score"] == 81.0


def test_chat_rate_limit_returns_429(client, app_context, monkeypatch):
    api = app_context["api"]

    class DummyChatLLM:
        def __init__(self, **_kwargs: Any):
            pass

        async def ainvoke(self, _prompt: str):
            return type("LLMReply", (), {"content": "ok"})()

    monkeypatch.setattr(api, "ChatOpenAI", DummyChatLLM)
    monkeypatch.setattr(api, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(api, "RATE_LIMIT_USE_REDIS", False)
    monkeypatch.setattr(api, "RATE_LIMIT_FALLBACK_LOCAL", True)
    monkeypatch.setattr(api, "CHAT_RATE_LIMIT_USER_PER_WINDOW", 0)
    monkeypatch.setattr(api, "CHAT_RATE_LIMIT_IP_PER_WINDOW", 1)
    api._LOCAL_RATE_LIMIT_COUNTER.clear()

    first = client.post("/api/v1/chat", json={"message": "hello"})
    assert first.status_code == 200, first.text

    second = client.post("/api/v1/chat", json={"message": "hello again"})
    assert second.status_code == 429, second.text
    assert "Retry-After" in second.headers
