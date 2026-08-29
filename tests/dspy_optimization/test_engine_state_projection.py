from worker.optimization.dspy.engine_adapter import DspyOptimizationEngineAdapter


class Signature:
    input_fields = {"goal": object(), "constraints": object()}
    output_fields = {"tasks": object()}
    instructions = "Return structured tasks."


class Demo:
    def toDict(self):
        return {"goal": "ship", "constraints": [], "tasks": []}


class Predict:
    signature = Signature()
    demos = [Demo()]


class Compiled:
    def named_predictors(self):
        return [("main", Predict())]


def test_upstream_objects_are_projected_to_closed_json_instead_of_saved_directly() -> None:
    state = DspyOptimizationEngineAdapter._state_only(Compiled(), "planning_structured_tasks")
    assert state["module_graph"][0]["module"] == "predict"
    assert state["demonstrations"][0]["goal"] == "ship"
    assert "api_key" not in repr(state)
