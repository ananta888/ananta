"""Fixed transport admission; real destination observations live separately."""

from unittest.mock import Mock, call

import pytest

from worker.meet_media.browser_network import FixedMeetOrigin, restrict_meet_browser_network


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "http://meet.test",
        "https://meet.test/",
        "https://MEET.test",
        "https://u@meet.test",
        "https://meet.test?x",
        "https://meet.test#x",
        "https://meet.test:0",
        "https://meet.test:bad",
        "https://meet.test\\other",
        "https://meet%2etest",
        "https://meet.test\n",
        "https://*.test",
        "https://meet.test;connect-src",
        "https://meet.test,other.test",
        "https://meet.test:",
        "https://meet.test:0443",
    ],
)
def test_invalid_origin_cannot_install_partial_routes(origin):
    context = Mock()
    with pytest.raises(ValueError):
        restrict_meet_browser_network(context, origin)
    assert not context.mock_calls


@pytest.mark.parametrize("suffix", ["/", "/machine", "/assets/test.js", "/config?version=1"])
def test_exact_origin_and_only_matching_secure_socket(suffix):
    policy = FixedMeetOrigin("https://meet.test")
    assert policy.allows("https://meet.test" + suffix)
    assert policy.allows("wss://meet.test" + suffix, websocket=True)
    assert not policy.allows("wss://meet.test" + suffix)
    assert not policy.allows("https://meet.test" + suffix, websocket=True)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://other.test/",
        "https://meet.test.other.test/",
        "https://meet.test:444/",
        "http://meet.test/",
        "ws://meet.test/",
        "file:///etc/passwd",
        "data:text/plain,test",
        "https://u:secret@meet.test/",
        "https://meet.test@other.test/",
        "https://meet.test./",
        "https://MEET.test/",
        "https://meet.test\\@other.test/",
        "https://meet.test\n/",
        "https://meet.test/#secret",
        "https://meet.test/" + "x" * 8192,
    ],
)
def test_other_authorities_schemes_credentials_and_ambiguous_urls_fail_closed(url):
    assert not FixedMeetOrigin("https://meet.test").allows(url)


def routes():
    context = Mock()
    restrict_meet_browser_network(context, "https://meet.test")
    assert [c[0] for c in context.mock_calls] == ["route"]
    route = Mock()
    route.request.method, route.request.url = "GET", "https://meet.test/config"
    response = route.fetch.return_value
    response.url, response.status = route.request.url, 200
    response.headers = {"content-type": "text/plain"}
    return context.route.call_args.args[1], context, route, response


def test_success_fetches_without_redirects_or_retries_then_releases_response():
    handler, _, route, response = routes()
    handler(route)
    assert route.mock_calls == [
        call.fetch(max_redirects=0, max_retries=0, timeout=2000),
        call.fulfill(
            response=response,
            headers={"content-type": "text/plain", "content-security-policy": FixedMeetOrigin("https://meet.test").csp},
        ),
        call.fetch().dispose(),
    ]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "get"])
def test_unneeded_http_methods_are_denied_before_network(method):
    handler, _, route, _ = routes()
    route.request.method = method
    handler(route)
    route.abort.assert_called_once_with("blockedbyclient")
    route.fetch.assert_not_called()


@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 307, 308, 399])
def test_no_redirect_response_reaches_browser(status):
    handler, _, route, response = routes()
    response.status = status
    handler(route)
    route.fulfill.assert_not_called()
    route.abort.assert_called_once_with("blockedbyclient")
    response.dispose.assert_called_once()


@pytest.mark.parametrize("failure", ["origin", "response-origin", "fetch", "fulfill", "closing"])
def test_network_error_never_continues_or_retries(failure):
    handler, _, route, response = routes()
    if failure == "origin":
        route.request.url = "https://other.test/"
    elif failure == "response-origin":
        response.url = "https://other.test/"
    else:
        getattr(route, "fulfill" if failure == "fulfill" else "fetch").side_effect = RuntimeError("private")
        if failure == "closing":
            route.abort.side_effect = RuntimeError("closed")
    handler(route)
    route.continue_.assert_not_called()
    route.fallback.assert_not_called()
    assert route.fetch.call_count == int(failure != "origin")
    assert response.dispose.call_count == int(failure in {"response-origin", "fulfill"})


@pytest.mark.parametrize("original", ["connect-src 'none'", "default-src 'self'; script-src 'none'"])
def test_existing_csp_is_preserved_as_intersecting_policy_without_socket_mock(original):
    handler, context, route, response = routes()
    response.headers["content-security-policy"] = original
    handler(route)
    assert (
        route.fulfill.call_args.kwargs["headers"]["content-security-policy"]
        == original + ", " + FixedMeetOrigin("https://meet.test").csp
    )
    assert response.headers["content-security-policy"] == original
    context.route_web_socket.assert_not_called()
    context.add_init_script.assert_not_called()
