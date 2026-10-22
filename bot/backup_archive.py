"""Versioned, bounded backup format. No service privileges or Telegram calls here."""
import hashlib
import io
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values

MAX_COMPRESSED = 19 * 1024 * 1024
MAX_EXTRACTED = 200 * 1024 * 1024
MAX_FILES = 10000
ENV_KEYS = {
    'BOT_TOKEN', 'BOT_ADMIN_IDS', 'BOT_DEFAULT_METRIC', 'BOT_CONNECT_TIMEOUT',
    'BOT_READ_TIMEOUT', 'BOT_WRITE_TIMEOUT', 'BOT_POOL_TIMEOUT',
    'BOT_PROCESS_TIMEOUT', 'BOT_PROXY', 'BOT_POOL_SIZE', 'BOT_UPDATES_POOL_SIZE',
    'BOT_PDF_FONT_PATH',
}
REQUIRED = {'.env', 'data/admins.json', 'template/fakhar/trmplate.xlsx',
            'template/dorin/trmplate.xlsx'}


class BackupError(ValueError):
    pass


def allowed_name(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        return False
    if name in ('.env', 'manifest.json', 'data/admins.json'):
        return True
    if len(p.parts) < 3 or p.parts[0] not in ('data', 'template') or p.parts[1] not in ('fakhar', 'dorin'):
        return False
    return p.suffix.lower() in {'.xlsx', '.pdf', '.jpg', '.jpeg', '.png', '.webp'}


def parse_env(raw):
    if len(raw) > 65536:
        raise BackupError('Configuration too large')
    values = dict(dotenv_values(stream=io.StringIO(raw.decode('utf-8')), interpolate=False))
    if not values or set(values) - ENV_KEYS or any(v is None or any(c in v for c in '\r\n\x00') for v in values.values()):
        raise BackupError('Unsupported configuration')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', values.get('BOT_TOKEN', '')):
        raise BackupError('Invalid bot token format')
    ids = values.get('BOT_ADMIN_IDS', '')
    if not ids or not all(x.strip().isdigit() and int(x) > 0 for x in ids.split(',')):
        raise BackupError('Invalid administrator IDs')
    return values


def systemd_env(raw):
    values = parse_env(raw)
    return ''.join(k + '="' + v.replace('\\', '\\\\').replace('"', '\\"') + '"\n' for k, v in sorted(values.items())).encode()


def create_archive(root, env_path, destination, revision='unknown'):
    root, destination = Path(root), Path(destination)
    files = {}
    for top in ('data', 'template'):
        for path in (root / top).rglob('*'):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise BackupError('Symbolic links cannot be backed up')
            if path.is_file() and path.name != '.gitkeep':
                if not allowed_name(name):
                    raise BackupError('Unexpected file in data: ' + name)
                files[name] = path
    if sum(p.stat().st_size for p in files.values()) > MAX_EXTRACTED - 2 * 1024 * 1024 or len(files) + 2 > MAX_FILES:
        raise BackupError('Backup exceeds limits')
    data = {name: p.read_bytes() for name, p in files.items()}
    data['.env'] = Path(env_path).read_bytes()
    data.setdefault('data/admins.json', b'[]')
    parse_env(data['.env'])
    if not REQUIRED <= data.keys() or len(data) + 1 > MAX_FILES or sum(map(len, data.values())) > MAX_EXTRACTED - 2 * 1024 * 1024:
        raise BackupError('Missing files or backup exceeds limits')
    manifest = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'revision': revision, 'files': {name: {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in sorted(data.items())}}
    data['manifest.json'] = json.dumps(manifest, ensure_ascii=False).encode()
    try:
        with tarfile.open(destination, 'w:gz') as archive:
            for name, raw in sorted(data.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(raw), 0o600
                archive.addfile(info, io.BytesIO(raw))
        destination.chmod(0o600)
        if destination.stat().st_size > MAX_COMPRESSED:
            raise BackupError('Backup exceeds 19 MB; no files were omitted')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return manifest


def validate_archive(source, stage):
    """Extract only checked regular files, never tarfile.extract/extractall."""
    source, stage = Path(source), Path(stage)
    if source.stat().st_size > MAX_COMPRESSED:
        raise BackupError('Archive exceeds 19 MB')
    stage.mkdir(parents=True, exist_ok=False, mode=0o700)
    seen, total = {}, 0
    try:
        with tarfile.open(source, 'r|gz') as archive:
            for member in archive:
                if not member.isfile() or not allowed_name(member.name) or member.name in seen:
                    raise BackupError('Unsafe, duplicate, or unsupported archive entry')
                total += member.size
                if member.size < 0 or total > MAX_EXTRACTED or len(seen) >= MAX_FILES:
                    raise BackupError('Archive exceeds extraction limits')
                if member.name == 'manifest.json' and member.size > 2 * 1024 * 1024:
                    raise BackupError('Manifest too large')
                if member.name in ('.env', 'data/admins.json') and member.size > 65536:
                    raise BackupError('Configuration or admin list too large')
                target = stage / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                reader = archive.extractfile(member)
                digest, remaining = hashlib.sha256(), member.size
                with target.open('xb') as output:
                    while remaining:
                        chunk = reader.read(min(65536, remaining))
                        if not chunk:
                            raise BackupError('Truncated archive')
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                target.chmod(0o600)
                seen[member.name] = {'size': member.size, 'sha256': digest.hexdigest()}
        if not REQUIRED | {'manifest.json'} <= seen.keys():
            raise BackupError('Required files missing')
        manifest = json.loads((stage / 'manifest.json').read_text())
        if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest.get('version') != 1:
            raise BackupError('Unsupported backup version')
        datetime.fromisoformat(manifest['created_at'])
        files = {k: v for k, v in seen.items() if k != 'manifest.json'}
        if manifest.get('files') != files:
            raise BackupError('Manifest checksum or file list mismatch')
        parse_env((stage / '.env').read_bytes())
        admins = json.loads((stage / 'data/admins.json').read_text())
        if not isinstance(admins, list) or any(type(x) is not int or x <= 0 for x in admins):
            raise BackupError('Invalid stored administrators')
        validate_payload(stage)
        return manifest
    except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        import shutil
        shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError('Invalid backup archive') from None


def validate_payload(root):
    """Check workbook ZIP sizes before parsing; decode every image."""
    import zipfile
    import openpyxl
    from PIL import Image
    image_counts = {}
    expanded_workbooks = 0
    for top in ('data', 'template'):
        for path in (Path(root) / top).rglob('*'):
            if not path.is_file():
                continue
            if path.suffix.lower() == '.xlsx':
                with zipfile.ZipFile(path) as workbook:
                    members = workbook.infolist()
                    expanded_workbooks += sum(m.file_size for m in members)
                    if len(members) > MAX_FILES or expanded_workbooks > MAX_EXTRACTED:
                        raise BackupError('Workbook exceeds limits')
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                try:
                    if (wb.active.max_row or 0) > 100000 or (wb.active.max_column or 0) > 1000:
                        raise BackupError('Workbook dimensions exceed limits')
                    for index, row in enumerate(wb.active.values, 1):
                        if index > 100000 or len(row) > 1000:
                            raise BackupError('Workbook dimensions exceed limits')
                finally:
                    wb.close()
            elif path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
                with Image.open(path) as image:
                    image.verify()
                image_counts[path.parent] = image_counts.get(path.parent, 0) + 1
                if image_counts[path.parent] > 10:
                    raise BackupError('Catalog exceeds ten images')


def summary(stage, manifest):
    import openpyxl
    root = Path(stage)
    result = {'created_at': manifest['created_at'], 'files': len(manifest['files']), 'warehouses': {}}
    for key in ('fakhar', 'dorin'):
        template = root / 'data' / key / 'template.xlsx'
        if not template.exists():
            template = root / 'template' / key / 'trmplate.xlsx'
        wb = openpyxl.load_workbook(template, read_only=True, data_only=True)
        try:
            products = sum(1 for row in wb.active.iter_rows(min_row=2, max_col=2, values_only=True) if any(v is not None for v in row))
        finally:
            wb.close()
        images = sum(1 for p in (root / 'data' / key).rglob('*') if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'))
        result['warehouses'][key] = {'products': products, 'images': images}
    result['stored_admins'] = json.loads((root / 'data/admins.json').read_text())
    result['config_admins'] = sorted(int(v.strip()) for v in parse_env((root / '.env').read_bytes())['BOT_ADMIN_IDS'].split(','))
    return result
