from io import BytesIO


def test_artifact_upload_and_detail_flow(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "collection_name": "team-docs",
            "file": (BytesIO(b"# Hello\nartifact body"), "README.md"),
        },
        content_type="multipart/form-data",
    )

    assert upload_res.status_code == 201
    upload_payload = upload_res.get_json()["data"]
    artifact = upload_payload["artifact"]
    version = upload_payload["version"]
    collection = upload_payload["collection"]

    assert artifact["id"]
    assert artifact["latest_version_id"] == version["id"]
    assert artifact["latest_filename"] == "README.md"
    assert artifact["latest_sha256"]
    assert version["version_number"] == 1
    assert collection["name"] == "team-docs"

    list_res = client.get("/artifacts", headers=admin_auth_header)
    assert list_res.status_code == 200
    assert any(item["id"] == artifact["id"] for item in list_res.get_json()["data"])

    detail_res = client.get(f"/artifacts/{artifact['id']}", headers=admin_auth_header)
    assert detail_res.status_code == 200
    detail = detail_res.get_json()["data"]
    assert detail["artifact"]["id"] == artifact["id"]
    assert detail["versions"][0]["id"] == version["id"]
    assert detail["knowledge_links"][0]["artifact_id"] == artifact["id"]


def test_artifact_extract_text_document(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b'{"hello":"world"}'), "doc.json"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "text-extracted"
    assert payload["document"]["extraction_mode"] == "text-extracted"
    assert '"hello":"world"' in payload["document"]["text_content"]
    assert payload["document"]["document_metadata"]["json_root_type"] == "dict"


def test_artifact_extract_binary_document_falls_back_to_raw_only(client, admin_auth_header):
    upload_res = client.post(
        "/artifacts/upload",
        headers=admin_auth_header,
        data={
            "file": (BytesIO(b"\x89PNG\r\n\x1a\nbinary"), "image.png"),
        },
        content_type="multipart/form-data",
    )
    artifact_id = upload_res.get_json()["data"]["artifact"]["id"]

    extract_res = client.post(f"/artifacts/{artifact_id}/extract", headers=admin_auth_header)
    assert extract_res.status_code == 200
    payload = extract_res.get_json()["data"]
    assert payload["artifact"]["status"] == "raw-only"
    assert payload["document"]["extraction_mode"] == "raw-only"
    assert payload["document"]["text_content"] is None


def test_artifact_upload_requires_file(client, admin_auth_header):
    response = client.post("/artifacts/upload", headers=admin_auth_header, data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["message"] == "file_required"
