"""Validate explicit OpenAI-compatible endpoints and their request options."""
from urllib import parse


def chat_generation_options(endpoint, model):
    """Use the provider's translation-friendly non-thinking mode on its own API."""
    host = parse.urlsplit(endpoint).hostname
    if host == "api.deepseek.com" and model in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"}:
        return {"thinking": {"type": "disabled"}}
    return {}


def service_base(endpoint):
    url = parse.urlsplit(endpoint.strip())
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or url.query or any(c.isspace() for c in endpoint)):
        raise ValueError("请填写有效的服务基础地址。")
    url.port  # Validate the port before authenticating.
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程服务请填写 HTTPS 地址，本机可使用 HTTP。")
    path = url.path.rstrip("/") or "/v1"
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return parse.urlunsplit((url.scheme, url.netloc, path, "", ""))
