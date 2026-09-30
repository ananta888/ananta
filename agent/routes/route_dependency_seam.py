"""Per-application dependency seam for a family of route handlers.

A route family (chat, voice, visual process, autopilot dispatch) declares its
collaborators - service getters, policies, clocks, executors - as one small
frozen dataclass. :class:`RouteDependencySeam` is the single place where the
handlers obtain that bundle:

* production code calls :meth:`RouteDependencySeam.resolve` and receives the
  bundle installed on the current Flask application, or the production default
  when the application installed none (for example a handler mounted without
  its blueprint, or code running outside an application context);
* tests replace collaborators for one application only, through
  :meth:`RouteDependencySeam.override` (scoped, restores the previous bundle) or
  :meth:`RouteDependencySeam.install` (for a throwaway test application).

The installed bundle lives in ``app.extensions`` under the seam's key, so an
override never leaks across applications or processes, and no module-level
name has to be monkeypatched. The production default is composed once, lazily,
by the factory the route family passes in.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from typing import Any, Callable, Generic, Iterator, TypeVar

from flask import current_app, has_app_context

DependencyBundle = TypeVar("DependencyBundle")


class RouteDependencySeam(Generic[DependencyBundle]):
    """Resolve and override one route family's dependency bundle per Flask app."""

    def __init__(self, extension_key: str, production_factory: Callable[[], DependencyBundle]) -> None:
        self._extension_key = extension_key
        self._production_factory = production_factory
        self._production: DependencyBundle | None = None

    @property
    def extension_key(self) -> str:
        return self._extension_key

    def production(self) -> DependencyBundle:
        """The production bundle, composed once on first use."""

        if self._production is None:
            self._production = self._production_factory()
        return self._production

    def resolve(self) -> DependencyBundle:
        """The bundle of the current application, falling back to production."""

        if has_app_context():
            installed = current_app.extensions.get(self._extension_key)
            if installed is not None:
                return installed
        return self.production()

    def resolve_for(self, app: Any | None) -> DependencyBundle:
        """The bundle installed on ``app`` (production when ``app`` is ``None``).

        For collaborators that run outside a request but own an explicit
        application reference, such as the autopilot loop's worker threads.
        """

        if app is None:
            return self.production()
        return self._current(app)

    def _current(self, app: Any) -> DependencyBundle:
        installed = getattr(app, "extensions", {}).get(self._extension_key)
        return installed if installed is not None else self.production()

    def install(self, app: Any, **replacements: Any) -> DependencyBundle:
        """Install the app's current bundle with ``replacements`` applied; returns it."""

        bundle = dataclasses.replace(self._current(app), **replacements)
        app.extensions[self._extension_key] = bundle
        return bundle

    @contextmanager
    def override(self, app: Any, **replacements: Any) -> Iterator[DependencyBundle]:
        """Replace collaborators on ``app`` for the ``with`` block, then restore."""

        missing = object()
        previous = app.extensions.get(self._extension_key, missing)
        try:
            yield self.install(app, **replacements)
        finally:
            if previous is missing:
                app.extensions.pop(self._extension_key, None)
            else:
                app.extensions[self._extension_key] = previous
