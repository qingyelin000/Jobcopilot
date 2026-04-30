from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class JobCopilotUser(HttpUser):
    wait_time = between(0.2, 1.0)

    api_prefix = os.getenv("LOCUST_API_PREFIX", "/api/v1").rstrip("/")
    password = os.getenv("LOCUST_PASSWORD", "locust_password_123")
    resume_id = _env_int("LOCUST_RESUME_ID", 0)
    jd_id = _env_int("LOCUST_JD_ID", 0)
    answer_text = os.getenv(
        "LOCUST_ANSWER_TEXT",
        "I will check error rate and latency first, then apply rate limit, degrade, and rollback.",
    )

    def on_start(self):
        self.session_id = ""
        username = f"locust_{uuid.uuid4().hex[:12]}"
        register_payload = {"username": username, "password": self.password}

        register_resp = self.client.post(
            f"{self.api_prefix}/auth/register",
            json=register_payload,
            name="auth.register",
        )
        if register_resp.status_code != 200:
            # If registration fails unexpectedly, keep user running with anonymous tasks.
            self.token = ""
            self.headers = {}
            return

        token = register_resp.json().get("access_token", "")
        self.token = str(token)
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(5)
    def retrieve_questions(self):
        payload = {
            "query": random.choice(
                [
                    "backend redis high concurrency",
                    "system design rate limiting",
                    "mysql index transaction",
                    "cache consistency",
                ]
            ),
            "top_k": random.choice([5, 8, 10]),
        }
        with self.client.post(
            f"{self.api_prefix}/interview/retrieve",
            json=payload,
            name="interview.retrieve",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected_status={response.status_code}")

    @task(2)
    def start_session(self):
        if not self.headers or self.resume_id <= 0 or self.jd_id <= 0:
            return

        payload = {
            "resume_id": self.resume_id,
            "jd_id": self.jd_id,
        }
        with self.client.post(
            f"{self.api_prefix}/interview/sessions/start",
            json=payload,
            headers=self.headers,
            name="interview.session.start",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.session_id = str(response.json().get("session_id", "")).strip()
                response.success()
                return
            response.failure(f"unexpected_status={response.status_code}")

    @task(2)
    def answer_session(self):
        if not self.headers or not self.session_id:
            return

        payload = {"answer_text": self.answer_text}
        with self.client.post(
            f"{self.api_prefix}/interview/sessions/{self.session_id}/answer",
            json=payload,
            headers=self.headers,
            name="interview.session.answer",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                body = response.json()
                next_question = body.get("next_question")
                if not next_question:
                    # Session ended. Clear local state for the next cycle.
                    self.session_id = ""
                response.success()
                return
            if response.status_code in {404, 409}:
                self.session_id = ""
            response.failure(f"unexpected_status={response.status_code}")
