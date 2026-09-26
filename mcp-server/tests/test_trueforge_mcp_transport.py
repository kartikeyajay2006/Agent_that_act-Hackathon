from __future__ import annotations

import pytest

from forgesre.trueforge import TrueForge, TrueForgeError


def test_transport_support_comes_from_installed_openapi_manifest_schema():
    client = TrueForge("http://trueforge.invalid")
    client._req = lambda method, path, body=None, params=None: {
        "components": {
            "schemas": {
                "MCPServerManifest": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/RemoteMCPServerManifest"},
                        {"$ref": "#/components/schemas/TrueFoundryMcpServerManifest"},
                    ],
                    "discriminator": {
                        "propertyName": "type",
                        "mapping": {"remote": "#/Remote", "truefoundry": "#/TrueFoundry"},
                    },
                }
            }
        }
    }

    assert client.mcp_transport_types() == {"remote", "truefoundry"}


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:18900/mcp", "http://localhost:18900/mcp", "http://[::1]:18900/mcp"],
)
def test_remote_mcp_configuration_refuses_loopback_without_api_write(url: str):
    client = TrueForge("http://trueforge.invalid")
    client._req = lambda *args, **kwargs: pytest.fail("loopback must be rejected before an API request")

    with pytest.raises(TrueForgeError, match="refusing loopback remote MCP URL"):
        client.configure_mcp(url=url, token="test-token")


def test_remote_mcp_keeps_bearer_auth_and_registers_remote_manifest():
    client = TrueForge("http://trueforge.invalid")
    calls = []

    def request(method, path, body=None, params=None):
        calls.append((method, path, body))
        if path == "/api/v1/openapi.json":
            return {
                "components": {
                    "schemas": {
                        "MCPServerManifest": {
                            "discriminator": {"mapping": {"remote": "#/Remote"}}
                        }
                    }
                }
            }
        if method == "GET":
            return {"data": []}
        return {"data": {}}

    client._req = request
    client.configure_mcp(url="https://mcp.example.net/forgesre/mcp", token="test-token")

    manifest = calls[-1][2]["manifest"]
    assert manifest["type"] == "remote"
    assert manifest["url"] == "https://mcp.example.net/forgesre/mcp"
    assert manifest["auth"] == {"type": "header", "headers": {"Authorization": "Bearer test-token"}}


def test_openapi_without_transport_schema_fails_closed():
    client = TrueForge("http://trueforge.invalid")
    client._req = lambda *args, **kwargs: {"components": {"schemas": {}}}

    with pytest.raises(TrueForgeError, match="could not determine supported MCP transports"):
        client.mcp_transport_types()
