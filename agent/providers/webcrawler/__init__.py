"""External ananta-webcrawler provider adapters.

The external service owns browser automation, sessions, credentials and
profiles.  This package contains only Hub-side configuration, transport,
policy, routing and lifecycle adapters.
"""

from .backend_provider import (
    AnantaWebcrawlerBackendProvider,
    WebcrawlerCompletion,
    WebcrawlerProviderError,
)
from .config import AnantaWebcrawlerProviderConfig, WebcrawlerConfigError
from .policy import WebcrawlerActionPolicy
from .routing import WebcrawlerRoutingDecision, route_webcrawler_task
from .runtime_manager import WebcrawlerRuntimeManager
from .tool_provider import AnantaWebcrawlerToolProvider

__all__ = [
    "AnantaWebcrawlerBackendProvider",
    "AnantaWebcrawlerProviderConfig",
    "AnantaWebcrawlerToolProvider",
    "WebcrawlerActionPolicy",
    "WebcrawlerCompletion",
    "WebcrawlerConfigError",
    "WebcrawlerProviderError",
    "WebcrawlerRoutingDecision",
    "WebcrawlerRuntimeManager",
    "route_webcrawler_task",
]
