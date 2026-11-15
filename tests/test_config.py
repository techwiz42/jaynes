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


def test_vertex_needs_no_gemini_key_but_a_project():
    s = Settings.from_env({"USE_VERTEX": "1", "VERTEX_PROJECT": "my-project", "ANTHROPIC_API_KEY": "a"})
    assert s.use_vertex and s.vertex_project == "my-project" and s.vertex_location == "global" and s.gemini_api_key == ""
    assert Settings.from_env({"USE_VERTEX": "1", "GOOGLE_CLOUD_PROJECT": "p2", "VERTEX_LOCATION": "us-central1", "ANTHROPIC_API_KEY": "a"}).vertex_location == "us-central1"
    with pytest.raises(ConfigError) as error:
        Settings.from_env({"USE_VERTEX": "1", "ANTHROPIC_API_KEY": "a"})
    assert "VERTEX_PROJECT" in str(error.value)
    with pytest.raises(ConfigError):
        Settings.from_env({"VERTEX_PROJECT": "my-project", "ANTHROPIC_API_KEY": "a"})        # a project alone does not turn Vertex on: the key is still required


def test_the_actor_uses_vertex_when_given_a_project_and_the_key_otherwise(monkeypatch):
    from google import genai
    from bicameral.actor import GeminiActor
    made = []
    monkeypatch.setattr(genai, "Client", lambda **kwargs: made.append(kwargs) or object())
    GeminiActor("a-key", "m", vertex_project="proj", vertex_location="us-central1")
    GeminiActor("a-key", "m")
    assert made == [{"vertexai": True, "project": "proj", "location": "us-central1"}, {"api_key": "a-key"}]
