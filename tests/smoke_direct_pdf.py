"""Round-trip the installed direct API command without an external service or credential."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading


def verify_direct(launcher, env, project, source):
    """Verify the public launcher, synthetic SSE provider, resume and both final PDFs."""
    translations = {"Surgical outcomes":"手术结局", "The median stay was 7 days.":"住院时间中位数为7天。"}
    credential = "rtranslate-fixture-only-9bfdb0"
    received = []
    failures = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions"
                assert self.headers["Authorization"] == "Bearer "+credential
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert request["model"] == "fixture" and request["stream"] is True
                assert [m["role"] for m in request["messages"]] == ["system","user"]
                task = json.loads(request["messages"][1]["content"])["task"]
                if task["kind"] == "raw" and task["messages"][-1]["content"].endswith("Hello"):
                    answer = "你好"
                elif task["kind"] == "single":
                    received.append(task["text"])
                    answer = translations[task["text"]]
                else:
                    assert task["kind"] == "paragraphs"
                    received.extend(task["items"])
                    answer = [translations[t] for t in task["items"]]
                content = json.dumps({"answer":answer}, ensure_ascii=False)
                parts = [(content[:5],None),(content[5:],None),("","stop")]
                events = [json.dumps({"choices":[{"index":0,"delta":{"content":chunk},"finish_reason":reason}]},
                                     ensure_ascii=False) for chunk,reason in parts]
                packet = ("".join("data: "+event+"\n\n" for event in events)+"data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream; charset=utf-8")
                self.send_header("Content-Length",str(len(packet)))
                self.end_headers()
                self.wfile.write(packet)
            except Exception as exc:
                failures.append(type(exc).__name__)
                self.send_error(400,"fixture rejected request")

    job = project/"接口任务 — 双语"
    with ThreadingHTTPServer(("127.0.0.1",0),Provider) as server:
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = [*launcher,"api","--base-url",f"http://127.0.0.1:{server.server_port}/v1","--model","fixture",
                "--job",str(job),"--stream"]
        config = dict(env, RTRANSLATE_API_KEY=credential, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        def invoke(arguments):
            proc = subprocess.run([*base,*arguments], env=config,cwd=project, capture_output=True,
                                  encoding="utf-8",timeout=240)
            if proc.returncode:
                raise RuntimeError(proc.stdout+"\n"+proc.stderr)
            return json.loads(proc.stdout)

        try:
            result = invoke([str(source),"--output","both","--no-figures"])
            assert result["state"] == "done" and result["pages"] == 1
            assert set(received) == set(translations) and not failures
            before = len(received)
            resumed = invoke(["--resume"])
            assert len(received) == before and resumed == result
        finally:
            server.shutdown()
            thread.join()
    for filename in (job/"job.json",job/"worker.log",job/"api-profile.json",*sorted((job/"api-responses").glob("*.json"))):
        assert credential.encode() not in filename.read_bytes(), filename
    runtime = Path(env["RTRANSLATE_RUNTIME_CACHE"])
    workers = list(runtime.glob("engine-*/Scripts/python.exe" if os.name=="nt" else "engine-*/bin/python"))
    assert len(workers)==1, workers
    verify = ("import sys,pymupdf,unicodedata; d=pymupdf.open(sys.argv[1]); "
              "s=unicodedata.normalize('NFKC',''.join(d[0].get_text().split())); "
              "assert len(d)==1 and '手术结局' in s and '住院时间中位数为7天' in s "
              "and ('Surgicaloutcomes' in s)==(sys.argv[2]=='dual'),s")
    for output in ("mono","dual"):
        subprocess.run([str(workers[0]),"-I","-B","-X","utf8","-c",verify,result[output],output],check=True)
