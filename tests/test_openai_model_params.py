from atlas.services.openai_model_params import responses_api_kwargs


def test_gpt6_uses_reasoning_effort_none_not_temperature() -> None:
    assert responses_api_kwargs("gpt-6-luna") == {"reasoning": {"effort": "none"}}


def test_legacy_models_keep_temperature_zero() -> None:
    assert responses_api_kwargs("gpt-4o-mini") == {"temperature": 0}
