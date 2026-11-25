import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_bridge import api_transport as api, cli, direct, jobs
from agent_bridge.queue import TaskQueue


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.server.credentials.append(self.headers["Authorization"])
        mode = self.server.mode
        if mode == "redirect":
            self.send_response(307)
            self.send_header("Location",f"http://127.0.0.1:{self.server.server_port}/another-origin")
            self.end_headers()
            return
        if mode == "limit":
            self.send_error(429)
            return
        if mode == "timeout":
            time.sleep(.15)
            return
        body = '{"answer":"研究结果为12%。"}'
        if mode in ("stream","broken"):
            content = b'data: '+json.dumps({"choices":[{"delta":{"content":body[:4]},"finish_reason":None}]}).encode()+b'\n\n'
            if mode != "broken":
                content += b'data: '+json.dumps({"choices":[{"delta":{"content":body[4:]},"finish_reason":"stop"}]}).encode()+b'\n\ndata: [DONE]\n\n'
        else:
            content = json.dumps({"choices":[{"message":{"content":body},"finish_reason":"stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream" if mode in ("stream","broken") else "application/json")
        self.end_headers()
        self.wfile.write(content)


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1",0), Provider)
        self.server.mode = "json"
        self.server.calls, self.server.credentials = [], []
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = api.endpoint(f"http://127.0.0.1:{self.server.server_port}/v1")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        jobs.write(self.root/"job.json",{"glossary":""})
        jobs.write(self.root/"state.json",{"state":"working"})
        self.ledger = self.root/"api-responses"
        self.ledger.mkdir()
        self.queue = TaskQueue(self.root)

    def next(self, text):
        prompt = "Translate\n## Rules\nOK\n## Output\nJSON\nNow translate the following text:"+text
        ident = self.queue.enqueue("agent",[{"role":"user","content":prompt}],{})
        with patch.object(jobs,"active",return_value=True):
            return ident, cli.next_batch(self.root,1,0,True)

    def handle(self, batch, *, retry=False, timeout=1):
        direct._handle(self.root,batch,self.url,"model","temporary-test-key",timeout,True,retry,self.ledger)

    def test_two_stateless_requests_stream_and_json_and_no_cross_batch_content(self):
        for mode,word in (("stream","Forest"),("json","Ocean")):
            self.server.mode = mode
            ident,batch = self.next(word+" 12%")
            self.handle(batch)
            self.assertIn("12%",self.queue.answer(ident))
        self.assertEqual(len(self.server.calls),2)
        for req in self.server.calls:
            self.assertEqual(set(req),{"messages","model","stream"})
            self.assertEqual([m["role"] for m in req["messages"]],["system","user"])
        self.assertNotIn("Forest",str(self.server.calls[1]))
        self.assertNotIn("Ocean",str(self.server.calls[0]))
        self.assertEqual(self.server.credentials,["Bearer temporary-test-key"]*2)

    def test_uncertain_timeout_or_partial_stream_no_implicit_paid_retry(self):
        for mode,name in (("timeout","First"),("broken","Second")):
            self.server.mode = mode
            ident,batch = self.next(name+" 12%")
            count = len(self.server.calls)
            with self.assertRaisesRegex(RuntimeError,"不会自动"):
                self.handle(batch,timeout=.05)
            with self.assertRaisesRegex(RuntimeError,"retry-uncertain"):
                self.handle(batch)
            self.assertEqual(len(self.server.calls),count+1)
            self.server.mode = "json"; self.handle(batch,retry=True)
            self.assertIn("12%",self.queue.answer(ident))
            self.assertEqual(len(self.server.calls),count+2)

    def test_cached_before_local_submit_and_never_paid_twice(self):
        ident,batch = self.next("Result 12%")
        with patch("agent_bridge.cli.submit",side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.handle(batch)
        self.assertEqual(jobs.read(self.ledger/(ident+".json"))["state"],"received")
        self.handle(batch); self.handle(batch)
        self.assertEqual(len(self.server.calls),1)

    def test_key_environment_not_propagated_to_real_child_and_redirect_not_followed(self):
        import subprocess,sys
        key = "not-a-real-secret-81731"
        with patch.dict(os.environ, {"CUSTOM_CRED":key,"ALIAS_VALUE":"x"+key,
                          "OPENAI_API_KEY":"other-secret","INNOCENT_FLAG":"yes"},clear=True):
            env = direct._safe_env("CUSTOM_CRED",key)
            output = subprocess.check_output([sys.executable,"-c",
                    "import os; print([os.getenv(k) for k in ('CUSTOM_CRED','ALIAS_VALUE','OPENAI_API_KEY','INNOCENT_FLAG')])"],env=env)
            self.assertEqual(output.strip(),b"[None, None, None, 'yes']")
        for mode in ("redirect","limit"):
            self.server.mode = mode
            with self.assertRaises(api.Rejected):
                api.send(self.url,"model",key,[],1,False)
        self.assertEqual(len(self.server.calls),2)
        for item in ("http://example.org","https://key@host.test","https://host.test?key=x"):
            with self.assertRaises(ValueError):api.endpoint(item)
