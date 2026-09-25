"""Read-only, deterministic knowledge base for the support-triage example."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

ARTICLES = (
    {
        "id": "KB-101",
        "title": "Restablecer MFA",
        "keywords": ("mfa", "2fa", "autenticacion", "autenticación", "codigo", "código"),
        "content": (
            "Un administrador puede restablecer MFA en Ajustes > Usuarios > Seguridad. "
            "El usuario debe volver a registrar su segundo factor al iniciar sesión."
        ),
    },
    {
        "id": "KB-202",
        "title": "Descargar facturas",
        "keywords": ("factura", "facturas", "invoice", "billing"),
        "content": (
            "Un administrador puede descargar facturas en Ajustes > Facturación > Facturas. "
            "Si falta una factura, debe abrirse una solicitud al equipo de facturación."
        ),
    },
)


def search_articles(query: str) -> list[dict[str, str]]:
    """Return only matching articles, never a guessed answer."""
    normalized = query.casefold()
    return [
        {"id": article["id"], "title": article["title"], "content": article["content"]}
        for article in ARTICLES
        if any(keyword in normalized for keyword in article["keywords"])
    ]


def create_server(host: str = "127.0.0.1", port: int = 8766) -> FastMCP:
    server = FastMCP("support-triage-kb", host=host, port=port, stateless_http=True)

    @server.tool()
    def search_support_articles(query: str) -> dict[str, object]:
        """Search fictional SaaS support articles by issue; returns matching citations or none."""
        print(f"SUPPORT_KB_SEARCH query={query!r}", flush=True)
        return {"articles": search_articles(query)}

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local support knowledge-base MCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    create_server(args.host, args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
