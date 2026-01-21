from prompts import load_prompt


def test_load_prompt_contains_required_section():
    prompt = load_prompt("area_extract")
    assert "必須抽出項目" in prompt
