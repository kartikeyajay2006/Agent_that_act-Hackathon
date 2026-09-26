from __future__ import annotations

from mcp.server.transport_security import TransportSecurityMiddleware

from forgesre import server
from forgesre.server import BearerAuth, transport_security_settings


def test_localhost_host_is_allowed_without_public_url(monkeypatch):
    monkeypatch.delenv("FORGESRE_MCP_URL", raising=False)
    settings = transport_security_settings()
    middleware = TransportSecurityMiddleware(settings)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*"]
    assert middleware._validate_host("localhost:18900") is True
    assert middleware._validate_host("127.0.0.1:18900") is True


def test_configured_public_hostname_is_allowed(monkeypatch):
    monkeypatch.setenv(
        "FORGESRE_MCP_URL", "https://knowledge-subsidiary-conditioning-collecting.trycloudflare.com/mcp"
    )
    settings = transport_security_settings()
    middleware = TransportSecurityMiddleware(settings)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == [
        "127.0.0.1:*",
        "localhost:*",
        "knowledge-subsidiary-conditioning-collecting.trycloudflare.com",
        "knowledge-subsidiary-conditioning-collecting.trycloudflare.com:*",
    ]
    assert middleware._validate_host("knowledge-subsidiary-conditioning-collecting.trycloudflare.com") is True
    assert middleware._validate_host("knowledge-subsidiary-conditioning-collecting.trycloudflare.com:443") is True


def test_unrelated_host_is_rejected(monkeypatch):
    monkeypatch.setenv(
        "FORGESRE_MCP_URL", "https://knowledge-subsidiary-conditioning-collecting.trycloudflare.com/mcp"
    )
    middleware = TransportSecurityMiddleware(transport_security_settings())

    assert middleware._validate_host("attacker.example:18900") is False


def test_build_app_passes_transport_security_and_keeps_bearer_auth(monkeypatch):
    monkeypatch.setenv("FORGESRE_MCP_URL", "https://mcp.example.test/mcp")
    monkeypatch.setenv("FORGESRE_MCP_TOKEN", "test-bearer-token")
    captured = {}
    app = object()

    monkeypatch.setattr("forgesre.dashboard.register", lambda *_args: None)
    monkeypatch.setattr(server.mcp, "streamable_http_app", lambda **kwargs: captured.update(kwargs) or app)

    wrapped = server.build_app()

    assert isinstance(wrapped, BearerAuth)
    assert wrapped.app is app
    assert wrapped.token == "test-bearer-token"
    assert captured["host"] == "127.0.0.1"
    assert captured["transport_security"].enable_dns_rebinding_protection is True
