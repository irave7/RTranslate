"""Consume one private batch in a fresh Codex CLI session using its existing ChatGPT login."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import jobs
from .direct import _SYSTEM
from .privacy import native_env


def add_command(commands):
    parser = commands.add_parser("codex-batch", help="用全新临时 Codex 会话处理一个任务包")
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--packet-file", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--model", help="指定此独立 Codex 会话的模型；缺省使用 CLI 内置默认值")


def _launcher(executable):
    """Run npm's JavaScript entry with node directly, bypassing Windows cmd.exe quoting."""
    path = Path(executable)
    if path.suffix.casefold() not in {".cmd", ".bat"}:
        return [str(path)]
    entry = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = path.with_name("node.exe")
    if entry.is_file():
        interpreter = str(node) if node.is_file() else shutil.which("node.exe")
        if interpreter:
            return [interpreter, str(entry)]
    if binary := shutil.which("codex.exe"):
        return [binary]
    raise ValueError("这个 Codex Windows 启动脚本缺少官方 npm 入口或 codex.exe，请检查 Codex CLI 安装。")


def _environment():
    return {key:value for key,value in native_env().items()
            if not re.search(r"(?:thread|conversation|session|turn|parent|ancestor|prompt)", key, re.I)
            and key not in {"OPENAI_API_KEY", "CODEX_API_KEY", "RTRANSLATE_API_KEY"}}


def _translate(codex, payload, timeout, env, model=None):
    instruction = (_SYSTEM.partition("仅返回 JSON 对象")[0]
        +"仅处理本次数据，勿用工具。返回仅含 answers 的 JSON 对象，按 tasks 顺序列各任务答复。"
        "paragraphs/figure 为 items 同序译文数组，图内短标签保留编号与组别。single 为 text 的译文字符串。"
        "raw 按其翻译请求以字符串返回。terms 为 [[源词,译词],…]。所有 Ref 只引用本批 sharedContext。")
    fields = {k:v for k,v in payload.items() if k in {"tasks","glossary","sharedContext"}}
    prompt = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    with tempfile.TemporaryDirectory(prefix="rtranslate-codex-") as temporary:
        folder = Path(temporary).resolve()
        final = folder / "answer.json"
        # Replace the generic code-editing role and skill catalog for this
        # isolated translation invocation via supported per-command settings.
        role = folder / "translation-instructions.txt"
        role.write_text("遵守宿主安全及权限要求。"+instruction, encoding="utf-8")
        # "exec" without resume/fork starts a new thread. No document, job
        # directory, previous turn/thread identifier, or user prompt is passed.
        command = [*codex, "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                   "--sandbox", "read-only", "-C", str(folder), "-c", 'web_search="disabled"',
                   "-c", "model_instructions_file="+json.dumps(str(role),ensure_ascii=False),
                   "-c", "skills.include_instructions=false", "-c", "skills.bundled.enabled=false",
                   "-c", "skills.max_context_tokens=1",
                   "-c", "features.memories=false", "-c", "features.multi_agent=false",
                   "-c", "features.apps=false", "-c", "features.plugins=false",
                   "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
                   "--output-last-message", str(final), "-"]
        if model:
            command[-1:-1] = ["--model", model]
        proc = subprocess.run(command, input=prompt.encode(), cwd=folder, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        if proc.returncode or not final.is_file():
            raise RuntimeError("临时 Codex 会话未返回结果。请核对 Codex CLI 版本、登录和额度。")
        data = json.loads(final.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("answers"), list) or len(data["answers"]) != len(fields["tasks"]):
            raise ValueError("临时会话未返回同序 answers 数组。")
        return data["answers"]


def run(directory, packet_file, timeout, model=None):
    from .cli import submit
    if model is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}",model):
        raise ValueError("请指定有效的 Codex 模型名。")
    if not 5 <= timeout <= 3600:
        raise ValueError("--timeout 范围 5–3600 秒。")
    batch = jobs.read(packet_file)
    code = batch.get("batch")
    if not isinstance(code,str) or not re.fullmatch(r"[a-f0-9]{12}",code):
        raise ValueError("任务包缺少有效批号。")
    ticket = jobs.read(directory/"tickets"/(code+".json"))
    if not isinstance(batch.get("tasks"),list) or len(batch["tasks"]) != len(ticket):
        raise ValueError("任务包与此作业的领取记录不符。")
    result_file = directory/"tickets"/(code+".codex-answer.json")
    with jobs.worker_lock(directory, "codex-"+code+".lock") as owner:
        if not owner:
            raise RuntimeError("此批次已有 Codex 会话在工作。")
        from .queue import TaskQueue
        pending = TaskQueue(directory).renew(ticket, timeout+60)
        if not pending:
            return {"batch":code, "accepted":len(ticket), "state":"answered"}
        if result_file.is_file():
            answers = jobs.read(result_file)
        else:
            codex = shutil.which("codex")
            if not codex:
                raise ValueError("此命令需要已登录的 Codex CLI；其他宿主可直接读取任务包并 submit。")
            codex = _launcher(codex)
            env = _environment()
            login = subprocess.run([*codex,"login","status"], env=env, capture_output=True, timeout=20)
            if login.returncode or b"chatgpt" not in (login.stdout+login.stderr).lower():
                raise ValueError("请先使用 ChatGPT 账户登录 Codex CLI，以使用现有宿主额度。")
            try:
                subset = {**batch,"tasks":[batch["tasks"][i-1] for i in pending]}
                translated = _translate(codex, subset, timeout, env, model)
                answers = {str(i):a for i,a in zip(pending, translated)}
            except subprocess.TimeoutExpired:
                raise RuntimeError("临时 Codex 会话超时；如重试，可能再次使用宿主额度。") from None
            jobs.write(result_file, answers)
        result = submit(directory, {"batch":code,"answers":answers})
        if result["errors"]:
            raise ValueError("部分译文未通过本机校验，请按当前批号修正对应项并 submit。"+json.dumps(result["errors"],ensure_ascii=False))
        return {"state":"answered","batch":code,"accepted":len(result["accepted"])}
