"""Errors safe to display without including request headers or upstream payloads."""


class HacdonaldsError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def public_error(exc: Exception) -> HacdonaldsError:
    """Inspect structured error attributes, never interpolate exception text."""
    if isinstance(exc, HacdonaldsError):
        return exc
    errors = []
    pending = [exc]
    seen = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        errors.append(error)
        pending.extend(getattr(error, "exceptions", []))
        cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        if cause is not None:
            pending.append(cause)
    # SDK task groups can wrap application errors when a connection exits.
    for error in errors:
        if isinstance(error, HacdonaldsError):
            return error
    for error in errors:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status in (401, 403):
            return HacdonaldsError("AUTH_FAILED", "认证失败，请检查 Token、有效期和权限。")
        if status == 429:
            return HacdonaldsError("RATE_LIMITED", "请求受到限流，请稍后再试；本次未自动重试。")
    if any(isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ for error in errors):
        return HacdonaldsError("TIMEOUT", "MCP 请求超时；本次未自动重试。")
    if any(isinstance(error, ImportError) for error in errors):
        return HacdonaldsError("DEPENDENCY_MISSING", "网络传输依赖不完整，请运行 uv sync；SOCKS 代理需要 socksio。")
    return HacdonaldsError("MCP_FAILED", "MCP 连接或调用失败，请检查网络、SDK 版本和官方服务状态。")
