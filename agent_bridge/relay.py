"""Authenticated loopback-only OpenAI transport for a host-operated job queue."""

from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from .queue import QueueCancelled, TaskQueue


MODEL = "host-agent"
MAX_REQUEST = 8 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@contextmanager
def _owner(directory):
    # OS releases the lock on process exit. A saved spool remains usable by a
    # restarted job without waiting for a stale application PID.
    descriptor = os.open(directory / "relay.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as err:
                raise RuntimeError("此任务的宿主中继已在运行。") from err
        else:
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as err:
                raise RuntimeError("此任务的宿主中继已在运行。") from err
        yield
    finally:
        os.close(descriptor)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _headers(self, status, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True

    def _send(self, status, data):
        body = _json(data)
        self._headers(status, "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, {"error": {"message": message, "type": "agent_relay_error"}})

    def _allowed(self):
        expected = ("Bearer " + self.server.relay.token).encode()
        supplied = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(supplied, expected):
            self._error(401, "无效的本机任务凭据。")
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        if urlsplit(self.path).path != "/v1/models":
            self._error(404, "未知接口。")
            return
        self._send(200, {"object": "list", "data": [
            {"id": MODEL, "object": "model", "created": 0, "owned_by": "local-host"}
        ]})

    def do_POST(self):
        if not self._allowed():
            return
        if urlsplit(self.path).path != "/v1/chat/completions":
            self._error(404, "未知接口。")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_REQUEST:
            self._error(413 if length > MAX_REQUEST else 400, "无效的请求大小。")
            return
        try:
            request = json.loads(self.rfile.read(length))
            model, messages, options, stream = self._parse(request)
            ident = self.server.relay.queue.enqueue(model, messages, options)
        except (TypeError, ValueError, RecursionError):
            self._error(400, "仅支持本机宿主模型的文本对话。")
            return
        except QueueCancelled:
            self._error(409, "宿主翻译任务已取消。")
            return
        try:
            self._deliver(ident, stream)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # The same caller may retry against the original durable request.
            return

    @staticmethod
    def _parse(request):
        if not isinstance(request, dict) or request.get("model") != MODEL or request.get("n", 1) != 1:
            raise ValueError("Unsupported model or choice count")
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("Missing messages")
        for message in messages:
            if (not isinstance(message, dict) or message.get("role") not in ("system","developer","user","assistant")
                    or not isinstance(message.get("content"), str)):
                raise ValueError("Invalid text message")
        stream = request.get("stream", False)
        if type(stream) is not bool:
            raise ValueError("Invalid stream option")
        options = {k: v for k,v in request.items() if k not in ("model","messages","stream","stream_options")}
        return MODEL, messages, options, stream

    def _chunk(self, payload):
        self.wfile.write(("%X\r\n" % len(payload)).encode() + payload + b"\r\n")
        self.wfile.flush()

    def _deliver(self, ident, stream):
        relay = self.server.relay
        self._headers(200, "text/event-stream" if stream else "application/json; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        deadline = time.monotonic()
        try:
            while not relay.stopping.is_set():
                answer = relay.queue.answer(ident)
                if answer is not None:
                    break
                if time.monotonic() >= deadline:
                    # Whitespace is legal before a JSON object. OpenAI SDKs
                    # also accept SSE comments while waiting for a human.
                    self._chunk(b": waiting\n\n" if stream else b"\n")
                    deadline = time.monotonic() + relay.heartbeat
                relay.stopping.wait(relay.poll_interval)
            else:
                raise QueueCancelled("本机中继已结束。")
        except QueueCancelled as exc:
            error = _json({"error": {"message": str(exc), "type": "job_cancelled"}})
            self._chunk(b"data: " + error + b"\n\n" if stream else error)
        else:
            header = {"id": "chatcmpl-" + ident, "model": MODEL, "created": int(time.time())}
            if stream:
                for delta, finish in (({"role":"assistant","content":answer}, None), ({}, "stop")):
                    chunk = dict(header, object="chat.completion.chunk",
                                 choices=[{"index": 0, "delta": delta, "finish_reason": finish}])
                    self._chunk(b"data: " + _json(chunk) + b"\n\n")
                self._chunk(b"data: [DONE]\n\n")
            else:
                message = {"role": "assistant", "content": answer}
                self._chunk(_json(dict(header, object="chat.completion", choices=[
                    {"index": 0, "message": message, "finish_reason": "stop"}])))
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class _Server(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 64


class AgentRelay:
    model = MODEL

    def __init__(self, job_dir, *, heartbeat=5, poll_interval=.2):
        if heartbeat <= 0 or poll_interval <= 0:
            raise ValueError("中继心跳和轮询间隔须为正数。")
        self.queue = TaskQueue(job_dir)
        self.token = None
        self.heartbeat = heartbeat
        self.poll_interval = poll_interval
        self.stopping = threading.Event()
        self.endpoint = None

    def __enter__(self):
        owner = _owner(self.queue.directory)
        owner.__enter__()
        self._lock = owner
        self.stopping.clear()
        self.token = secrets.token_urlsafe(32)
        try:
            self.server = _Server(("127.0.0.1", 0), _Handler)
            self.server.relay = self
            self.endpoint = "http://127.0.0.1:%d/v1" % self.server.server_port
            self.thread = threading.Thread(target=self.server.serve_forever, args=(.1,), daemon=True)
            self.thread.start()
            return self
        except BaseException:
            if self.endpoint is not None:
                self.server.server_close()
            self.endpoint = None
            self._lock.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.stopping.set()
        try:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
        finally:
            self.endpoint = None
            self._lock.__exit__(*args)
