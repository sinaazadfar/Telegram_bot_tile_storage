"""Private admin commands and durable daily delivery, serialized with bot updates."""
import asyncio
from datetime import datetime, time, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time as clock
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .auth import admin_ids, is_admin
from .config import ROOT_DIR, DATA_DIR
from .backup_archive import BackupError, MAX_COMPRESSED, create_archive, parse_env, summary, validate_archive, validate_payload

TEHRAN = ZoneInfo('Asia/Tehran')
RUNTIME = Path(os.getenv('DELDEL_BACKUP_RUNTIME', '/var/lib/deldel-tile-backup'))
ENV_PATH = Path(os.getenv('DELDEL_ENV_FILE', '/etc/deldel-tile-bot.env'))
LOG = logging.getLogger(__name__)
BACKUP_LOCK = asyncio.Lock()


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf8'))


def restoring():
    return (RUNTIME / 'restore-request.json').exists() or (RUNTIME / 'restore-inprogress').exists()


class CoordinatedApplication(Application):
    async def process_update(self, update):
        async with BACKUP_LOCK:
            if restoring():
                if update.effective_message:
                    await update.effective_message.reply_text('بازیابی در حال انجام است؛ کمی بعد دوباره تلاش کنید.')
                return
            await super().process_update(update)


async def wait_for_processing(task, timeout):
    # A timed-out executor thread continues writing. Keep the update lock until it exits.
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout) if timeout else await asyncio.shield(task)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            await asyncio.shield(task)
        finally:
            raise


