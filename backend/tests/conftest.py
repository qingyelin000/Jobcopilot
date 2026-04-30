from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_RESET_MODULES = (
    "api",
    "db",
    "models",
    "document_assets",
)


def _clear_backend_modules() -> None:
    for module_name in _RESET_MODULES:
        if module_name in sys.modules:
            del sys.modules[module_name]


@pytest.fixture(scope="session")
def app_context(tmp_path_factory: pytest.TempPathFactory):
    db_dir: Path = tmp_path_factory.mktemp("jobcopilot_test_db")
    sqlite_path = db_dir / "jobcopilot.sqlite3"

    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{sqlite_path.as_posix()}"
    os.environ.setdefault("JWT_SECRET", "jobcopilot-test-secret")
    os.environ.setdefault("JWT_EXPIRE_MINUTES", "10080")

    _clear_backend_modules()
    db = importlib.import_module("db")
    models = importlib.import_module("models")
    api = importlib.import_module("api")

    models.Base.metadata.create_all(bind=db.engine)
    return {
        "db": db,
        "models": models,
        "api": api,
    }


@pytest.fixture(autouse=True)
def clean_database(app_context):
    db = app_context["db"]
    models = app_context["models"]

    session = db.SessionLocal()
    try:
        session.query(models.InterviewTurn).delete()
        session.query(models.InterviewSession).delete()
        session.query(models.JDDocument).delete()
        session.query(models.ResumeDocument).delete()
        session.query(models.ResumeProcessJob).delete()
        session.query(models.User).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(app_context):
    api = app_context["api"]
    with TestClient(api.app) as test_client:
        yield test_client
