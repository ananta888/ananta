from types import SimpleNamespace

from flask import Flask, jsonify

from agent.bootstrap import extensions


def test_configure_cors_exposes_etag_to_allowed_frontend(monkeypatch):
    monkeypatch.setattr(
        extensions,
        "settings",
        SimpleNamespace(cors_origins="http://localhost:4200"),
    )
    app = Flask(__name__)
    extensions.configure_cors(app)

    @app.get("/resource")
    def resource():
        response = jsonify({"ok": True})
        response.headers["ETag"] = '"revision-1"'
        return response

    response = app.test_client().get(
        "/resource",
        headers={"Origin": "http://localhost:4200"},
    )

    assert response.headers["Access-Control-Allow-Origin"] == (
        "http://localhost:4200"
    )
    exposed_headers = {
        header.strip().lower()
        for header in response.headers[
            "Access-Control-Expose-Headers"
        ].split(",")
    }
    assert "etag" in exposed_headers
