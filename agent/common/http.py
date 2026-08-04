import logging
import math
import threading
import time
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HttpTimeout = float | int | tuple[float, float]


class HttpTransportDeadlineExceeded(RuntimeError):
    """The caller-owned absolute transport deadline elapsed."""


class HttpTransportResponseLost(RuntimeError):
    """A state-changing request may have been accepted without a response."""


class HttpTransportCancelled(RuntimeError):
    """The caller cancelled the guarded request; it must not be retried."""


def _raise_transport_failure_if_requested(
    enabled: bool,
    error_type: type[RuntimeError],
    reason_code: str,
    *,
    cause: Exception | None = None,
) -> None:
    if not enabled:
        return
    error = error_type(reason_code)
    if cause is None:
        raise error
    raise error from cause


_CANCELLED_REQUEST = object()


def _valid_timeout(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and float(value) > 0
    return bool(
        isinstance(value, tuple)
        and len(value) == 2
        and all(_valid_timeout(item) for item in value)
    )


def _effective_http_timeout(
    requested: HttpTimeout | None,
    default: HttpTimeout,
) -> HttpTimeout:
    selected = requested if _valid_timeout(requested) else default
    return selected if _valid_timeout(selected) else 30


def _validate_transport_deadline(
    deadline_monotonic: float | None,
) -> None:
    if deadline_monotonic is None:
        return
    if (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(float(deadline_monotonic))
    ):
        raise ValueError("http_transport_deadline_invalid")
    if time.monotonic() >= float(deadline_monotonic):
        raise HttpTransportDeadlineExceeded(
            "http_transport_deadline_exceeded"
        )


def _post_with_session(
    session: requests.Session,
    *,
    url: str,
    data: dict | None,
    headers: dict[str, Any],
    form: bool,
    timeout: HttpTimeout,
    transport_options: dict[str, Any],
) -> Any:
    request_data = data or {}
    if form:
        return session.post(
            url,
            data=request_data,
            headers=headers,
            timeout=timeout,
            **transport_options,
        )
    return session.post(
        url,
        json=request_data,
        headers=headers,
        timeout=timeout,
        **transport_options,
    )


def _request_tracking_context() -> tuple[Any, Any, Any, Any]:
    try:
        from agent.common.lmstudio_request_registry import (
            _get_current_context,
            is_cancelled,
            register_existing_session,
        )

        goal_id, task_id = _get_current_context()
        return goal_id, task_id, register_existing_session, is_cancelled
    except Exception:
        return None, None, None, None


def _execute_guarded_request(
    *,
    send: Callable[[], Any],
    session: requests.Session,
    goal_id: Any,
    task_id: Any,
    is_cancelled: Any,
    deadline_monotonic: float | None,
) -> Any:
    """Run one request with cancellation and a total wall-clock deadline."""

    request_box: dict[str, Any] = {
        "response": None,
        "error": None,
    }
    completed = threading.Event()
    abandoned = threading.Event()
    result_lock = threading.Lock()

    def _close_response(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _abandon_request() -> None:
        with result_lock:
            abandoned.set()
            response = request_box["response"]
            request_box["response"] = None
        _close_response(response)

    def _do_request() -> None:
        try:
            response = send()
            with result_lock:
                if abandoned.is_set():
                    close_late_response = True
                else:
                    request_box["response"] = response
                    close_late_response = False
            if close_late_response:
                _close_response(response)
        except Exception as exc:
            with result_lock:
                if not abandoned.is_set():
                    request_box["error"] = exc
        finally:
            completed.set()

    threading.Thread(target=_do_request, daemon=True).start()
    while not completed.is_set():
        if callable(is_cancelled) and is_cancelled(goal_id, task_id):
            _abandon_request()
            session.close()
            return _CANCELLED_REQUEST
        wait_seconds = 0.2
        if deadline_monotonic is not None:
            remaining = float(deadline_monotonic) - time.monotonic()
            if remaining <= 0:
                _abandon_request()
                session.close()
                raise HttpTransportDeadlineExceeded(
                    "http_transport_deadline_exceeded"
                )
            wait_seconds = min(wait_seconds, max(0.001, remaining))
        completed.wait(timeout=wait_seconds)

    if (
        deadline_monotonic is not None
        and time.monotonic() >= float(deadline_monotonic)
    ):
        _abandon_request()
        session.close()
        raise HttpTransportDeadlineExceeded(
            "http_transport_deadline_exceeded"
        )
    if request_box["error"] is not None:
        # Propagate exactly once.  A state-changing POST must never be repeated
        # outside tracking after a genuine transport exception.
        raise request_box["error"]
    return request_box["response"]


def close_http_response(response: Any) -> None:
    """Close a response and its optional deadline/cancellation session."""

    session = getattr(response, "_ananta_request_session", None)
    tracked_key = getattr(response, "_ananta_tracked_key", None)
    try:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    finally:
        if session is not None:
            try:
                from agent.common.lmstudio_request_registry import (
                    release_session,
                )

                release_session(tracked_key, session)
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass


def _classify_status(code: int) -> str:
    """Klassifiziert HTTP-Statuscodes in transient (retry sinnvoll) vs permanent."""
    if code in (408, 429) or 500 <= code < 600:
        return "transient"
    return "permanent"


def create_session(retries: int = 3, backoff_factor: float = 0.3, status_forcelist=(408, 429, 500, 502, 503, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class HttpClient:
    def __init__(self, timeout: HttpTimeout = 30, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = create_session(retries=retries)

    def head(self, url: str, timeout: Optional[int] = None) -> Any:
        try:
            r = self.session.head(url, timeout=timeout or self.timeout)
            r.raise_for_status()
            return True
        except requests.exceptions.RequestException:
            return False

    def get(
        self,
        url: str,
        params: dict | None = None,
        timeout: Optional[int] = None,
        return_response: bool = False,
        silent: bool = False,
        headers: dict | None = None,
    ) -> Any:
        try:
            request_kwargs: dict[str, Any] = {"params": params, "timeout": timeout or self.timeout}
            if headers:
                request_kwargs["headers"] = headers
            r = self.session.get(url, **request_kwargs)
            if return_response:
                return r
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            if not silent:
                logging.warning(f"HTTP GET Timeout ({timeout or self.timeout}s): {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            # Fallback für host.docker.internal
            if "host.docker.internal" in url:
                from agent.utils import get_host_gateway_ip

                gateway = get_host_gateway_ip()
                if gateway:
                    fallback_url = url.replace("host.docker.internal", gateway)
                    if not silent:
                        logging.info(
                            f"host.docker.internal verweigert Verbindung. Versuche Fallback auf Gateway: {fallback_url}"
                        )
                    return self.get(
                        fallback_url,
                        params=params,
                        timeout=timeout,
                        return_response=return_response,
                        silent=silent,
                        headers=headers,
                    )

            if not silent:
                msg = f"HTTP GET Verbindungsfehler: {url} - {e}"
                # Tipp für lokale Verbindungen (host.docker.internal oder private IPs)
                if "host.docker.internal" in url or any(p in url for p in ["127.0.0.1", "192.168.", "172.", "10."]):
                    msg += (
                        " (Tipp: Stellen Sie sicher, dass der Dienst auf dem Host GESTARTET ist. "
                        "Nutzen Sie 'setup_host_services.ps1' für Firewall/Proxy-Konfiguration.)"
                    )
                logging.error(msg)
            return None
        except requests.exceptions.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None:
                level = logging.warning if _classify_status(code) == "transient" else logging.error
                if not silent:
                    level(f"HTTP GET Fehler ({code}, {_classify_status(code)}): {url}")
            else:
                if not silent:
                    logging.error(f"HTTP GET Fehler: {url} - {e}")
            return None

    def post(
        self,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        form: bool = False,
        timeout: HttpTimeout | None = None,
        silent: bool = False,
        return_response: bool = False,
        idempotency_key: Optional[str] = None,
        allow_redirects: bool = True,
        stream: bool = False,
        deadline_monotonic: float | None = None,
        raise_on_transport_error: bool = False,
    ) -> Any:
        tracked_key = None
        tracked_session = None
        response_handed_off = False
        try:
            headers = (headers or {}).copy()
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key

            effective_timeout = _effective_http_timeout(
                timeout,
                self.timeout,
            )
            _validate_transport_deadline(deadline_monotonic)
            transport_options: dict[str, Any] = {}
            if not allow_redirects:
                transport_options["allow_redirects"] = False
            if stream:
                transport_options["stream"] = True
            request_session = self.session

            def _send_once() -> Any:
                return _post_with_session(
                    request_session,
                    url=url,
                    data=data,
                    headers=headers,
                    form=form,
                    timeout=effective_timeout,
                    transport_options=transport_options,
                )

            (
                goal_id,
                task_id,
                register_existing_session,
                is_cancelled,
            ) = _request_tracking_context()

            guarded_request = bool(
                goal_id or task_id or deadline_monotonic is not None
            )
            if guarded_request:
                tracked_session = requests.Session()
                request_session = tracked_session
                if callable(register_existing_session) and (
                    goal_id or task_id
                ):
                    try:
                        tracked_key = register_existing_session(
                            tracked_session
                        )
                    except Exception:
                        # Registry bookkeeping is best-effort and happens
                        # before the one and only network send.
                        tracked_key = None
                r = _execute_guarded_request(
                    send=_send_once,
                    session=request_session,
                    goal_id=goal_id,
                    task_id=task_id,
                    is_cancelled=is_cancelled,
                    deadline_monotonic=deadline_monotonic,
                )
                if r is _CANCELLED_REQUEST:
                    _raise_transport_failure_if_requested(
                        raise_on_transport_error,
                        HttpTransportCancelled,
                        "http_transport_cancelled",
                    )
                    return None
            else:
                r = _send_once()
            if return_response:
                if stream and tracked_session is not None:
                    setattr(
                        r,
                        "_ananta_request_session",
                        tracked_session,
                    )
                    setattr(r, "_ananta_tracked_key", tracked_key)
                    response_handed_off = True
                return r
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout as exc:
            _raise_transport_failure_if_requested(
                raise_on_transport_error,
                HttpTransportResponseLost,
                "http_transport_response_lost",
                cause=exc,
            )
            if not silent:
                logging.warning(f"HTTP POST Timeout ({effective_timeout}s): {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            # Fallback für host.docker.internal
            if (
                deadline_monotonic is None
                and "host.docker.internal" in url
            ):
                from agent.utils import get_host_gateway_ip

                gateway = get_host_gateway_ip()
                if gateway:
                    fallback_url = url.replace("host.docker.internal", gateway)
                    if not silent:
                        logging.info(
                            f"host.docker.internal verweigert Verbindung. Versuche Fallback auf Gateway: {fallback_url}"
                        )
                    return self.post(
                        fallback_url,
                        data=data,
                        headers=headers,
                        form=form,
                        timeout=timeout,
                        silent=silent,
                        return_response=return_response,
                        idempotency_key=idempotency_key,
                        allow_redirects=allow_redirects,
                        stream=stream,
                        deadline_monotonic=deadline_monotonic,
                        raise_on_transport_error=(
                            raise_on_transport_error
                        ),
                    )

            _raise_transport_failure_if_requested(
                raise_on_transport_error,
                HttpTransportResponseLost,
                "http_transport_response_lost",
                cause=e,
            )
            if not silent:
                msg = f"HTTP POST Verbindungsfehler: {url} - {e}"
                # Tipp für lokale Verbindungen (host.docker.internal oder private IPs)
                if "host.docker.internal" in url or any(p in url for p in ["127.0.0.1", "192.168.", "172.", "10."]):
                    msg += (
                        " (Tipp: Stellen Sie sicher, dass der Dienst auf dem Host GESTARTET ist. "
                        "Nutzen Sie 'setup_host_services.ps1' für Firewall/Proxy-Konfiguration.)"
                    )
                logging.error(msg)
            return None
        except requests.exceptions.RequestException as e:
            if return_response and getattr(e, "response", None) is not None:
                return e.response
            _raise_transport_failure_if_requested(
                raise_on_transport_error,
                HttpTransportResponseLost,
                "http_transport_response_lost",
                cause=e,
            )
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None:
                level = logging.warning if _classify_status(code) == "transient" else logging.error
                if not silent:
                    level(f"HTTP POST Fehler ({code}, {_classify_status(code)}): {url}")
            else:
                if not silent:
                    logging.error(f"HTTP POST Fehler: {url} - {e}")
            return None
        finally:
            if tracked_session is not None and not response_handed_off:
                try:
                    from agent.common.lmstudio_request_registry import release_session

                    release_session(tracked_key, tracked_session)
                except Exception:
                    pass
                try:
                    tracked_session.close()
                except Exception:
                    pass


# Singleton-Instanz mit Standardwerten
_default_client = None


def get_default_client(timeout: HttpTimeout = 30, retries: int = 3):
    global _default_client
    if _default_client is None or _default_client.timeout != timeout:
        _default_client = HttpClient(timeout=timeout, retries=retries)
    return _default_client
