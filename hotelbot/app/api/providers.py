"""Provider-facing API: signed status callbacks (see app/transactions/callbacks.py)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import get_container
from app.container import Container
from app.transactions.callbacks import handle_callback

router = APIRouter(prefix="/api/providers", tags=["providers"])
MAX_BODY_BYTES = 64 * 1024


@router.post("/{property_slug}/{provider_slug}/callbacks")
async def provider_callback(property_slug: str, provider_slug: str, request: Request, background: BackgroundTasks,
                            container: Container = Depends(get_container)) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    with container.session_factory() as session:
        result = handle_callback(session, container.txn_deps, property_slug, provider_slug,
                                 dict(request.headers), body)
        session.commit()   # rejected/duplicate events are recorded too
    background.add_task(container.kick)
    return JSONResponse(status_code=result.status_code, content=result.body)
