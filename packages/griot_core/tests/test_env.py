from griot_core.env import load_dotenv


def test_load_dotenv_reads_nearest_file_and_respects_real_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# c\nGRIOT_T1=from-file\nGRIOT_T2='quoted'\nGRIOT_T3=\nexport GRIOT_T4=x\n"
    )
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.setenv("GRIOT_T1", "real")
    for k in ("GRIOT_T2", "GRIOT_T3", "GRIOT_T4"):
        monkeypatch.delenv(k, raising=False)
    assert load_dotenv(sub) == tmp_path / ".env"
    import os

    assert os.environ["GRIOT_T1"] == "real"  # real env wins
    assert os.environ["GRIOT_T2"] == "quoted" and os.environ["GRIOT_T4"] == "x"
    assert "GRIOT_T3" not in os.environ  # blank slots stay unset
