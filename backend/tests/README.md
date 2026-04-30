# Backend Testing Guide

This folder contains a practical baseline for:
- API boundary and flow tests (`pytest`)
- High-concurrency pressure testing (`locust`)

## 1. Install test dependencies

From the `backend` directory:

```bash
pip install -r requirements-test.txt
```

## 2. Run functional and boundary tests

From the `backend` directory:

```bash
pytest
```

Run a single file:

```bash
pytest tests/test_auth_and_interview_api.py
```

## 3. Coverage report (optional)

```bash
pytest --cov=. --cov-report=term-missing
```

## 4. Run load test (high concurrency)

Start your backend service first (default `http://localhost:8000`), then run:

```bash
locust -f tests/load/locustfile.py --host http://localhost:8000
```

Open the Locust UI:
- http://localhost:8089

## 5. Load-test env vars

Set these before launching Locust:

```bash
LOCUST_API_PREFIX=/api/v1
LOCUST_PASSWORD=locust_password_123
LOCUST_RESUME_ID=1
LOCUST_JD_ID=1
LOCUST_ANSWER_TEXT=I will check error rate and latency first, then apply rate limit and rollback.
```

Notes:
- `LOCUST_RESUME_ID` and `LOCUST_JD_ID` are required for session start/answer tasks.
- If they are not set, Locust will still run retrieval load only.
