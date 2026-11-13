import pytest

from bicameral.config import ConfigError, Settings


def test_defaults_and_overrides():
    s = Settings.from_env({"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"})
    assert (s.actor_model, s.observer_model) == ("gemini-3.1-flash-lite", "claude-haiku-5-5")
    s = Settings.from_env({"GOOGLE_API_KEY": "g", "ANTHROPIC_API_KEY": "a", "ACTOR_MODEL": "x", "OBSERVER_MODEL": "y"})
    assert (s.actor_model, s.observer_model) == ("x", "y")


def test_missing_keys_are_named_without_values():
    with pytest.raises(ConfigError) as error:
        Settings.from_env({"ANTHROPIC_API_KEY": "sekrit"})
    assert "GEMINI_API_KEY" in str(error.value) and "sekrit" not in str(error.value)


def test_keys_are_not_printed():
    s = Settings.from_env({"GEMINI_API_KEY": "gemkey123", "ANTHROPIC_API_KEY": "anthkey456"})
    assert "gemkey123" not in repr(s) and "anthkey456" not in str(s)
