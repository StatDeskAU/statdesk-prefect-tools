"""Unit tests for the Dokploy REST wrapper (no network — uses pytest-mock)."""
from __future__ import annotations

import pytest

from statdesk_prefect_tools.dokploy.client import (
    DokployClient,
    DokployError,
    parse_env_block,
    render_env_block,
)


# --------------------------------------------------------------------------
# Pure helpers (no HTTP)
# --------------------------------------------------------------------------


def test_parse_env_block_roundtrips_simple_keys() -> None:
    raw = "PGHOST=db\nPGPORT=5432\n"
    assert parse_env_block(raw) == {"PGHOST": "db", "PGPORT": "5432"}


def test_parse_env_block_skips_comments_and_blanks() -> None:
    raw = "# header comment\n\nPGHOST=db\n\n# trailing\n"
    assert parse_env_block(raw) == {"PGHOST": "db"}


def test_parse_env_block_preserves_value_with_dollar_dollar() -> None:
    """Once Dokploy has stored the $$-escaped value, we read it back verbatim."""
    raw = "PGPASSWORD=secret$$value\n"
    assert parse_env_block(raw) == {"PGPASSWORD": "secret$$value"}


def test_parse_env_block_handles_equals_in_value() -> None:
    """A literal `=` after the first one belongs to the value."""
    raw = "URL=http://x.com/?a=b&c=d\n"
    assert parse_env_block(raw) == {"URL": "http://x.com/?a=b&c=d"}


def test_render_env_block_emits_one_per_line() -> None:
    env = {"A": "1", "B": "2", "C": "3"}
    assert render_env_block(env) == "A=1\nB=2\nC=3"


def test_render_env_block_preserves_insertion_order() -> None:
    env = {"Z": "1", "A": "2", "M": "3"}
    assert render_env_block(env) == "Z=1\nA=2\nM=3"


# --------------------------------------------------------------------------
# Constructor
# --------------------------------------------------------------------------


def test_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOKPLOY_API_KEY", raising=False)
    with pytest.raises(DokployError, match="API key missing"):
        DokployClient(api_base="https://x.example/api")


def test_client_reads_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_API_KEY", "k123")
    client = DokployClient(api_base="https://x.example/api")
    assert client.api_key == "k123"


def test_client_accepts_explicit_api_key() -> None:
    client = DokployClient(api_base="https://x.example/api/", api_key="literal")
    assert client.api_key == "literal"
    # Trailing slash on api_base is normalised
    assert client.api_base == "https://x.example/api"


# --------------------------------------------------------------------------
# HTTP methods (mocked)
# --------------------------------------------------------------------------


def test_get_compose_env_parses_env_block(mocker) -> None:
    client = DokployClient(api_base="https://x.example/api", api_key="k")
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"composeId": "abc", "env": "FOO=1\nBAR=2\n"}
    mocker.patch("requests.get", return_value=mock_resp)

    env = client.get_compose_env("abc")
    assert env == {"FOO": "1", "BAR": "2"}


def test_get_compose_env_handles_null_env(mocker) -> None:
    """Dokploy returns env=null for services with no env set."""
    client = DokployClient(api_base="https://x.example/api", api_key="k")
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"composeId": "abc", "env": None}
    mocker.patch("requests.get", return_value=mock_resp)

    assert client.get_compose_env("abc") == {}


def test_update_compose_env_sends_full_replacement(mocker) -> None:
    client = DokployClient(api_base="https://x.example/api", api_key="k")
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.text = "{}"
    mock_resp.json.return_value = {}
    post = mocker.patch("requests.post", return_value=mock_resp)

    client.update_compose_env("abc", {"NEW": "v", "OLD": "w"})

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://x.example/api/compose.update"
    body = kwargs["json"]
    assert body["composeId"] == "abc"
    assert "NEW=v" in body["env"]
    assert "OLD=w" in body["env"]


def test_get_compose_raises_on_4xx(mocker) -> None:
    client = DokployClient(api_base="https://x.example/api", api_key="k")
    mock_resp = mocker.Mock()
    mock_resp.status_code = 404
    mock_resp.text = "compose not found"
    mocker.patch("requests.get", return_value=mock_resp)

    with pytest.raises(DokployError, match="404"):
        client.get_compose("doesnotexist")
