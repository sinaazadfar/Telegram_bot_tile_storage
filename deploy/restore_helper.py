"""Root-owned, fixed-path restore worker triggered by a systemd PathExists unit."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path('/opt/deldel-tile-bot')
RUNTIME = Path('/var/lib/deldel-tile-backup')
PRIVATE = Path('/var/lib/deldel-tile-backup-root')
ENV = Path('/etc/deldel-tile-bot/config.env')
sys.path.insert(0, str(ROOT))
from bot.backup_archive import MAX_COMPRESSED, create_archive, systemd_env, validate_archive


def atomic_bytes(path, raw, owner=None, user='root'):
    # Publish into the app-writable runtime only from a root-private temporary file.
    temporary_parent = PRIVATE if path.parent == RUNTIME else path.parent
    descriptor, temporary = tempfile.mkstemp(prefix='.restore-', dir=temporary_parent)
    temp = Path(temporary)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temp.chmod(0o640 if owner else 0o600)
    if owner:
        shutil.chown(temp, user=user, group=owner)
    os.replace(temp, path)


def owned_tree(directory):
    for path in [directory, *directory.rglob('*')]:
        if path.is_symlink():
            raise ValueError('Symlink in staged data')
        shutil.chown(path, user='deldel-bot', group='deldel-bot')
        path.chmod(0o700 if path.is_dir() else 0o600)


def apply_transaction(root, env, stage, full, health, safety, owner=True):
    """Retain old trees until startup passes. Used with disposable roots in tests."""
    old_env = env.read_bytes()
    previous = root / '.backup-restore-previous'
    previous.mkdir(mode=0o700)
    promoted = []
    try:
        for name in ('data', 'template'):
            replacement = root / ('.backup-restore-' + name)
            shutil.copytree(stage / name, replacement)
            if owner:
                owned_tree(replacement)
            os.replace(root / name, previous / name)
            try:
                os.replace(replacement, root / name)
            except Exception:
                os.replace(previous / name, root / name)
                raise
            promoted.append(name)
        if full:
            atomic_bytes(env, systemd_env((stage / '.env').read_bytes()), 'deldel-bot' if owner else None)
        if not health():
            raise RuntimeError('Restored service did not become healthy')
        os.replace(safety, safety.with_name('safety-backup.tar.gz'))
    except Exception:
        for name in reversed(promoted):
            shutil.rmtree(root / name)
            os.replace(previous / name, root / name)
        atomic_bytes(env, old_env, 'deldel-bot' if owner else None)
        raise
    finally:
        for name in ('data', 'template'):
            shutil.rmtree(root / ('.backup-restore-' + name), ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)


def service(action):
    subprocess.run(['systemctl', action, 'deldel-tile-bot.service'], check=True, timeout=60)


def healthy_start():
    (RUNTIME / 'startup-ready.json').unlink(missing_ok=True)
    service('start')
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        try:
            ready = json.loads((RUNTIME / 'startup-ready.json').read_text())
            result = subprocess.run(['systemctl', 'show', 'deldel-tile-bot', '-p', 'MainPID', '--value'], capture_output=True, text=True, check=True)
            if int(result.stdout.strip()) == ready['pid'] and time.time() - ready['time'] < 45:
                return True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    service('stop')
    return False


def notify_result(request, success):
    raw = json.dumps({'admin': request['admin'], 'success': success, 'notified': False}).encode()
    atomic_bytes(RUNTIME / 'restore-result.json', raw, 'deldel-bot', user='deldel-bot')


def main():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    request_file = RUNTIME / 'restore-request.json'
    # Read the request without following links; never use caller-supplied absolute paths.
    descriptor = os.open(request_file, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        request_file.unlink()
        raise ValueError('Request too large')
    try:
        request = json.loads(raw)
    except ValueError:
        request_file.unlink()
        raise ValueError('Invalid request JSON') from None
    if not re.fullmatch('[0-9a-f]{32}', request.get('id', '')) or request.get('mode') not in ('data', 'full') or type(request.get('admin')) is not int:
        request_file.unlink()
        raise ValueError('Invalid request')
    atomic_bytes(RUNTIME / 'restore-inprogress', b'')
    success = False
    stopped = False
    try:
        if time.time() > float(request['expires']):
            raise ValueError('Expired request')
        service('stop')
        stopped = True
        with tempfile.TemporaryDirectory(dir=PRIVATE) as temporary:
            work = Path(temporary)
            # Stop the unprivileged process before copying and validating its archive.
            # openat with NOFOLLOW also excludes a substituted parent directory.
            parent = os.open(RUNTIME / request['id'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                descriptor = os.open('upload.tar.gz', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            finally:
                os.close(parent)
            with os.fdopen(descriptor, 'rb') as source, (work / 'upload.tar.gz').open('wb') as target:
                import stat
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError('Not a regular archive')
                raw = source.read(MAX_COMPRESSED + 1)
                if len(raw) > MAX_COMPRESSED or hashlib.sha256(raw).hexdigest() != request['sha256']:
                    raise ValueError('Archive changed')
                target.write(raw)
            stage = work / 'stage'
            validate_archive(work / 'upload.tar.gz', stage)
            safety = PRIVATE / 'safety-candidate.tar.gz'
            # Previous safety-backup is retained if this restore fails.
            create_archive(ROOT, ENV, safety)
            apply_transaction(ROOT, ENV, stage, request['mode'] == 'full', healthy_start, safety)
            success = True
    except Exception as exc:
        # Do not emit exception details: configuration or tokens can appear in them.
        print('Restore rejected or rolled back:', type(exc).__name__, flush=True)
        if stopped:
            service('stop')
            if not healthy_start():
                print('Original service failed health check', flush=True)
    finally:
        notify_result(request, success)
        request_file.unlink(missing_ok=True)
        (RUNTIME / 'restore-inprogress').unlink(missing_ok=True)
        shutil.rmtree(RUNTIME / request['id'], ignore_errors=True)
        (PRIVATE / 'safety-candidate.tar.gz').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
