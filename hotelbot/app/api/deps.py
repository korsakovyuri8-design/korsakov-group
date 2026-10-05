from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(container: Container = Depends(get_container)) -> Iterator[Session]:
    session = container.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_staff(
    container: Container = Depends(get_container),
    x_staff_token: str | None = Header(default=None),
) -> None:
    """Shared-token auth for the staff API. Without a configured token the
    staff API is open only in dev mode."""
    expected = container.settings.staff_api_token
    if expected is None:
        if container.settings.env == "dev":
            return
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "staff API token not configured")
    if not x_staff_token or not hmac.compare_digest(x_staff_token, expected.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid staff token")


def require_demo(container: Container = Depends(get_container)) -> None:
    if not container.settings.enable_demo_endpoints:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
