"""FastAPI app that plays CoolTech's CRM, FSM and ERP systems."""

from fastapi import Depends, FastAPI

from app.admin import router as admin_router
from app.chaos import ChaosMiddleware, ChaosSettings
from app.crm.router import router as crm_router
from app.erp.router import router as erp_router
from app.errors import ERROR_RESPONSES, register_error_handlers
from app.fsm.router import router as fsm_router
from app.security import require_api_key

app = FastAPI(
    title="CoolTech Enterprise Mock",
    version="0.1.0",
    description=(
        "Mock CRM, FSM and ERP systems for the FieldOps agent. "
        "Every endpoint except /health needs an `X-API-Key` header. "
        'Errors always look like `{"error": {"code", "message", "details"}}`.'
    ),
)
register_error_handlers(app)
CHAOS = ChaosSettings.from_env()
app.add_middleware(ChaosMiddleware, chaos=CHAOS)

for prefix, router, tag in (
    ("/crm", crm_router, "CRM"),
    ("/fsm", fsm_router, "FSM"),
    ("/erp", erp_router, "ERP"),
    ("/admin", admin_router, "Admin"),
):
    app.include_router(
        router,
        prefix=prefix,
        tags=[tag],
        dependencies=[Depends(require_api_key)],
        responses=ERROR_RESPONSES,
    )


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {"status": "ok", "chaos": CHAOS.public()}
