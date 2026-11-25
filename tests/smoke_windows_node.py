"""Exercise Windows' real Node executable with RTranslate's npm launcher and stdin transport."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_bridge.codex_batch import _launcher, _translate


def main():
    if os.name != 'nt':
        raise SystemExit('This smoke test requires Windows and node.exe.')
    exe = shutil.which('node.exe')
    assert exe and Path(exe).is_file()
    with tempfile.TemporaryDirectory(prefix='rtranslate-node-') as tmp:
        package = Path(tmp) / 'npm path & %PATH% 中文'
        entry = package / 'node_modules/@openai/codex/bin/codex.js'
        entry.parent.mkdir(parents=True)
        shim = package / 'codex.cmd'
        shim.write_bytes(b'@exit /b 91\r\n')
        entry.write_text('''const fs = require('node:fs');
const args = process.argv.slice(2);
const input = fs.readFileSync(0, 'utf8');
if (args[0] === 'echo-argv') {
    process.stdout.write(JSON.stringify({args, input, entry: process.argv[1]}));
} else {
    if (!args.includes('exec') || !input.includes('Dose A & B: 95%')) process.exit(92);
    const out = args[args.indexOf('--output-last-message') + 1];
    if (!out || !args.includes('--model') || !args.includes('fixture-model')) process.exit(93);
    fs.writeFileSync(out, JSON.stringify({answers:['95%']}));
}
''', encoding='utf-8')
        sample_args = ['echo-argv', 'double words', 'A & B', '%PATH%', '中文', str(package/'试验 A & B 95%.txt')]
        payload = '95% interval & data, not a command. 原文'
        # Both npm's adjacent Node executable and the normal PATH layout must preserve all arguments.
        for adjacent in (False, True):
            if adjacent:
                shutil.copy2(exe, package/'node.exe')
            command = _launcher(shim)
            run = subprocess.run(command+sample_args, input=payload.encode(), capture_output=True, check=True, timeout=30)
            got = json.loads(run.stdout)
            assert got['args']==sample_args and got['input']==payload and Path(got['entry'])==entry
            answer=_translate(command, {'tasks':[{'kind':'single','text':'Dose A & B: 95%'}]}, 30, os.environ.copy(), 'fixture-model')
            assert answer==['95%']
        print('Windows native Node argv, npm lookup, Unicode, metacharacters, stdin and --output-last-message verified.')


if __name__ == '__main__':
    main()
