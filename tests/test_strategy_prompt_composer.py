from types import SimpleNamespace

from agent.services.strategy_prompt_composer import StrategyPromptComposer


def _context(base_prompt: str):
    description = "Fasse die Architektur-Dokumente zusammen. " + "Material " * 50
    return SimpleNamespace(task={"description": description, "task_kind": "analysis"}, goal_id="G", task_id="T",
                           rendered_system_prompt="", base_prompt=base_prompt), description


def test_description_already_in_the_user_prompt_is_not_repeated_in_the_system_prompt():
    context, description = _context(base_prompt="")
    context.base_prompt = description + "\n\nCitation contract"
    system = StrategyPromptComposer().compose_system_prompt(
        context=context, prompt_context_bundle={}, strategy_contract={"role": "r", "output_contract": "o"})
    assert description not in system and "Task description:" not in system


def test_description_is_added_when_the_user_prompt_is_something_else():
    context, description = _context(base_prompt="Kurze Anweisung")
    system = StrategyPromptComposer().compose_system_prompt(
        context=context, prompt_context_bundle={}, strategy_contract={"role": "r", "output_contract": "o"})
    assert "Task description:" in system and description.strip() in system
