"""``md-serve`` — launch the backend on the port from the environment.

The backend port is configured **entirely via ``.env``** (``HTTP_PORT``, ``HTTP_HOST``);
there is no hardcoded/fallback port in the source. Start the service with this launcher
(not raw ``uvicorn``) so the env-configured port is used:

    md-serve            # or: python -m app.server
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        print("Set HTTP_PORT (and the other required vars) in backend/.env.", file=sys.stderr)
        return 2

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.http_host,
        port=settings.http_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
