from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, **variables) -> str:
    """Load prompt file and render simple {{var}} templates."""
    prompt_path = PROMPTS_DIR / f"{name}.md"
    template = prompt_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template
