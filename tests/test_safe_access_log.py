import logging

from tests.support import authenticated_client


def test_access_log_omits_query_string(caplog) -> None:
    client, _, _ = authenticated_client()

    with caplog.at_level(logging.INFO, logger="app.access"):
        response = client.get("/api/session?code=secret-authorization-code")

    assert response.status_code == 200
    assert "GET /api/session 200" in caplog.text
    assert "secret-authorization-code" not in caplog.text
    assert "code=" not in caplog.text
