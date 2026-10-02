"""X-API-Key header check shared by every enterprise router."""

import secrets

from fastapi import Security
from fastapi.security import APIKeyHeader

from app.config import settings
from app.errors import APIError

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    if api_key is None or not secrets.compare_digest(api_key, settings.api_key):
        raise APIError(401, "unauthorized", "Missing or invalid X-API-Key header")
