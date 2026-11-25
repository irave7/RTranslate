"""A stateless, single-attempt OpenAI-compatible Chat Completions transport."""
import http.client
import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


class Rejected(RuntimeError):
    """HTTP rejection where the provider did not return a translated result."""


class Uncertain(RuntimeError):
    """Request may already be billable. Never retry implicitly."""


def endpoint(base):
    parts = urlsplit(base)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("API 地址须为完整 HTTPS 地址，不能含账号、密码、查询或片段。")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if parts.scheme == "http" and not loopback:
        raise ValueError("远程 API 须使用 HTTPS，仅本机地址支持 HTTP。")
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LIMIT = 8 * 1024 * 1024


def _stream(response):
    chunks, used, reason = [], 0, None
    while True:
        line = response.readline(min(1048577, _LIMIT - used + 1))
        used += len(line)
        if used > _LIMIT or len(line) > 1048576:
            raise Uncertain("接口回复超出最大长度。")
        if not line:
            break
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == b"[DONE]":
            break
        msg = json.loads(data)
        if "error" in msg:
            raise Uncertain("流式接口在生成途中报错。")
        for choice in msg.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            value = choice.get("delta", {}).get("content")
            if value:
                if not isinstance(value, str):
                    raise Uncertain("接口未返回文本片段。")
                chunks.append(value)
            if choice.get("finish_reason"):
                reason = choice["finish_reason"]
    if reason != "stop":
        raise Uncertain("流式回复未完整结束。")
    return "".join(chunks)


def send(url, model, key, messages, timeout, stream):
    body = json.dumps({"model":model, "messages":messages, "stream":stream}, ensure_ascii=False, separators=(",", ":")).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization":"Bearer "+key, "Content-Type":"application/json",
                                 "Accept":"text/event-stream" if stream else "application/json"}, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.headers.get_content_type() == "text/event-stream":
                return _stream(response)
            data = response.read(_LIMIT+1)
            if len(data) > _LIMIT:
                raise Uncertain("接口回复超出最大长度。")
            choice = json.loads(data)["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise Uncertain("接口未确认回复完整结束。")
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise Uncertain("接口回复中缺少正文。")
            return answer
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if 300 <= code < 500 and code != 408:
            raise Rejected(f"API 返回 HTTP {code}。请检查地址、模型、密钥及服务额度，再继续。") from None
        raise Uncertain(f"API 返回 HTTP {code}，供应商是否已计费未知。") from None
    except (OSError, http.client.HTTPException, UnicodeError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise Uncertain("API 连接中断、超时或响应格式异常，供应商可能已收到请求。") from None
