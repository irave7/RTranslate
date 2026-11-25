import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

from agent_bridge.queue import InvalidLease, QueueCancelled, TaskQueue
from agent_bridge.relay import AgentRelay


class QueueTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.queue = TaskQueue(self.path)

    def add(self, prompt, **options):
        return self.queue.enqueue("host-agent", [{"role": "user", "content": prompt}], options)

    def test_equivalent_retries_share_one_answer_but_context_and_options_stay_distinct(self):
        first = self.add("one", temperature=0, seed=4)
        self.assertEqual(first, self.add("one", seed=4, temperature=0))
        self.assertNotEqual(first, self.add("one", seed=2, temperature=0))
        other = self.queue.enqueue("host-agent", [{"role": "system", "content": "context"},
                                                  {"role": "user", "content": "one"}], {})
        self.assertNotEqual(first, other)
        self.assertNotIn("lease", self.queue.get(first))
        with self.assertRaises(KeyError):
            self.queue.get("absent")
        claimed = self.queue.claim(limit=2)
        self.assertEqual(len(claimed), 2)
        self.assertEqual(claimed[0]["id"], first)
        item = claimed[0]
        another = TaskQueue(self.path)
        another.reply(first, item["lease"], '[{"id":1,"output":"试验组"}]')
        another.reply(first, item["lease"], '[{"id":1,"output":"试验组"}]')
        self.assertEqual(another.answer(first), '[{"id":1,"output":"试验组"}]')
        with self.assertRaises(InvalidLease):
            another.reply(first, item["lease"], "changed")

    def test_host_in_another_process_can_claim_and_answer(self):
        ident = self.add("Compare groups A and B.")
        program = (
            "from agent_bridge.queue import TaskQueue\n"
            "import sys\n"
            "queue = TaskQueue(sys.argv[1])\n"
            "item, = queue.claim()\n"
            "assert item['id'] == sys.argv[2]\n"
            "queue.reply(item['id'], item['lease'], '比较 A 组与 B 组。')\n"
        )
        subprocess.run([sys.executable, "-c", program, str(self.path), ident],
                       cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)
        self.assertEqual(self.queue.answer(ident), "比较 A 组与 B 组。")

    def test_simultaneous_claims_are_exclusive(self):
        ids = [self.add(str(i)) for i in range(16)]
        barrier = threading.Barrier(4)
        def claim():
            queue = TaskQueue(self.path)
            barrier.wait()
            return [item["id"] for item in queue.claim(4)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _:claim(), range(4)))
        self.assertCountEqual([i for group in results for i in group], ids)
        self.assertEqual(self.queue.claim(), [])

    def test_expired_and_released_claims_can_be_recovered_without_stale_writes(self):
        ident = self.add("未完成")
        first = self.queue.claim(lease_seconds=.01)[0]
        time.sleep(.025)
        with self.assertRaises(InvalidLease):
            self.queue.reply(ident, first["lease"], "late")
        second = self.queue.claim()[0]
        with self.assertRaises(InvalidLease):
            self.queue.release(ident, first["lease"])
        self.queue.release(ident, second["lease"])
        current = self.queue.claim()[0]
        self.assertNotEqual(current["lease"], second["lease"])
        self.queue.reply(ident, current["lease"], "finished")
        self.assertEqual(self.queue.counts(), dict(total=1, claimed=0, answered=1, pending=0, cancelled=False))

    def test_restart_preserves_answers_and_unblocks_cancelled_work(self):
        one, two = self.add("one"), self.add("two")
        a,b = self.queue.claim()
        self.queue.reply(one, a["lease"], "一")
        self.queue.cancel()
        with self.assertRaises(QueueCancelled):
            self.queue.claim()
        self.queue.restart()
        self.assertEqual(self.queue.answer(one), "一")
        with self.assertRaises(InvalidLease):
            self.queue.reply(two, b["lease"], "stale")
        fresh = self.queue.claim()
        self.assertEqual([r["id"] for r in fresh], [two])

    @unittest.skipIf(os.name == "nt", "POSIX mode")
    def test_spool_is_private(self):
        self.assertEqual(self.queue.database.stat().st_mode & 0o077, 0)
        self.assertEqual(self.queue.directory.stat().st_mode & 0o077, 0)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.relay = AgentRelay(self.folder.name, heartbeat=.02, poll_interval=.005)
        self.relay.__enter__()
        self.addCleanup(self.finish)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def finish(self):
        if not self.relay.stopping.is_set():
            self.relay.__exit__(None,None,None)
        self.pool.shutdown()

    def request(self, method, route, payload=None, *, allowed=True):
        url = urlsplit(self.relay.endpoint)
        # A tiny read timeout detects missing keepalive during host thinking.
        sock = http.client.HTTPConnection(url.hostname, url.port, timeout=1.0)
        try:
            auth = {"Authorization": "Bearer " + self.relay.token} if allowed else {}
            sock.request(method, route, body=json.dumps(payload) if payload is not None else None, headers=auth)
            response = sock.getresponse()
            return response.status, response.read().decode()
        finally:
            sock.close()

    def claim(self):
        end = time.monotonic() + 5
        while time.monotonic()<end:
            items = self.relay.queue.claim()
            if items:
                return items[0]
            time.sleep(.005)
        self.fail("No host request arrived")

    def chat(self, stream=False, prompt="Compare {v1} with {v2}."):
        return self.pool.submit(self.request, "POST", "/v1/chat/completions", {
            "model": self.relay.model, "messages":[{"role":"user","content":prompt}],
            "stream":stream
        })

    def test_authenticated_models_and_input_validation(self):
        status, text = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["data"][0]["id"], self.relay.model)
        self.assertEqual(self.request("GET", "/v1/models", allowed=False)[0], 401)
        self.assertEqual(self.request("POST", "/v1/chat/completions", {})[0], 400)
        self.assertEqual(self.request("GET", "/v1/host/claim")[0], 404)
        self.assertEqual(self.relay.queue.counts()["total"], 0)
        with self.assertRaisesRegex(RuntimeError, "已在运行"):
            with AgentRelay(self.folder.name):
                pass

    def test_json_and_stream_clients_share_host_reply_and_receive_keepalives(self):
        plain, sse = self.chat(), self.chat(True)
        item = self.claim()
        time.sleep(1.15)
        self.assertEqual(self.relay.queue.counts()["total"], 1)
        self.relay.queue.reply(item["id"], item["lease"], "比较 {v1} 和 {v2}。")
        status, text = plain.result(3)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["choices"][0]["message"]["content"], "比较 {v1} 和 {v2}。")
        _, events = sse.result(3)
        self.assertGreater(events.count(": waiting"), 1)
        lines = [v for v in events.splitlines() if v.startswith("data: ")]
        self.assertEqual(json.loads(lines[0][6:])["choices"][0]["delta"]["content"], "比较 {v1} 和 {v2}。")
        self.assertEqual(json.loads(lines[1][6:])["choices"][0]["finish_reason"], "stop")
        self.assertEqual(lines[-1], "data: [DONE]")

    def test_cancel_and_shutdown_release_waiting_clients(self):
        first = self.chat()
        self.claim()
        self.relay.queue.cancel()
        self.assertEqual(json.loads(first.result(2)[1])["error"]["type"], "job_cancelled")
        self.assertEqual(self.chat().result(2)[0], 409)
        self.relay.queue.restart()
        second = self.chat(prompt="another")
        end = time.monotonic() + 2
        while time.monotonic() < end and self.relay.queue.counts()["total"] < 2:
            time.sleep(.005)
        self.relay.__exit__(None,None,None)
        self.assertEqual(json.loads(second.result(2)[1])["error"]["type"], "job_cancelled")
