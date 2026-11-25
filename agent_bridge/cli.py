"""JSON command-line interface used by the desktop skill."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time

from . import jobs, outputs
from .codec import pack, unpack
from .compact import omit_literals, restore
from .queue import TaskQueue, QueueCancelled

TERMINAL = {"done","cancelled","error","interrupted"}


def terms_db(directory):
    db = sqlite3.connect(directory / "terms.sqlite3", timeout=15)
    db.execute("CREATE TABLE IF NOT EXISTS terms (source TEXT PRIMARY KEY COLLATE NOCASE, target TEXT NOT NULL)")
    return db


def selected_terms(directory, tasks):
    from contextlib import closing
    text = json.dumps(tasks,ensure_ascii=False).casefold().replace("- ", "").replace("\u00ad","")
    supplied = {}
    for line in jobs.job_info(directory)["glossary"].splitlines():
        if line.strip():
            en, _, zh = line.partition("=")
            supplied[en.strip().casefold()] = zh.strip()
    with closing(terms_db(directory)) as db:
        combined = dict(db.execute("SELECT source,target FROM terms"))
    combined.update(supplied)
    return {s:t for s,t in combined.items() if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", text)}


def remember(directory, terms):
    from contextlib import closing
    if not isinstance(terms,dict) or len(terms)>128 or any(
            not isinstance(k,str) or not isinstance(v,str) or not 0<len(k.strip())<=200 or
            not 0<len(v.strip())<=200 for k,v in terms.items()):
        raise ValueError("terms 应是本批新增的英中术语对象，每次最多 128 项，每项最多 200 字符。")
    if terms:
        with closing(terms_db(directory)) as db, db:
            db.executemany("INSERT OR IGNORE INTO terms VALUES (?,?)", (
                (k.strip().casefold(),v.strip()) for k,v in terms.items()))


def next_batch(directory, limit, wait, compact):
    if not 1<=limit<=24 or not 0<=wait<=60:
        raise ValueError("limit 范围 1–24，wait 范围 0–60 秒。")
    st = jobs.status(directory)
    queue = TaskQueue(directory)
    from .short_labels import annotate
    until = time.monotonic()+wait
    while st["state"] not in TERMINAL:
        try:
            rows = queue.claim(limit=limit, lease_seconds=900, compact_budget=6000 if compact else None)
        except QueueCancelled:
            break
        if rows:
            tasks, mappings = [], []
            context_file = directory/"short-label-context.json"
            catalog = jobs.read(context_file) if context_file.is_file() else {}
            for row in rows:
                item = pack(row)
                if compact:
                    ident, lease = item.pop("id"), item.pop("lease")
                    sent = omit_literals(item)
                    if sent == []:
                        queue.reply(ident, lease, unpack(row, pack(row)["items"]))
                        continue
                    mappings.append([ident, lease, sent])
                    item["n"] = len(tasks)+1
                annotate(item,catalog)
                tasks.append(item)
            if not tasks:
                st = jobs.status(directory)
                continue
            output = {"state":"working","tasks":tasks}
            if compact:
                tickets = directory / "tickets"
                tickets.mkdir(mode=0o700, exist_ok=True)
                batch = secrets.token_hex(6)
                jobs.write(tickets / (batch + ".json"), mappings)
                output["batch"] = batch
            terms = selected_terms(directory, tasks)
            if terms:
                output["glossary"] = terms
            if compact:
                from .payload import streamline
                streamline(output)
            return output
        if time.monotonic()>=until:
            break
        time.sleep(.35)
        st = jobs.status(directory)
    return {**jobs.status(directory), "tasks":[]}


def submissions(directory, value):
    if not isinstance(value,dict) or "batch" not in value:
        return value.get("answers") if isinstance(value,dict) else value
    code = value["batch"]
    if not isinstance(code,str) or not re.fullmatch("[0-9a-f]{12}",code):
        raise ValueError("无效的批次编号。")
    keys = jobs.read(directory / "tickets" / (code + ".json"))
    answers = value.get("answers")
    if isinstance(answers,list) and len(answers)==len(keys):
        entries = enumerate(answers,1)
    elif isinstance(answers,dict) and all(re.fullmatch(r"[1-9][0-9]*",k) and int(k)<=len(keys) for k in answers):
        entries = ((int(k),answer) for k,answer in answers.items())
    else:
        raise ValueError("answers 请按本批 tasks 顺序填写；单项重提可用任务序号作键。")
    result = []
    for n,answer in entries:
        key = keys[n-1]
        result.append({"id":key[0],"lease":key[1],"answer":answer,"n":n,
                       "_sent":key[2] if len(key)>2 else None})
    return result


def submit(directory, value):
    jobs.job_info(directory)
    entries = submissions(directory, value)
    if not isinstance(entries,list) or not 1<=len(entries)<=24:
        raise ValueError("请提交 1–24 项答复。")
    queue = TaskQueue(directory)
    accepted,errors = [],[]
    for index, item in enumerate(entries,1):
        reference = item.get("n",item.get("id",index)) if isinstance(item,dict) else index
        try:
            if not isinstance(item,dict):
                raise ValueError("答复结构无效。")
            raw = queue.get(item["id"])
            answer = item["answer"]
            if isinstance(value,dict) and "batch" in value and item.get("_sent") is not None:
                answer = restore(pack({**raw,"lease":item["lease"]})["items"], item["_sent"], answer)
            content = unpack(raw,answer)
            queue.reply(item["id"],item["lease"],content)
            accepted.append(reference)
        except (TypeError,ValueError,KeyError) as exc:
            errors.append({"task":reference,"detail":str(exc)})
    if isinstance(value,dict) and not errors:
        remember(directory,value.get("terms",{}))
    return {"accepted":accepted,"errors":errors}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="原译 RTranslate 论文翻译接口")
    commands = parser.add_subparsers(dest="cmd",required=True)
    from .direct import add_command
    add_command(commands)
    from .codex_batch import add_command as add_codex
    add_codex(commands)
    prefs = commands.add_parser("preferences", help="查看或设置当前用户的默认 PDF 输出")
    pref_actions = prefs.add_mutually_exclusive_group()
    pref_actions.add_argument("--output", choices=outputs.MODES)
    pref_actions.add_argument("--reset", action="store_true")
    for name in ("start","next","submit","status","cancel","resume","segments","revise","_bootstrap","_engine"):
        command = commands.add_parser(name)
        command.add_argument("--job",type=Path,required=True)
        if name=="start":
            outputs.add_output_arguments(command)
            command.add_argument("input",type=Path)
            command.add_argument("--glossary",type=Path)
            command.add_argument("--no-figures",action="store_true")
            command.add_argument("--auto-terms",action="store_true")
        if name=="next":
            command.add_argument("--limit",type=int,default=8)
            command.add_argument("--wait",type=float,default=15)
            command.add_argument("--compact",action="store_true")
            command.add_argument("--packet-file",type=Path, help="将紧凑请求包写入指定文件，终端返回状态")
        if name=="segments":
            command.add_argument("--page",required=True,type=int)
        if name in ("submit","revise"):
            command.add_argument("--file",type=Path,required=True)
        if name=="resume":
            command.add_argument("--reclaim",action="store_true")
    args = parser.parse_args(argv)
    directory = args.job.expanduser().resolve() if hasattr(args,"job") else None
    try:
        if args.cmd=="preferences":
            result = outputs.preferences(args.output, args.reset)
        elif args.cmd=="api":
            from .direct import run
            result = run(args, directory)
        elif args.cmd=="codex-batch":
            from .codex_batch import run
            result = run(directory,args.packet_file,args.timeout)
        elif args.cmd=="start":
            result = jobs.create(args.input.expanduser().resolve(),directory,args.glossary,
                                 not args.no_figures,args.auto_terms, output=args.output, remember_output=args.remember_output)
        elif args.cmd=="next":
            if args.packet_file is not None:
                if not args.compact:
                    raise ValueError("--packet-file 须与 --compact 一起使用。")
                from .payload import packet
                result = packet(directory,args.packet_file,args.limit,args.wait)
            else:
                result = next_batch(directory,args.limit,args.wait,args.compact)
        elif args.cmd=="submit":
            with args.file.open(encoding="utf-8-sig") as stream:
                result = submit(directory,json.load(stream))
        elif args.cmd=="status":
            result = jobs.status(directory)
        elif args.cmd=="cancel":
            result = jobs.status(directory)
            if result["state"] not in TERMINAL:
                TaskQueue(directory).cancel()
                result = jobs.status(directory)
        elif args.cmd=="resume":
            result = jobs.status(directory)
            if result["state"] in {"cancelled","interrupted","error"}:
                with jobs.worker_lock(directory, "launch.lock") as owner:
                    if not owner or jobs.active(directory):
                        raise ValueError("上次工作进程正在退出或重启，请稍后 resume。")
                    queue = TaskQueue(directory)
                    queue.restart()
                    jobs.write(directory / "state.json", {"state":"starting","started":time.time()})
                    result = jobs.spawn(directory)
            elif args.reclaim and result["state"] == "working":
                TaskQueue(directory).reclaim()
                result = jobs.status(directory)
        elif args.cmd=="segments":
            from .revision import segments
            result = segments(directory,args.page)
        elif args.cmd=="revise":
            from .revision import revise
            result = revise(directory,args.file.expanduser().resolve())
        elif args.cmd=="_bootstrap":
            jobs.bootstrap(directory)
            return 0
        else:
            from .worker import run
            run(directory)
            return 0
        print(json.dumps(result, ensure_ascii=False,separators=(",",":")))
        return 1 if result.get("errors") else 0
    except (OSError,ValueError,RuntimeError,KeyError,sqlite3.Error) as exc:
        print(json.dumps({"state":"error","detail":str(exc)},ensure_ascii=False))
        return 1
