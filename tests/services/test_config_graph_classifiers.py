from agent.services.config_graph_classifiers import _classify_rule_character
def test_classify_rule_character_uses_path_policy_signals() -> None:
    assert _classify_rule_character(["full_llm"], []) == "kein_vollstaendiges_llm"
    assert _classify_rule_character(["deploy"], ["pytest"]) == "eingeschraenkt"
    assert _classify_rule_character([], ["pytest"]) == "selektiv_erlaubt"
