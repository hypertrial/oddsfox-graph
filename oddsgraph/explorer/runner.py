"""Launch helpers for the local graph explorer."""

from __future__ import annotations

from oddsgraph.config import Settings


def run_explorer(
    settings: Settings,
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
) -> None:
    """Build and run the Dash explorer against ``settings`` artifacts."""
    from oddsgraph.explorer.app import build_app

    app = build_app(settings)
    app.run(host=host, port=port, debug=debug)
