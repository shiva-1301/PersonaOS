"""HTTP middleware: request IDs, and an early size limit for uploads."""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.errors import error_body
from app.logging_config import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


# Multipart framing (boundaries, headers) on top of the file itself.
UPLOAD_OVERHEAD_BYTES = 64 * 1024


class UploadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject uploads by Content-Length before the body is read (413).

    The router also checks the real file size, which covers requests that lie about or
    omit Content-Length. A reverse proxy limit is added in Phase 10.
    """

    def __init__(self, app, max_bytes: int, path_prefix: str = "/documents"):
        super().__init__(app)
        self.max_bytes = max_bytes + UPLOAD_OVERHEAD_BYTES
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST" and request.url.path.startswith(self.path_prefix):
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > self.max_bytes:
                limit_mb = (self.max_bytes - UPLOAD_OVERHEAD_BYTES) // (1024 * 1024)
                return JSONResponse(
                    error_body("payload_too_large", f"File too large (max {limit_mb} MB)"),
                    status_code=413,
                )
        return await call_next(request)
