from biolitsynth.env import load_env_file


def test_test_suite_never_reads_dotenv(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n")
    assert load_env_file(f, environ={"BIOLITSYNTH_NO_DOTENV": "1"}) == []


def test_loads_values_skips_comments_and_never_overrides(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\n\nA=1\nexport B = 'two words'\nC=\"x=y\"\nNOEQUALS\nKEEP=file\n")
    env = {"KEEP": "shell"}
    assert load_env_file(f, environ=env) == ["A", "B", "C"]
    assert env == {"KEEP": "shell", "A": "1", "B": "two words", "C": "x=y"}


def test_missing_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope", environ={}) == []
