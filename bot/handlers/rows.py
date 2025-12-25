import asyncio
import logging
from decimal import Decimal, InvalidOperation

from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram import Update

from build_output import process_files

from ..config import (
    ALLOWED_METRICS,
    DEFAULT_METRIC,
    PROCESS_TIMEOUT,
    resolve_warehouse_input_path,
    ensure_warehouse_template_path,
    warehouse_output_path,
)
from ..formatting import build_buttons_from_labels, build_label_map
from ..keyboards import keyboard_with_back, main_keyboard, manage_rows_keyboard
from ..storage import (
    append_template_row,
    delete_template_row,
    find_template_matches_any,
    list_template_rows,
    update_template_row,
)
from ..strings import ADD_ROW_TEXT, BACK_TEXT, CONFIRM_TEXT, DELETE_ROW_TEXT, EDIT_ROW_TEXT
from ..text import send_text

STATE_CODE, STATE_NAME, STATE_SIZE, STATE_DIVISOR, STATE_CONFIRM = range(5)
STATE_DEL_LIST, STATE_DEL_CONFIRM = range(5, 7)
(
    STATE_EDIT_LIST,
    STATE_EDIT_CODE,
    STATE_EDIT_NAME,
    STATE_EDIT_SIZE,
    STATE_EDIT_DIVISOR,
    STATE_EDIT_CONFIRM,
) = range(7, 13)


def keyboard_with_old_value(old_value: str):
    rows: list[list[str]] = []
    if old_value:
        rows.append([old_value])
    return keyboard_with_back(rows)