def revision():
    result = subprocess.run(['git', '-C', str(ROOT_DIR), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else 'unknown'


def make_backup(destination):
    manifest = create_archive(ROOT_DIR, ENV_PATH, destination, revision())
    try:
        with tempfile.TemporaryDirectory(dir=RUNTIME) as temporary:
            validate_archive(destination, Path(temporary) / 'check')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return manifest


async def require_admin(update):
    if not update.effective_chat or update.effective_chat.type != 'private' or not is_admin(update):
        if update.effective_message:
            await update.effective_message.reply_text('این دستور فقط برای مدیر، در گفت‌وگوی خصوصی با ربات، فعال است.')
        raise ApplicationHandlerStop


async def send_archive(bot, chat_id, path, caption):
    with path.open('rb') as archive:
        return await bot.send_document(chat_id=chat_id, document=archive, filename=path.name,
                                       caption=caption, write_timeout=120, read_timeout=120)


async def export_backup(update, context):
    await require_admin(update)
    await update.effective_message.reply_text('در حال تهیه نسخه پشتیبان هر دو انبار؛ فایل شامل توکن ربات است.')
    path = RUNTIME / ('manual-' + secrets.token_hex(8) + '.tar.gz')
    try:
        await asyncio.to_thread(make_backup, path)
        await send_archive(context.bot, update.effective_chat.id, path,
                           'نسخه پشتیبان کامل شامل تنظیمات محرمانه است. فایل را فقط در محل امن نگه دارید.')
    except Exception as exc:
        LOG.error('Manual backup failed (%s)', type(exc).__name__)
        await update.effective_message.reply_text('تهیه یا ارسال پشتیبان انجام نشد. حداکثر حجم فایل ۱۹ مگابایت است؛ هیچ فایلی حذف نشده است.')
    finally:
        path.unlink(missing_ok=True)
    raise ApplicationHandlerStop


def clear_pending(context):
    pending = context.user_data.pop('backup_import', None)
    if pending:
        shutil.rmtree(RUNTIME / pending['id'], ignore_errors=True)


async def import_backup(update, context):
    await require_admin(update)
    clear_pending(context)
    # Existing conversation handlers retain state until restart, but backup routing takes priority.
    context.user_data['backup_import'] = {'id': secrets.token_hex(16), 'expires': clock.time() + 900, 'state': 'upload'}
    await update.effective_message.reply_text('فایل پشتیبان .tar.gz همین ربات را بفرستید (حداکثر ۱۹ مگابایت). تا ۱۵ دقیقه فرصت دارید. لغو: /cancel')
    raise ApplicationHandlerStop


async def cancel_import(update, context):
    if not context.user_data.get('backup_import'):
        return
    await require_admin(update)
    clear_pending(context)
    await update.effective_message.reply_text('بازیابی لغو شد. داده‌ای تغییر نکرد.')
    raise ApplicationHandlerStop


def restore_buttons(identifier, mode=None):
    if mode:
        rows = [[InlineKeyboardButton('تأیید جایگزینی داده‌ها' if mode == 'data' else 'تأیید بازیابی کامل و راه‌اندازی مجدد', callback_data=f'bk:confirm:{identifier}:{mode}')]]
    else:
        rows = [[InlineKeyboardButton('بازیابی داده‌ها (پیشنهادی)', callback_data=f'bk:mode:{identifier}:data')],
                [InlineKeyboardButton('بازیابی همه‌چیز با تنظیمات', callback_data=f'bk:mode:{identifier}:full')]]
    rows.append([InlineKeyboardButton('لغو', callback_data=f'bk:cancel:{identifier}')])
    return InlineKeyboardMarkup(rows)


async def import_document(update, context):
    pending = context.user_data.get('backup_import')
    if not pending:
        return
    await require_admin(update)
    if clock.time() > pending['expires']:
        clear_pending(context)
        await update.effective_message.reply_text('مهلت تمام شد؛ دوباره /import_backup را بفرستید.')
        raise ApplicationHandlerStop
    document = update.effective_message.document
    if not document:
        await update.effective_message.reply_text('فایل .tar.gz را بفرستید یا /cancel را انتخاب کنید.')
        raise ApplicationHandlerStop
    if pending['state'] != 'upload':
        await update.effective_message.reply_text('ابتدا بازیابی قبلی را تأیید یا لغو کنید.')
        raise ApplicationHandlerStop
    if not (document.file_name or '').endswith('.tar.gz') or not document.file_size or document.file_size > MAX_COMPRESSED:
        await update.effective_message.reply_text('فقط فایل .tar.gz تا ۱۹ مگابایت پذیرفته می‌شود.')
        raise ApplicationHandlerStop
    directory = RUNTIME / pending['id']
    directory.mkdir(mode=0o700)
    try:
        archive = directory / 'upload.tar.gz'
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(archive)
        manifest = await asyncio.to_thread(validate_archive, archive, directory / 'stage')
        details = await asyncio.to_thread(summary, directory / 'stage', manifest)
        pending.update(state='choice', details=details, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        text = f"پشتیبان {details['created_at']}\nتعداد فایل‌ها: {details['files']}"
        for key, label in (('fakhar', 'فخار'), ('dorin', 'دورین')):
            values = details['warehouses'][key]
            text += f"\n{label}: {values['products']} محصول، {values['images']} تصویر"
        await update.effective_message.reply_text(text + '\nروش بازیابی را انتخاب کنید؛ هنوز داده‌ای تغییر نکرده است.', reply_markup=restore_buttons(pending['id']))
    except Exception as exc:
        LOG.error('Backup import validation failed (%s)', type(exc).__name__)
        clear_pending(context)
        await update.effective_message.reply_text('فایل پشتیبان معتبر نیست یا بررسی آن انجام نشد. هیچ داده‌ای تغییر نکرد. دوباره /import_backup را بفرستید.')
    raise ApplicationHandlerStop


async def import_callback(update, context):
    await require_admin(update)
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    pending = context.user_data.get('backup_import')
    if not pending or len(parts) < 3 or parts[2] != pending['id'] or clock.time() > pending['expires']:
        clear_pending(context)
        await query.edit_message_text('درخواست منقضی شده است. دوباره /import_backup را بفرستید.')
        raise ApplicationHandlerStop
    action = parts[1]
    if action == 'cancel':
        clear_pending(context)
        await query.edit_message_text('بازیابی لغو شد؛ داده‌ای تغییر نکرد.')
        raise ApplicationHandlerStop
    mode = parts[3] if len(parts) == 4 else ''
    if mode not in ('data', 'full') or pending['state'] not in ('choice', 'confirm'):
        raise ApplicationHandlerStop
    if action == 'mode':
        details = pending['details']
        after_admins = set(details['stored_admins']) | (set(details['config_admins']) if mode == 'full' else set(parse_env(ENV_PATH.read_bytes())['BOT_ADMIN_IDS'].replace(' ', '').split(',')))
        after_admins = {int(x) for x in after_admins}
        text = 'داده‌های هر دو انبار و فهرست مدیران ذخیره‌شده جایگزین می‌شوند.\nمدیران پس از بازیابی: ' + ', '.join(map(str, sorted(after_admins)))
        if update.effective_user.id not in after_admins:
            text += '\nتوجه: دسترسی مدیریت شما در این پشتیبان وجود ندارد.'
        if mode == 'full':
            token = parse_env((RUNTIME / pending['id'] / 'stage/.env').read_bytes())['BOT_TOKEN']
            try:
                async with Bot(token) as candidate:
                    identity = await candidate.get_me()
                text += f'\nتنظیمات نیز جایگزین می‌شود. ربات فعال پس از بازیابی: @{identity.username}'
            except Exception:
                await query.edit_message_text('توکن پشتیبان تأیید نشد. دوباره /import_backup را اجرا کنید یا /cancel را بفرستید.')
                raise ApplicationHandlerStop
        else:
            text += '\nتوکن و تنظیمات فعلی حفظ می‌شود.'
        pending.update(state='confirm', mode=mode)
        await query.edit_message_text(text + '\nپیش از تغییر، پشتیبان ایمنی ساخته می‌شود. تأیید می‌کنید؟', reply_markup=restore_buttons(pending['id'], mode))
    elif action == 'confirm' and pending['state'] == 'confirm' and pending.get('mode') == mode:
        await query.edit_message_text('بازیابی تأیید شد؛ ربات موقتاً متوقف و دوباره راه‌اندازی می‌شود. نتیجه پس از راه‌اندازی ارسال خواهد شد.')
        save_json(RUNTIME / 'restore-request.json', {'id': pending['id'], 'mode': mode, 'admin': update.effective_user.id,
                  'sha256': pending['sha256'], 'expires': pending['expires']})
        context.user_data.pop('backup_import', None)
    raise ApplicationHandlerStop


def due_day(now):
    local = now.astimezone(TEHRAN)
    return local.date().isoformat() if local.time() >= time(3, 30) else None


async def daily_delivery(bot, now):
    """Called with BACKUP_LOCK held. Record each successful recipient immediately."""
    day = due_day(now)
    if not day:
        return
    state_path = RUNTIME / 'daily.json'
    state = read_json(state_path, {})
    if state.get('day') != day:
        for old in RUNTIME.glob('daily-*.tar.gz'):
            old.unlink(missing_ok=True)
        state = {'day': day, 'sent': [], 'attempts': 0, 'retry_at': 0}
    recipients = sorted(admin_ids() - set(state['sent']))
    if not recipients:
        (RUNTIME / f'daily-{day}.tar.gz').unlink(missing_ok=True)
        return
    if now.timestamp() < state.get('retry_at', 0):
        return
    archive = RUNTIME / f'daily-{day}.tar.gz'
    try:
        if not archive.exists():
            await asyncio.to_thread(make_backup, archive)
        for recipient in recipients:
            try:
                await send_archive(bot, recipient, archive, f'پشتیبان روزانه {day}؛ برنامه ارسال: ۰۳:۳۰ تهران. شامل توکن محرمانه ربات است.')
                state['sent'].append(recipient)
                save_json(state_path, state)
            except Exception as exc:
                LOG.error('Daily backup delivery failed for admin %s (%s)', recipient, type(exc).__name__)
    except Exception as exc:
        LOG.error('Daily backup creation failed (%s)', type(exc).__name__)
        if not state.get('error_notified'):
            for recipient in recipients:
                try:
                    await bot.send_message(recipient, 'پشتیبان روزانه ساخته نشد. حجم یا سلامت داده‌ها باید بررسی شود؛ هیچ فایلی از پشتیبان حذف نشده است.')
                except Exception:
                    pass
            state['error_notified'] = True
    state['attempts'] += 1
    state['retry_at'] = now.timestamp() + min(3600, 300 * 2 ** min(state['attempts'] - 1, 4))
    save_json(state_path, state)
    if admin_ids() <= set(state['sent']):
        archive.unlink(missing_ok=True)


async def schedule_tick(context):
    async with BACKUP_LOCK:
        if restoring():
            return
        result_path = RUNTIME / 'restore-result.json'
        result = read_json(result_path, None)
        if result and not result.get('notified'):
            try:
                await context.bot.send_message(result['admin'], 'بازیابی با موفقیت انجام شد.' if result['success'] else 'بازیابی انجام نشد؛ داده‌ها و تنظیمات قبلی حفظ یا بازگردانده شد.')
                result['notified'] = True
                save_json(result_path, result)
            except Exception as exc:
                LOG.error('Restore notification failed (%s)', type(exc).__name__)
        cutoff = clock.time() - 900
        for directory in RUNTIME.iterdir():
            if directory.is_dir() and len(directory.name) == 32 and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory)
        await daily_delivery(context.bot, datetime.now(TEHRAN))


async def health_tick(context):
    if context.application.running:
        jobs = context.application.job_queue.get_jobs_by_name('backup-0330-tehran')
        save_json(RUNTIME / 'startup-ready.json', {'pid': os.getpid(), 'time': clock.time(),
                  'next_backup': jobs[0].next_t.isoformat() if jobs else None})


async def startup(application):
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Initialization authenticates the bot before this hook. Validate restored workbooks too.
    await asyncio.to_thread(validate_payload, ROOT_DIR)
    await application.bot.set_my_commands([
        ('start', 'شروع'), ('help', 'راهنما'), ('userid', 'شناسه کاربری'),
        ('export_backup', 'دریافت پشتیبان (مدیر)'), ('import_backup', 'بازیابی پشتیبان (مدیر)'),
    ])
    application.job_queue.run_daily(schedule_tick, time(3, 30, tzinfo=TEHRAN), name='backup-0330-tehran')
    application.job_queue.run_repeating(schedule_tick, interval=60, first=10, name='backup-retry-and-startup')
    application.job_queue.run_repeating(health_tick, interval=30, first=1, name='backup-startup-health')
    for old in RUNTIME.glob('manual-*.tar.gz'):
        old.unlink(missing_ok=True)


def register_handlers(application):
    # Separate group precedes every conversation, and HandlerStop prevents fall-through.
    for handler in (
        CommandHandler('export_backup', export_backup),
        CommandHandler('import_backup', import_backup),
        CommandHandler('cancel', cancel_import),
        CallbackQueryHandler(import_callback, pattern=r'^bk:'),
        MessageHandler(filters.ALL & ~filters.COMMAND, import_document),
    ):
        application.add_handler(handler, group=-10)
