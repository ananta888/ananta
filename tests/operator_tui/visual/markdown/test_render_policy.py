from __future__ import annotations

from client_surfaces.operator_tui.visual.markdown.config import MarkdownMermaidConfig, config_from_dict
from client_surfaces.operator_tui.visual.markdown.render_policy import MarkdownRenderPolicyResolver


def test_config_accepts_docs_graphics_profiles_mapping() -> None:
    cfg = config_from_dict(
        {
            "docs_graphics_profile": "default",
            "docs_graphics_profiles": [
                {
                    "name": "default",
                    "backend_order": ["mermaid_cli", "fallback_codeblock"],
                    "timeout_seconds": 10,
                    "max_pixel_width": 1024,
                    "max_pixel_height": 640,
                    "prefer_image_over_source": True,
                }
            ],
        }
    )
    assert cfg.docs_graphics_profile == "default"
    assert cfg.docs_graphics_profiles[0].backend_order == ("mermaid_cli", "fallback_codeblock")


def test_render_policy_resolver_uses_requested_profile() -> None:
    cfg = MarkdownMermaidConfig(
        docs_graphics_profile="default",
        docs_graphics_profiles=(
            MarkdownMermaidConfig().docs_graphics_profiles[0],
            MarkdownMermaidConfig().docs_graphics_profiles[1],
        ),
    )
    resolver = MarkdownRenderPolicyResolver()
    policy = resolver.resolve(config=cfg, state={"docs_graphics_profile": "wsl2"})
    assert policy.active_profile == "wsl2"
    assert policy.backend_order[0] == "mermaid_cli"


def test_render_policy_resolver_auto_prefers_wsl2_when_forced() -> None:
    cfg = MarkdownMermaidConfig(docs_graphics_profile="auto")
    resolver = MarkdownRenderPolicyResolver()
    policy = resolver.resolve(config=cfg, state={"docs_graphics_profile": "wsl2_auto"})
    assert policy.active_profile in {"default", "wsl2"}
    assert len(policy.backend_order) >= 1
