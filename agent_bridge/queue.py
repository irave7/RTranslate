"""A private, durable request spool for a single translation job."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time


class QueueCancelled(RuntimeError):
    pass


class InvalidLease(ValueError):
    pass


class TaskQueue:
    def __init__(self, job_dir):
        self.directory = Path(job_dir) / "agent-queue"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.directory / "requests.sqlite3"
        descriptor = os.open(self.database, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    seq INTEGER PRIMARY KEY,
                    id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    lease TEXT,
                    expires REAL,
                    answer TEXT
                );
                CREATE TABLE IF NOT EXISTS job (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cancelled INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO job VALUES (1, 0);
            """)

    @contextmanager
    def _connect(self, *, write=False):
        db = sqlite3.connect(self.database, timeout=15)
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _check_active(db):
        if db.execute("SELECT cancelled FROM job WHERE id=1").fetchone()[0]:
            raise QueueCancelled("宿主翻译任务已取消。")

    def enqueue(self, model, messages, options):
        # OpenAI clients can reconnect after their HTTP request was accepted.
        # An equivalent prompt shares one durable answer within this job.
        payload = json.dumps({"model": model, "messages": messages, "options": options},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        ident = hashlib.sha256(payload.encode()).hexdigest()
        with self._connect(write=True) as db:
            self._check_active(db)
            db.execute("INSERT OR IGNORE INTO requests (id, payload) VALUES (?,?)", (ident, payload))
        return ident

    def get(self, ident):
        """Read the original request without claiming it or exposing its lease."""
        with self._connect() as db:
            row = db.execute("SELECT payload FROM requests WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            return dict(id=ident, **json.loads(row[0]))

    def answer(self, ident):
        with self._connect() as db:
            self._check_active(db)
            found = db.execute("SELECT answer FROM requests WHERE id=?", (ident,)).fetchone()
            if found is None:
                raise KeyError(ident)
            return found[0]

    def claim(self, limit=8, lease_seconds=300, compact_budget=None):
        """Claim at most limit prompts. A crashed host's leases expire automatically."""
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("每次可领取 1–128 个请求。")
        if not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 86400:
            raise ValueError("租约须大于零且不超过一天。")
        with self._connect(write=True) as db:
            self._check_active(db)
            now = time.time()
            rows = db.execute("""
                SELECT id, payload FROM requests
                WHERE answer IS NULL AND (lease IS NULL OR expires<=?)
                ORDER BY seq LIMIT ?
            """, (now, limit)).fetchall()
            results = []
            size = 0
            for ident, payload in rows:
                request = json.loads(payload)
                if compact_budget is not None:
                    from .codec import pack
                    from .compact import omit_literals
                    item = pack({**request, "id":"", "lease":""})
                    omit_literals(item)
                    size += len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                    # Never split an original native paragraph or starve a long task.
                    if results and size > compact_budget:
                        break
                lease = secrets.token_urlsafe(18)
                expires = now + lease_seconds
                db.execute("UPDATE requests SET lease=?, expires=? WHERE id=?", (lease, expires, ident))
                results.append(dict(id=ident, lease=lease, expires=expires, **request))
            return results

    def reply(self, ident, lease, content):
        """Commit a claimed reply exactly once, including safe retries after a lost response."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("宿主答复须为非空文本。")
        if not isinstance(lease, str) or not lease:
            raise InvalidLease("请使用当前领取凭据答复。")
        with self._connect(write=True) as db:
            self._check_active(db)
            row = db.execute("SELECT lease, expires, answer FROM requests WHERE id=?", (ident,)).fetchone()
            if row is None or row[0] != lease:
                raise InvalidLease("请求未领取或已由新宿主领取。")
            if row[2] is not None:
                if row[2] != content:
                    raise InvalidLease("此请求已有不同答复。")
                return
            if row[1] <= time.time():
                raise InvalidLease("领取凭据已过期，请重新领取。")
            db.execute("UPDATE requests SET answer=? WHERE id=?", (content, ident))

    def renew(self, ticket, seconds):
        """Only renew currently owned, unanswered slots before starting costly inference."""
        pending = []
        with self._connect(write=True) as db:
            self._check_active(db)
            now = time.time()
            for index, (ident, lease, *_) in enumerate(ticket, 1):
                row = db.execute("SELECT lease,expires,answer FROM requests WHERE id=?", (ident,)).fetchone()
                if row is None:
                    raise InvalidLease("找不到此任务包中的请求。")
                if row[2] is not None:
                    continue
                if row[0] != lease or row[1] <= now:
                    raise InvalidLease("任务包已过期或由其他宿主领取，请重新取批。")
                db.execute("UPDATE requests SET expires=? WHERE id=?", (max(row[1], now+seconds), ident))
                pending.append(index)
        return pending

    def counts(self):
        with self._connect() as db:
            row = db.execute("""
                SELECT count(*), count(answer),
                       count(CASE WHEN answer IS NULL AND expires>? THEN 1 END),
                       (SELECT cancelled FROM job WHERE id=1)
                FROM requests
            """, (time.time(),)).fetchone()
        total, answered, claimed, cancelled = row
        return dict(total=total, pending=total-answered-claimed, claimed=claimed,
                    answered=answered, cancelled=bool(cancelled))

    def cancel(self):
        with self._connect(write=True) as db:
            db.execute("UPDATE job SET cancelled=1 WHERE id=1")

    def restart(self):
        """Resume after the caller has acquired exclusive ownership of this job."""
        with self._connect(write=True) as db:
            db.execute("UPDATE job SET cancelled=0 WHERE id=1")
            db.execute("UPDATE requests SET lease=NULL, expires=NULL WHERE answer IS NULL")

    def reclaim(self):
        """A replacement host can reclaim work left behind in a closed chat."""
        with self._connect(write=True) as db:
            self._check_active(db)
            db.execute("UPDATE requests SET lease=NULL, expires=NULL WHERE answer IS NULL")

    def release(self, ident, lease):
        """Return an unfinished request to the host's available work list."""
        if not isinstance(lease, str) or not lease:
            raise InvalidLease("请使用当前领取凭据释放请求。")
        with self._connect(write=True) as db:
            self._check_active(db)
            changed = db.execute("""
                UPDATE requests SET lease=NULL, expires=NULL
                WHERE id=? AND lease=? AND answer IS NULL
            """, (ident, lease)).rowcount
            if not changed:
                raise InvalidLease("此请求已完成或已由其他宿主领取。")
