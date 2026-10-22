import asyncio
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
from PIL import Image
from telegram.ext import ApplicationHandlerStop

from bot import backup_archive as archive
from bot import backup_runtime as runtime

TOKEN_ENV = b'BOT_TOKEN=123456:testing_token\nBOT_ADMIN_IDS=42\nBOT_DEFAULT_METRIC=physical\n'


def fixture(directory):
    root = directory / 'app'
    for key in ('fakhar', 'dorin'):
        template = root / 'template' / key / 'trmplate.xlsx'
        template.parent.mkdir(parents=True)
        wb = openpyxl.Workbook()
        wb.active.append(['code', 'name', 'size', 'divisor'])
        wb.active.append([8400, 'white', '30*60', 108])
        wb.save(template)
        wb.close()
        images = root / 'data' / key / 'catalogs' / '8400'
        images.mkdir(parents=True)
        Image.new('RGB', (10, 10)).save(images / 'img_01.png')
    (root / 'data/admins.json').write_text('[77]')
    env = directory / 'config.env'
    env.write_bytes(TOKEN_ENV)
    return root, env


def mutated_tar(source, destination, mutation):
    with tarfile.open(source) as original:
        members = [(m.name, original.extractfile(m).read()) for m in original]
    with tarfile.open(destination, 'w:gz') as output:
        for name, raw in mutation(members):
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            output.addfile(entry, io.BytesIO(raw))


class ArchiveTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.root, self.env = fixture(self.directory)
        self.source = self.directory / 'backup.tar.gz'
        self.manifest = archive.create_archive(self.root, self.env, self.source, 'test-revision')

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip_includes_configuration_and_both_warehouses(self):
        stage = self.directory / 'stage'
        result = archive.validate_archive(self.source, stage)
        self.assertEqual(result, self.manifest)
        self.assertEqual((stage / '.env').read_bytes(), TOKEN_ENV)
        details = archive.summary(stage, result)
        self.assertEqual(details['stored_admins'], [77])
        for value in details['warehouses'].values():
            self.assertEqual(value, {'products': 1, 'images': 1})
        self.assertFalse(any('venv' in name or name.endswith('.py') for name in result['files']))

    def test_rejects_traversal_duplicates_missing_files_checksum_and_version(self):
        mutations = [
            lambda rows: rows + [('../escape', b'bad')],
            lambda rows: rows + [rows[0]],
            lambda rows: [(n, r) for n, r in rows if n != '.env'],
            lambda rows: [(n, b'[]' if n == 'data/admins.json' else r) for n, r in rows],
            lambda rows: [(n, json.dumps(dict(json.loads(r), version=99)).encode() if n == 'manifest.json' else r) for n, r in rows],
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                broken = self.directory / f'broken-{index}.tar.gz'
                mutated_tar(self.source, broken, mutation)
                with self.assertRaises(archive.BackupError):
                    archive.validate_archive(broken, self.directory / f'stage-{index}')
                self.assertFalse((self.directory / f'stage-{index}').exists())

    def test_rejects_symlinks_and_absolute_names(self):
        for name, kind in [('/etc/passwd', tarfile.REGTYPE), ('data/fakhar/evil.xlsx', tarfile.SYMTYPE)]:
            broken = self.directory / 'evil.tar.gz'
            with tarfile.open(broken, 'w:gz') as output:
                entry = tarfile.TarInfo(name)
                entry.type, entry.linkname = kind, '/etc/passwd'
                output.addfile(entry)
            with self.assertRaises(archive.BackupError):
                archive.validate_archive(broken, self.directory / 'evil-stage')

    def test_all_limits_are_enforced(self):
        for setting, limit in [('MAX_COMPRESSED', 1), ('MAX_EXTRACTED', 1), ('MAX_FILES', 1)]:
            with self.subTest(setting=setting), patch.object(archive, setting, limit):
                with self.assertRaises(archive.BackupError):
                    archive.validate_archive(self.source, self.directory / setting)

    def test_rejects_executable_environment_and_bad_admins(self):
        for raw in [TOKEN_ENV + b'PYTHONPATH=/tmp/evil\n', TOKEN_ENV.replace(b'42', b'not-an-id')]:
            with self.assertRaises(archive.BackupError):
                archive.parse_env(raw)

    def test_creation_rejects_unexpected_files_instead_of_omitting_them(self):
        (self.root / 'data/fakhar/unexpected.txt').write_text('important')
        with self.assertRaises(archive.BackupError):
            archive.create_archive(self.root, self.env, self.directory / 'new.tar.gz')

    def test_dimensionless_source_workbook_is_valid(self):
        import re
        import zipfile
        template = self.root / 'template/fakhar/trmplate.xlsx'
        with zipfile.ZipFile(template) as original:
            data = {n: original.read(n) for n in original.namelist()}
        data['xl/worksheets/sheet1.xml'] = re.sub(rb'<dimension[^>]*/>', b'', data['xl/worksheets/sheet1.xml'])
        with zipfile.ZipFile(template, 'w') as output:
            for name, raw in data.items():
                output.writestr(name, raw)
        archive.validate_payload(self.root)

    def test_restore_preserves_config_or_restores_full_and_rolls_back_failure(self):
        module_path = Path(__file__).parents[1] / 'deploy/restore_helper.py'
        spec = importlib.util.spec_from_file_location('restore_helper', module_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for full, health_ok in [(False, True), (True, True), (True, False)]:
            with self.subTest(full=full, healthy=health_ok), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, env = fixture(base)
                original_env = env.read_bytes()
                stage = base / 'stage'
                archive.validate_archive(self.source, stage)
                (stage / 'data/admins.json').write_text('[99]')
                (stage / '.env').write_bytes(TOKEN_ENV.replace(b'123456', b'654321'))
                safety = base / 'safety-candidate.tar.gz'
                safety.write_bytes(b'safety')
                if health_ok:
                    helper.apply_transaction(root, env, stage, full, lambda: True, safety, owner=False)
                    self.assertEqual((root / 'data/admins.json').read_text(), '[99]')
                    self.assertEqual(archive.parse_env(env.read_bytes())['BOT_TOKEN'], '654321:testing_token' if full else '123456:testing_token')
                    self.assertTrue((base / 'safety-backup.tar.gz').exists())
                else:
                    with self.assertRaises(RuntimeError):
                        helper.apply_transaction(root, env, stage, full, lambda: False, safety, owner=False)
                    self.assertEqual((root / 'data/admins.json').read_text(), '[77]')
                    self.assertEqual(env.read_bytes(), original_env)


class RuntimeTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.patch = patch.object(runtime, 'RUNTIME', self.directory)
        self.patch.start()
        runtime.BACKUP_LOCK = asyncio.Lock()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_tehran_boundary(self):
        # Tehran 03:30 is 00:00 UTC with the installed zone database.
        self.assertIsNone(runtime.due_day(datetime(2026, 10, 22, 23, 59, tzinfo=timezone.utc).astimezone(runtime.TEHRAN).replace(hour=3, minute=29)))
        self.assertEqual(runtime.due_day(datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)), '2026-10-22')

    async def test_catchup_delivers_once_and_retries_only_failed_admin(self):
        now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
        bot = SimpleNamespace(send_document=AsyncMock())
        delivered = []
        async def send(bot, admin, path, caption):
            if admin == 77 and not delivered:
                raise RuntimeError('blocked')
            delivered.append(admin)
        def create(path):
            path.write_bytes(b'archive')
        with patch.object(runtime, 'admin_ids', return_value={42, 77}), patch.object(runtime, 'make_backup', side_effect=create), patch.object(runtime, 'send_archive', new=AsyncMock(side_effect=[None, RuntimeError('blocked'), None])) as sender:
            await runtime.daily_delivery(bot, now)
            self.assertEqual(runtime.read_json(self.directory / 'daily.json', {})['sent'], [42])
            self.assertTrue((self.directory / 'daily-2026-10-22.tar.gz').exists())
            await runtime.daily_delivery(bot, now)
            self.assertEqual(sender.await_count, 2)
            await runtime.daily_delivery(bot, now.replace(hour=11))
            self.assertEqual([c.args[1] for c in sender.await_args_list], [42, 77, 77])
            self.assertFalse((self.directory / 'daily-2026-10-22.tar.gz').exists())
            await runtime.daily_delivery(bot, now.replace(hour=12))
            self.assertEqual(sender.await_count, 3)

    async def test_non_admin_and_group_rejected(self):
        for kind, allowed in [('private', False), ('group', True)]:
            update = SimpleNamespace(effective_chat=SimpleNamespace(type=kind), effective_message=SimpleNamespace(reply_text=AsyncMock()))
            with patch.object(runtime, 'is_admin', return_value=allowed), self.assertRaises(ApplicationHandlerStop):
                await runtime.require_admin(update)

    async def test_manual_export_deletes_temporary_archive_after_delivery_failure(self):
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private', id=42), effective_message=SimpleNamespace(reply_text=AsyncMock()))
        def create(path):
            path.write_bytes(b'archive')
        with patch.object(runtime, 'is_admin', return_value=True), patch.object(runtime, 'make_backup', side_effect=create), patch.object(runtime, 'send_archive', new=AsyncMock(side_effect=RuntimeError('blocked'))), self.assertRaises(ApplicationHandlerStop):
            await runtime.export_backup(update, SimpleNamespace(bot=object()))
        self.assertEqual(list(self.directory.glob('manual-*')), [])

    async def test_cancel_and_expiry_stop_inventory_document_fallthrough(self):
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_message=SimpleNamespace(reply_text=AsyncMock(), document=None))
        context = SimpleNamespace(user_data={'backup_import': {'id': 'a' * 32, 'expires': 0}})
        with patch.object(runtime, 'is_admin', return_value=True), self.assertRaises(ApplicationHandlerStop):
            await runtime.import_document(update, context)
        self.assertNotIn('backup_import', context.user_data)
        context.user_data['backup_import'] = {'id': 'b' * 32}
        with patch.object(runtime, 'is_admin', return_value=True), self.assertRaises(ApplicationHandlerStop):
            await runtime.cancel_import(update, context)
        self.assertNotIn('backup_import', context.user_data)

    async def test_processing_timeout_waits_for_writer_before_releasing_lock(self):
        finished = []
        def write():
            time.sleep(0.04)
            finished.append(True)
        task = asyncio.get_running_loop().run_in_executor(None, write)
        with self.assertRaises(asyncio.TimeoutError):
            await runtime.wait_for_processing(task, 0.001)
        self.assertEqual(finished, [True])

    async def test_updates_wait_for_shared_backup_lock(self):
        application = runtime.CoordinatedApplication.builder().application_class(runtime.CoordinatedApplication).token('123456:testing_token').build()
        with patch('telegram.ext.Application.process_update', new=AsyncMock()) as process, patch.object(runtime, 'restoring', return_value=False):
            async with runtime.BACKUP_LOCK:
                pending = asyncio.create_task(application.process_update(object()))
                await asyncio.sleep(0)
                process.assert_not_awaited()
            await pending
            process.assert_awaited_once()

    async def test_scheduler_registers_daily_0330_and_startup_retry(self):
        application = SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock()), job_queue=MagicMock())
        with patch.object(runtime, 'validate_payload'):
            await runtime.startup(application)
        trigger = application.job_queue.run_daily.call_args.args[1]
        self.assertEqual((trigger.hour, trigger.minute, str(trigger.tzinfo)), (3, 30, 'Asia/Tehran'))
        self.assertEqual(application.job_queue.run_repeating.call_args_list[0].kwargs['first'], 10)

    async def test_backup_commands_registered_before_existing_conversations(self):
        application = MagicMock()
        runtime.register_handlers(application)
        self.assertTrue(all(call.kwargs['group'] == -10 for call in application.add_handler.call_args_list))
        commands = [call.args[0].commands for call in application.add_handler.call_args_list if hasattr(call.args[0], 'commands')]
        self.assertIn(frozenset({'import_backup'}), commands)
        self.assertIn(frozenset({'export_backup'}), commands)

    async def test_import_confirmation_rechecks_admin_and_queues_fixed_request(self):
        identity = 'c' * 32
        directory = self.directory / identity
        directory.mkdir()
        query = SimpleNamespace(data=f'bk:confirm:{identity}:data', answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()), callback_query=query)
        pending = {'id': identity, 'expires': time.time() + 900, 'state': 'confirm', 'mode': 'data', 'sha256': 'a' * 64}
        context = SimpleNamespace(user_data={'backup_import': pending.copy()})
        with patch.object(runtime, 'is_admin', return_value=False), self.assertRaises(ApplicationHandlerStop):
            await runtime.import_callback(update, context)
        self.assertFalse((self.directory / 'restore-request.json').exists())
        with patch.object(runtime, 'is_admin', return_value=True), self.assertRaises(ApplicationHandlerStop):
            await runtime.import_callback(update, context)
        result = runtime.read_json(self.directory / 'restore-request.json', {})
        self.assertEqual((result['id'], result['mode'], result['admin']), (identity, 'data', 42))
        self.assertNotIn('backup_import', context.user_data)

    async def test_upload_preview_and_full_restore_identity_confirmation(self):
        root, env = fixture(self.directory)
        archive_path = self.directory / 'upload-source.tar.gz'
        archive.create_archive(root, env, archive_path)
        identity = 'd' * 32
        async def download(path):
            path.write_bytes(archive_path.read_bytes())
        document = SimpleNamespace(file_name='backup.tar.gz', file_size=archive_path.stat().st_size,
                                   get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock(side_effect=download))))
        message = SimpleNamespace(document=document, reply_text=AsyncMock())
        query = SimpleNamespace(data=f'bk:mode:{identity}:full', answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=SimpleNamespace(id=42), effective_message=message, callback_query=query)
        context = SimpleNamespace(user_data={'backup_import': {'id': identity, 'expires': time.time() + 900, 'state': 'upload'}})
        with patch.object(runtime, 'is_admin', return_value=True), self.assertRaises(ApplicationHandlerStop):
            await runtime.import_document(update, context)
        self.assertEqual(context.user_data['backup_import']['state'], 'choice')
        candidate = MagicMock()
        candidate.__aenter__ = AsyncMock(return_value=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='restored_bot'))))
        candidate.__aexit__ = AsyncMock()
        with patch.object(runtime, 'is_admin', return_value=True), patch.object(runtime, 'Bot', return_value=candidate), self.assertRaises(ApplicationHandlerStop):
            await runtime.import_callback(update, context)
        self.assertEqual(context.user_data['backup_import']['mode'], 'full')
        self.assertIn('@restored_bot', query.edit_message_text.await_args.args[0])
        self.assertFalse((self.directory / 'restore-request.json').exists())
