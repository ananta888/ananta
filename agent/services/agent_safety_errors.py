"""Stable domain errors shared by agent safety application services."""


class AgentSafetyDenied(RuntimeError):
    pass


__all__ = ["AgentSafetyDenied"]
