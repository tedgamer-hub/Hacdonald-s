"""Errors safe to display without including request headers or upstream payloads."""


class HacdonaldsError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def exception_chain(exc: BaseException, *, include_suppressed=False):
    """Walk bounded exception structure without reading messages or payloads."""
    errors = []
    pending = [exc]
    seen = set()
    while pending and len(errors) < 64:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        errors.append(error)
        pending.extend(getattr(error, "exceptions", []))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None and (include_suppressed or not error.__suppress_context__):
            pending.append(error.__context__)
    return errors


def error_metadata(exc: BaseException, *, token="") -> list[dict]:
    """Structured codes plus redacted RPC messages for private diagnostics.

    Never copy generic exception text, request headers or error data payloads.
    """
    from .privacy import redact
    result = []
    for error in exception_chain(exc, include_suppressed=True):
        item = {"type": f"{type(error).__module__}.{type(error).__name__}"}
        status = getattr(getattr(error, "response", None), "status_code", None)
        if type(status) is int:
            item["http_status"] = status
        code = getattr(error, "code", None)
        if type(code) is int and type(error).__module__.startswith("mcp."):
            item["rpc_code"] = code
            message = getattr(error, "message", None)
            if isinstance(message, str):
                item["rpc_message"] = redact(message, token)[:500]
        result.append(item)
    return result


def public_error(exc: BaseException) -> HacdonaldsError:
    """Inspect structured error attributes, never interpolate exception text."""
    if isinstance(exc, HacdonaldsError):
        return exc
    errors = exception_chain(exc)
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
        if type(status) is int and 500 <= status <= 599:
            return HacdonaldsError("UPSTREAM_HTTP_ERROR", f"官方 MCP 或中间网关返回 HTTP {status}；本次未自动重试。")
    rpc_codes = [error.code for error in errors if type(error).__module__.startswith("mcp.") and type(getattr(error, "code", None)) is int]
    # MCP SDK 2.x jsonrpc.REQUEST_TIMEOUT. The dispatcher raises this from
    # None, so a Python TimeoutError may no longer be present in the chain.
    if -32001 in rpc_codes or any(isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ for error in errors):
        return HacdonaldsError("TIMEOUT", "MCP 请求超时；本次未自动重试。")
    if -32000 in rpc_codes:
        return HacdonaldsError("CONNECTION_CLOSED", "MCP 连接在响应完成前关闭；本次未自动重试。")
    names = {type(error).__name__ for error in errors}
    if "ProxyError" in names:
        return HacdonaldsError("PROXY_FAILED", "代理连接失败，请检查本机代理是否可用。")
    if names & {"SSLError", "SSLCertVerificationError"}:
        return HacdonaldsError("TLS_FAILED", "TLS 连接或证书校验失败，请检查网络代理和证书配置。")
    if names & {"ConnectError", "ReadError", "WriteError", "RemoteProtocolError"}:
        return HacdonaldsError("CONNECTION_FAILED", "网络连接或响应传输失败，请检查网络与代理；本次未自动重试。")
    if rpc_codes:
        return HacdonaldsError("MCP_RPC_ERROR", f"官方 MCP 返回协议错误（代码 {rpc_codes[0]}）；诊断记录保留了错误码。")
    if any(isinstance(error, ImportError) for error in errors):
        return HacdonaldsError("DEPENDENCY_MISSING", "网络传输依赖不完整，请运行 uv sync；SOCKS 代理需要 socksio。")
    return HacdonaldsError("MCP_FAILED", "MCP 连接或调用失败，请检查网络、SDK 版本和官方服务状态。")
