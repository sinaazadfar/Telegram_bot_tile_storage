import asyncio
import logging
import time
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from telegram import InputFile, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from build_output import process_files

from ..config import (
    DEFAULT_METRIC,
    ALLOWED_METRICS,
    PROCESS_TIMEOUT,
    ensure_warehouse_template_path,
)
from ..text import send_text


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return
    if not context.user_data.get("warehouse"):
        await send_text(update, "ابتدا انبار را انتخاب کنید.")
        return
    document = update.message.document
    filename = document.file_name or ""
    if not (filename.lower().endswith(".xlsx") or filename.lower().endswith(".pdf")):
        await send_text(update, "فقط فایل .xlsx یا .pdf ارسال کنید.")
        return
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "تمپلیت پیدا نشد.")
        return

    logging.info("Received document: %s (%s bytes)", filename, document.file_size)
    metric = DEFAULT_METRIC if DEFAULT_METRIC in ALLOWED_METRICS else "physical"
    tmpdir_path = Path(mkdtemp())
    input_suffix = ".pdf" if filename.lower().endswith(".pdf") else ".xlsx"
    input_path = tmpdir_path / f"input{input_suffix}"
    output_path = tmpdir_path / "output_from_template.xlsx"
    try:
        logging.info("Fetching file info from Telegram.")
        file_obj = await document.get_file()
        logging.info("Downloading file to %s.", input_path)
        await file_obj.download_to_drive(custom_path=str(input_path))
        logging.info("Download complete. Starting processing.")
        loop = asyncio.get_running_loop()
        processing_task = loop.run_in_executor(
            None,
            process_files,
            input_path,
            template_path,
            output_path,
            metric,
            None,
            False,
        )
        if PROCESS_TIMEOUT:
            await asyncio.wait_for(processing_task, timeout=PROCESS_TIMEOUT)
        else:
            await processing_task
        logging.info("Processing done. Uploading output.")
        output_bytes = output_path.read_bytes()
        buffer = BytesIO(output_bytes)
        buffer.seek(0)
        await update.message.reply_document(
            document=InputFile(buffer, filename="output_from_template.xlsx")
        )
        logging.info("Output sent successfully.")
    except asyncio.TimeoutError:
        logging.exception("Timeout while processing request.")
        await send_text(update, "زمان پردازش تمام شد. دوباره تلاش کنید.")
    except (TimedOut, NetworkError):
        logging.exception("Telegram API request failed.")
        await send_text(update, "خطای شبکه. دوباره تلاش کنید.")
    except Exception:
        logging.exception("Failed to process file.")
        await send_text(update, "پردازش انجام نشد. دوباره تلاش کنید.")
        return
    finally:
        for _ in range(5):
            try:
                rmtree(tmpdir_path)
                break
            except PermissionError:
                time.sleep(0.2)
