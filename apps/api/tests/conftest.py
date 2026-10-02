import pytest


@pytest.fixture(autouse=True)
def _submit_token(monkeypatch):
    monkeypatch.setenv("GRIOT_SUBMIT_TOKENS", "test-token")
