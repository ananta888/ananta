# RLM Recursive Context

Optional recursive analysis on top of CodeCompass hybrid retrieval and
hierarchical architecture handles. Feature flag: `codecompass_rlm_enabled`.

Simple questions stay on the normal planner. Complex questions get a
bounded plan (depth/fanout), per-step retrieval, and an
evidence-preserving merge that records conflicts instead of hiding them.

RLM may only expand HAC handles. It never walks the raw graph freely.
