#!/usr/bin/env python3
"""2026.11.13 sunghoon OCI 코드 배포와 장애 시 이전 코드 복구."""
import argparse
import compileall
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT = Path('/home/ubuntu/sea-info-test')
BASE = Path('/home/ubuntu/sea-info-deploy')
NAMES = ('app.py', 'requirements.txt', '.streamlit', 'assets', 'backend',
         'components', 'jobs', 'mock', 'services', 'static', 'tools', 'views')
SERVICES = ('sea-info-api.service', 'sea-info-web.service')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def service(action):
    subprocess.run(['sudo', '-n', 'systemctl', action, *SERVICES], check=True)


def health():
    for attempt in range(30):
        try:
            for url in ('http://127.0.0.1:8000/health', 'http://127.0.0.1:8501/_stcore/health'):
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status != 200:
                        raise RuntimeError('서비스 응답 오류')
                    if url.endswith('/health') and ':8000/' in url and not json.load(response).get('ok'):
                        raise RuntimeError('API 상태 오류')
            return
        except Exception:
            if attempt == 29:
                raise RuntimeError('서비스 상태 확인 실패')
            time.sleep(2)


def rollback(tx):
    # 2026.11.13 sunghoon 백업이 있는 항목만 복원해 중단된 배포도 복구
    service('stop')
    backup, incoming = tx / 'backup', tx / 'incoming'
    for name in NAMES:
        old, current, staged = backup / name, ROOT / name, incoming / name
        if old.exists():
            if current.exists():
                failed = tx / 'failed' / name
                failed.parent.mkdir(exist_ok=True)
                shutil.move(str(current), str(failed))
            shutil.move(str(old), str(current))
        elif not staged.exists() and current.exists() and name not in json.loads((tx / 'original.json').read_text()):
            failed = tx / 'failed' / name
            failed.parent.mkdir(exist_ok=True)
            shutil.move(str(current), str(failed))
    service('start')
    health()
    (tx / 'state').write_text('rolled_back')


def deploy(archive, tx):
    incoming = tx / 'incoming'
    incoming.mkdir(parents=True)
    (tx / 'backup').mkdir()
    # 2026.11.13 sunghoon 배포 압축파일의 경로 이탈과 링크를 차단
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] not in NAMES or '..' in parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError('허용하지 않은 압축파일 항목: ' + member.name)
        tf.extractall(incoming, filter='data')
    for name in ('app.py', 'requirements.txt', 'backend', 'tools', 'views'):
        if not (incoming / name).exists():
            raise ValueError('필수 항목 누락: ' + name)
    if digest(incoming / 'requirements.txt') != digest(ROOT / 'requirements.txt'):
        raise ValueError('의존성이 변경되어 자동 배포를 중단합니다.')
    if not compileall.compile_dir(str(incoming), quiet=1):
        raise ValueError('Python 문법 검사 실패')
    (tx / 'original.json').write_text(json.dumps([n for n in NAMES if (ROOT / n).exists()]))
    (tx / 'state').write_text('switching')
    try:
        service('stop')
        for name in NAMES:
            current, old, new = ROOT / name, tx / 'backup' / name, incoming / name
            if current.exists():
                shutil.move(str(current), str(old))
            if new.exists():
                shutil.move(str(new), str(current))
        service('start')
        health()
        (tx / 'state').write_text('deployed')
    except BaseException:
        try:
            rollback(tx)
        except BaseException as error:
            print('자동 복구 실패. 수동 복구 필요:', tx, error, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('deploy', 'rollback'))
    parser.add_argument('build', help='숫자 Jenkins 빌드 번호')
    parser.add_argument('archive', nargs='?')
    args = parser.parse_args()
    if not args.build.isdecimal():
        parser.error('빌드 번호는 숫자여야 합니다')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    tx = BASE / ('build-' + args.build)
    with (BASE / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'deploy':
            if not args.archive or tx.exists():
                parser.error('압축파일이 없거나 이미 사용한 빌드 번호입니다')
            for existing in BASE.glob('build-*/state'):
                if existing.read_text() == 'switching':
                    raise RuntimeError('이전 배포의 복구가 필요합니다: ' + str(existing.parent))
            tx.mkdir(mode=0o700)
            deploy(Path(args.archive), tx)
        else:
            if not (tx / 'state').exists() or (tx / 'state').read_text() not in ('switching', 'deployed'):
                parser.error('복구할 수 없는 배포입니다')
            # 2026.11.13 sunghoon 더 최신 배포를 과거 백업으로 덮어쓰지 않도록 제한
            for other in BASE.glob('build-*/state'):
                if int(other.parent.name[6:]) > int(args.build) and other.read_text() in ('switching', 'deployed'):
                    raise RuntimeError('더 최신 배포부터 복구해야 합니다: ' + str(other.parent))
            rollback(tx)


if __name__ == '__main__':
    main()
