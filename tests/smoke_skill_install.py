"""Run a native install plus a real PDF round trip using a deterministic offline host response."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def fixture(path):
    text = b'BT /F0 14 Tf 58 690 Td (Surgical outcomes) Tj 0 -40 Td (The median stay was 7 days.) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 540 740] /Resources << /Font << /F0 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(text)).encode()+b' >>\nstream\n'+text+b'\nendstream']
    pdf = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf.extend(f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(pdf); pdf.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]: pdf.extend(f'{offset:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()); path.write_bytes(pdf)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--runtime-cache', type=Path, help='Use an isolated pre-existing test cache')
    args = parser.parse_args()
    archive = args.archive.resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='rtranslate-') as temp:
        folder = Path(temp); project = folder/'研究 项目'; project.mkdir()
        runtime = args.runtime_cache.resolve() if args.runtime_cache else folder/'排版缓存'
        env = dict(os.environ, RTRANSLATE_RUNTIME_CACHE=str(runtime), RTRANSLATE_MANAGED_BOOTSTRAP='1',
                   RTRANSLATE_WORK_DIR=str(folder/'settings'))
        if os.name == 'nt':
            env['LOCALAPPDATA'] = str(folder/'localappdata')
            install = ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','RemoteSigned','-File',
                       str(ROOT/'install.ps1'),'-Project',str(project),'-Archive',str(archive),'-Sha256',digest]
        else:
            install = ['/bin/sh',str(ROOT/'install.sh'),'--project',str(project),'--archive',str(archive),'--sha256',digest]
        subprocess.run(install,env=env,check=True,timeout=300)
        subprocess.run(install,env=env,check=True,timeout=300)
        # Explicitly exercise WorkBuddy's project installation path too.
        extra = ['-TargetHost','workbuddy'] if os.name=='nt' else ['--host','workbuddy']
        subprocess.run(install+extra,env=env,check=True,timeout=300)
        assert (project/'.codebuddy/skills/rtranslate/SKILL.md').is_file()
        skill = project/'.agents/skills/rtranslate'
        launcher = [str(skill/'scripts/rtranslate.cmd')] if os.name=='nt' else ['/bin/sh',str(skill/'scripts/rtranslate.sh')]
        def call(*values):
            result = subprocess.run([*launcher,*values],cwd=project,env=env,stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,encoding='utf-8',errors='replace',timeout=180)
            if result.returncode:
                raise RuntimeError(result.stdout+'\n'+result.stderr)
            return json.loads(result.stdout)
        subprocess.run([*launcher,'--prepare'],env=env,check=True,timeout=900)
        source = project/'样本.pdf';fixture(source)
        job = ['--job',str(project/'论文任务')];call('start',source.name,*job,'--no-figures','--output','both')
        deadline = time.monotonic()+300; handled=0
        source_to_zh = {'Surgical outcomes':'手术结局','The median stay was 7 days.':'住院时间中位数为7天。'}
        while time.monotonic()<deadline:
            row=call('next',*job,'--compact','--wait','8')
            if row['state']=='done': break
            if row['state'] in ('error','cancelled','interrupted'): raise RuntimeError(row)
            if not row.get('tasks'): continue
            answers=[]
            for task in row['tasks']:
                if task['kind']=='raw' and task['messages'][-1]['content'].endswith('Hello'):
                    answers.append('你好')
                elif task['kind']=='paragraphs':
                    answers.append([source_to_zh[text] for text in task['items']]);handled+=len(task['items'])
                elif task['kind']=='single':
                    answers.append(source_to_zh[task['text']]);handled+=1
                else: raise AssertionError(task)
            path=project/'本批答复.json';path.write_text(json.dumps(dict(batch=row['batch'],answers=answers)),encoding='utf-8')
            call('submit',*job,'--file',str(path))
        else: raise TimeoutError('The PDF job did not finish')
        assert handled>=2
        workers=list(runtime.glob('engine-*/Scripts/python.exe' if os.name=='nt' else 'engine-*/bin/python'))
        assert len(workers)==1
        check = ('import sys,pymupdf,unicodedata; d=pymupdf.open(sys.argv[1]); '
                 's=unicodedata.normalize("NFKC","".join(d[0].get_text().split())); '
                 'assert len(d)==1 and "住院时间中位数为7天" in s and ("Surgicaloutcomes" in s)==(sys.argv[2]=="dual"),s')
        for kind in ('mono','dual'):
            subprocess.run([str(workers[0]),'-I','-B','-X','utf8','-c',check,row[kind],kind],check=True)
        from smoke_direct_pdf import verify_direct
        verify_direct(launcher, env, project, source)
        print('Verified fresh installation, repeat installation, WorkBuddy profile, PDF/OCR bootstrap and both translated PDFs.')


if __name__ == '__main__':
    main()