async def regenerate_output(
    update: Update, context: ContextTypes.DEFAULT_TYPE, note_prefix: str
) -> None:
    warehouse = context.user_data.get("warehouse")
    if not warehouse:
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    input_path = resolve_warehouse_input_path(warehouse)
    output_path = warehouse_output_path(warehouse)
    template_path = ensure_warehouse_template_path(warehouse)
    if not input_path:
        await send_text(
            update,
            f"{note_prefix}\nبرای بروزرسانی خروجی، فایل محصولات را ارسال کنید.",
            reply_markup=manage_rows_keyboard(),
        )
        context.user_data["menu_level"] = "manage_rows"
        return
    if not template_path:
        await send_text(
            update,
            f"{note_prefix}\nتمپلیت پیدا نشد.",
            reply_markup=manage_rows_keyboard(),
        )
        context.user_data["menu_level"] = "manage_rows"
        return
    metric = DEFAULT_METRIC if DEFAULT_METRIC in ALLOWED_METRICS else "physical"
    try:
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(
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
            await asyncio.wait_for(task, timeout=PROCESS_TIMEOUT)
        else:
            await task
        await send_text(
            update,
            f"{note_prefix}\nخروجی بروزرسانی شد.",
            reply_markup=manage_rows_keyboard(),
        )
        context.user_data["menu_level"] = "manage_rows"
    except asyncio.TimeoutError:
        logging.exception("Timeout while regenerating output.")
        await send_text(
            update,
            f"{note_prefix}\nبروزرسانی خروجی به زمان بیشتری نیاز دارد.",
            reply_markup=manage_rows_keyboard(),
        )
        context.user_data["menu_level"] = "manage_rows"
    except Exception:
        logging.exception("Failed to regenerate output.")
        await send_text(
            update,
            f"{note_prefix}\nبروزرسانی خروجی انجام نشد.",
            reply_markup=manage_rows_keyboard(),
        )
        context.user_data["menu_level"] = "manage_rows"


async def add_row_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    context.user_data["new_row"] = {}
    await send_text(update, "کد کالا را وارد کنید.", reply_markup=keyboard_with_back([]))
    return STATE_CODE


async def add_row_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await add_row_cancel(update, context)
    if not text:
        await send_text(update, "کد کالا خالی است. دوباره وارد کنید.")
        return STATE_CODE
    context.user_data["new_row"]["code"] = text
    await send_text(update, "نام طرح را وارد کنید.")
    return STATE_NAME


async def add_row_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await add_row_cancel(update, context)
    if not text:
        await send_text(update, "نام طرح خالی است. دوباره وارد کنید.")
        return STATE_NAME
    context.user_data["new_row"]["name"] = text
    await send_text(update, "سایز را وارد کنید.")
    return STATE_SIZE


async def add_row_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await add_row_cancel(update, context)
    if not text:
        await send_text(update, "سایز خالی است. دوباره وارد کنید.")
        return STATE_SIZE
    context.user_data["new_row"]["size"] = text
    await send_text(update, "مقدار تقسیم پالت را وارد کنید.")
    return STATE_DIVISOR


async def add_row_divisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await add_row_cancel(update, context)
    cleaned = text.replace(",", ".")
    try:
        divisor = Decimal(cleaned)
    except InvalidOperation:
        await send_text(update, "عدد معتبر نیست. دوباره وارد کنید.")
        return STATE_DIVISOR
    context.user_data["new_row"]["divisor"] = divisor
    row = context.user_data["new_row"]
    summary = (
        f"کد کالا: {row['code']}\n"
        f"نام طرح: {row['name']}\n"
        f"سایز: {row['size']}\n"
        f"مقدار تقسیم پالت: {row['divisor']}\n"
        "تایید می‌کنید؟"
    )
    await send_text(
        update, summary, reply_markup=keyboard_with_back([[CONFIRM_TEXT]])
    )
    return STATE_CONFIRM


async def add_row_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await add_row_cancel(update, context)
    if text != CONFIRM_TEXT:
        await send_text(update, "برای ادامه روی تایید بزنید یا برگشت کنید.")
        return STATE_CONFIRM
    row = context.user_data.get("new_row", {})
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        new_row = append_template_row(
            row["code"], row["name"], row["size"], row["divisor"], template_path
        )
    except Exception:
        logging.exception("Failed to append template row.")
        await send_text(update, "ثبت نشد. دوباره تلاش کنید.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["conversation_active"] = False
    await regenerate_output(
        update, context, f"ثبت شد. ردیف {new_row} به تمپلیت اضافه شد."
    )
    return ConversationHandler.END


async def add_row_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_text(update, "لغو شد.", reply_markup=manage_rows_keyboard())
    context.user_data["menu_level"] = "manage_rows"
    context.user_data["skip_back_once"] = True
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def delete_row_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        matches = list_template_rows(template_path)
    except Exception:
        logging.exception("Failed to list template rows.")
        await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["delete_label_map"] = build_label_map(matches)
    buttons = build_buttons_from_labels(context.user_data["delete_label_map"])
    await send_text(
        update,
        "لیست طرح‌ها. برای جستجو متن وارد کنید یا یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DEL_LIST


async def delete_row_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == BACK_TEXT:
        return await delete_row_cancel(update, context)
    label_map = context.user_data.get("delete_label_map", {})
    if text in label_map:
        rows = label_map[text]
        if len(rows) > 1:
            await send_text(
                update, "چند مورد با این عنوان وجود دارد. متن دقیق‌تری وارد کنید."
            )
            return STATE_DEL_LIST
        target = rows[0]
        context.user_data["delete_target"] = target
        await send_text(
            update,
            f"این طرح حذف شود؟\n{text}",
            reply_markup=keyboard_with_back([[CONFIRM_TEXT]]),
        )
        return STATE_DEL_CONFIRM
    try:
        matches = find_template_matches_any(text, template_path)
    except Exception:
        logging.exception("Failed to search template rows.")
        await send_text(update, "جستجو انجام نشد. دوباره تلاش کنید.")
        return STATE_DEL_LIST
    if not matches:
        await send_text(update, "موردی پیدا نشد.")
        return STATE_DEL_LIST
    label_map = build_label_map(matches)
    context.user_data["delete_label_map"] = label_map
    buttons = build_buttons_from_labels(label_map)
    await send_text(
        update,
        "نتیجه جستجو. یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DEL_LIST


async def delete_row_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await delete_row_cancel(update, context)
    if text != CONFIRM_TEXT:
        await send_text(update, "برای ادامه روی تایید بزنید یا برگشت کنید.")
        return STATE_DEL_CONFIRM
    target = context.user_data.get("delete_target")
    if not target:
        await send_text(update, "موردی برای حذف انتخاب نشده است.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        deleted = delete_template_row(target, template_path)
    except Exception:
        logging.exception("Failed to delete template row.")
        await send_text(update, "حذف انجام نشد. دوباره تلاش کنید.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if deleted:
        context.user_data["conversation_active"] = False
        await regenerate_output(update, context, "حذف شد.")
    else:
        await send_text(update, "مورد پیدا نشد.", reply_markup=manage_rows_keyboard())
        context.user_data["menu_level"] = "manage_rows"
        context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def delete_row_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_text(update, "لغو شد.", reply_markup=manage_rows_keyboard())
    context.user_data["menu_level"] = "manage_rows"
    context.user_data["skip_back_once"] = True
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def edit_row_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        matches = list_template_rows(template_path)
    except Exception:
        logging.exception("Failed to list template rows.")
        await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["edit_label_map"] = build_label_map(matches)
    buttons = build_buttons_from_labels(context.user_data["edit_label_map"])
    await send_text(
        update,
        "لیست طرح‌ها. برای جستجو متن وارد کنید یا یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_EDIT_LIST


async def edit_row_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    label_map = context.user_data.get("edit_label_map", {})
    if text in label_map:
        rows = label_map[text]
        if len(rows) > 1:
            await send_text(
                update, "چند مورد با این عنوان وجود دارد. متن دقیق‌تری وارد کنید."
            )
            return STATE_EDIT_LIST
        target = rows[0]
        context.user_data["edit_original"] = target
        context.user_data["edit_new"] = {}
        old_code = target.get("code_display", "")
        await send_text(
            update,
            "کد کالا جدید را وارد کنید.",
            reply_markup=keyboard_with_old_value(old_code),
        )
        return STATE_EDIT_CODE
    try:
        matches = find_template_matches_any(text, template_path)
    except Exception:
        logging.exception("Failed to search template rows.")
        await send_text(update, "جستجو انجام نشد. دوباره تلاش کنید.")
        return STATE_EDIT_LIST
    if not matches:
        await send_text(update, "موردی پیدا نشد.")
        return STATE_EDIT_LIST
    label_map = build_label_map(matches)
    context.user_data["edit_label_map"] = label_map
    buttons = build_buttons_from_labels(label_map)
    await send_text(
        update,
        "نتیجه جستجو. یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_EDIT_LIST


async def edit_row_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    if not text:
        await send_text(update, "کد کالا خالی است. دوباره وارد کنید.")
        return STATE_EDIT_CODE
    context.user_data["edit_new"]["code"] = text
    old_name = context.user_data["edit_original"].get("name_display", "")
    await send_text(
        update,
        "نام طرح جدید را وارد کنید.",
        reply_markup=keyboard_with_old_value(old_name),
    )
    return STATE_EDIT_NAME


async def edit_row_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    if not text:
        await send_text(update, "نام طرح خالی است. دوباره وارد کنید.")
        return STATE_EDIT_NAME
    context.user_data["edit_new"]["name"] = text
    old_size = context.user_data["edit_original"].get("size_display", "")
    await send_text(
        update,
        "سایز جدید را وارد کنید.",
        reply_markup=keyboard_with_old_value(old_size),
    )
    return STATE_EDIT_SIZE


async def edit_row_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    if not text:
        await send_text(update, "سایز خالی است. دوباره وارد کنید.")
        return STATE_EDIT_SIZE
    context.user_data["edit_new"]["size"] = text
    old_divisor = context.user_data["edit_original"].get("divisor_display", "")
    await send_text(
        update,
        "مقدار تقسیم پالت جدید را وارد کنید.",
        reply_markup=keyboard_with_old_value(old_divisor),
    )
    return STATE_EDIT_DIVISOR


async def edit_row_divisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    cleaned = text.replace(",", ".")
    try:
        divisor = Decimal(cleaned)
    except InvalidOperation:
        await send_text(update, "عدد معتبر نیست. دوباره وارد کنید.")
        return STATE_EDIT_DIVISOR
    context.user_data["edit_new"]["divisor"] = divisor
    new_vals = context.user_data["edit_new"]
    summary = (
        f"کد کالا: {new_vals['code']}\n"
        f"نام طرح: {new_vals['name']}\n"
        f"سایز: {new_vals['size']}\n"
        f"مقدار تقسیم پالت: {new_vals['divisor']}\n"
        "تایید می‌کنید؟"
    )
    await send_text(
        update, summary, reply_markup=keyboard_with_back([[CONFIRM_TEXT]])
    )
    return STATE_EDIT_CONFIRM


async def edit_row_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await edit_row_cancel(update, context)
    if text != CONFIRM_TEXT:
        await send_text(update, "برای ادامه روی تایید بزنید یا برگشت کنید.")
        return STATE_EDIT_CONFIRM
    original = context.user_data.get("edit_original")
    new_vals = context.user_data.get("edit_new")
    if not original or not new_vals:
        await send_text(update, "اطلاعات کافی نیست.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        updated = update_template_row(original, new_vals, template_path)
    except Exception:
        logging.exception("Failed to update template row.")
        await send_text(update, "ویرایش انجام نشد. دوباره تلاش کنید.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if updated:
        context.user_data["conversation_active"] = False
        await regenerate_output(update, context, "ویرایش انجام شد.")
    else:
        await send_text(update, "مورد پیدا نشد.", reply_markup=manage_rows_keyboard())
        context.user_data["menu_level"] = "manage_rows"
        context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def edit_row_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_text(update, "لغو شد.", reply_markup=manage_rows_keyboard())
    context.user_data["menu_level"] = "manage_rows"
    context.user_data["skip_back_once"] = True
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


def build_add_row_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{ADD_ROW_TEXT}$"), add_row_start)],
        states={
            STATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_row_code)],
            STATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_row_name)],
            STATE_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_row_size)],
            STATE_DIVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_row_divisor)
            ],
            STATE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_row_confirm)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), add_row_cancel),
            CommandHandler("cancel", add_row_cancel),
        ],
    )


def build_delete_row_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{DELETE_ROW_TEXT}$"), delete_row_start)
        ],
        states={
            STATE_DEL_LIST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_row_list)
            ],
            STATE_DEL_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_row_confirm)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), delete_row_cancel),
            CommandHandler("cancel", delete_row_cancel),
        ],
    )


def build_edit_row_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{EDIT_ROW_TEXT}$"), edit_row_start)
        ],
        states={
            STATE_EDIT_LIST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_list)
            ],
            STATE_EDIT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_code)
            ],
            STATE_EDIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_name)
            ],
            STATE_EDIT_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_size)
            ],
            STATE_EDIT_DIVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_divisor)
            ],
            STATE_EDIT_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_row_confirm)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), edit_row_cancel),
            CommandHandler("cancel", edit_row_cancel),
        ],
    )
