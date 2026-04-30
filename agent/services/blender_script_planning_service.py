from __future__ import annotations


def build_script_proposal(*, prompt: str, capability: str = "blender.script.plan") -> dict:
    return {
        "capability": capability,
        "script": "# generated proposal\nimport bpy\n",
        "trusted": False,
        "safety_notes": ["untrusted until approved"],
        "expected_effects": ["scene mutation possible"],
        "prompt_summary": str(prompt or "")[:300],
    }
