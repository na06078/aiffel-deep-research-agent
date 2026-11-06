"""DNS rebinding·교차 출처 요청을 업무 처리 전에 차단하는 ASGI 경계."""

from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class LoopbackOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        hosts = [v.decode("latin-1") for k, v in headers if k.lower() == b"host"]
        origins = [v.decode("latin-1") for k, v in headers if k.lower() == b"origin"]
        error = None
        try:
            host = urlsplit(f"//{hosts[0]}") if len(hosts) == 1 else None
            if (
                host is None
                or host.hostname not in {"127.0.0.1", "localhost", "::1"}
                or host.username
                or host.password
                or host.path
                or host.query
                or host.fragment
            ):
                raise ValueError("invalid host")
            port = host.port or (443 if scope["scheme"] == "https" else 80)
        except (ValueError, IndexError):
            error = ("host_forbidden", "허용된 loopback Host가 아닙니다.")
        if not error and origins:
            try:
                origin = urlsplit(origins[0])
                if (
                    len(origins) != 1
                    or origin.scheme != scope["scheme"]
                    or origin.hostname != host.hostname
                    or (origin.port or (443 if origin.scheme == "https" else 80)) != port
                    or origin.username
                    or origin.password
                    or origin.path
                    or origin.query
                    or origin.fragment
                ):
                    raise ValueError("invalid origin")
            except ValueError:
                error = ("origin_forbidden", "같은 loopback 출처만 허용합니다.")
        if error:
            await JSONResponse(
                status_code=403, content={"error": {"code": error[0], "message": error[1]}}
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
