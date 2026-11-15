"""Small dependency-free HTTP hardening middleware."""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach browser hardening headers to every API response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class ExpensiveRequestRateLimitMiddleware(BaseHTTPMiddleware):
    """Bound costly public operations by the socket peer, not spoofable headers."""

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int,
    ) -> None:
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @staticmethod
    def _is_expensive(request: Request) -> bool:
        path = request.url.path
        return request.method == "POST" and (
            path.endswith("/guest/messages")
            or path.endswith("/conversations/messages")
            or (
                "/conversations/" in path
                and path.endswith("/messages")
            )
            or path.endswith("/flights/lookup")
        )

    async def dispatch(self, request: Request, call_next):
        if not self._is_expensive(request):
            return await call_next(request)

        peer = request.client.host if request.client else "unknown"
        key = f"{peer}:{request.url.path}"
        now = monotonic()
        with self._lock:
            recent = self._requests[key]
            while recent and now - recent[0] >= 60:
                recent.popleft()
            if len(recent) >= self.requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            "Too many requests. Wait briefly before trying again."
                        ),
                    },
                    headers={"Retry-After": "60"},
                )
            recent.append(now)
        return await call_next(request)
