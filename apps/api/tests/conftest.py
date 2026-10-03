import pytest


@pytest.fixture(autouse=True)
def _submit_token(monkeypatch):
    monkeypatch.setenv("GRIOT_SUBMIT_TOKENS", "test-token")


@pytest.fixture(autouse=True)
def _no_warmup(monkeypatch):
    monkeypatch.setenv("GRIOT_WARM", "0")  # tests build their own service synchronously
