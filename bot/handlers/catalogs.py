import logging
import mimetypes
from pathlib import Path

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..catalogs import (
    MAX_CATALOG_IMAGES,
    catalog_image_count,
    clear_catalog,
    list_catalog_images,
    next_catalog_image_path,
)
from ..config import ensure_warehouse_template_path
from ..formatting import build_buttons_from_labels, build_label_map
from ..keyboards import (
    catalog_menu_keyboard,
    keyboard_with_back,
    main_keyboard,
    manage_menu_keyboard,
)
from ..storage import find_template_matches_any, list_template_rows
from ..strings import (
    BACK_TEXT,
    CATALOG_CREATE_TEXT,
    CATALOG_DELETE_TEXT,
    CATALOG_DONE_TEXT,
    CATALOG_EDIT_TEXT,
    CATALOG_MENU_TEXT,
    CONFIRM_TEXT,
)
from ..text import send_text

STATE_CATALOG_MENU = 0
STATE_CATALOG_SELECT = 1
STATE_CATALOG_UPLOAD = 2
STATE_CATALOG_DELETE_CONFIRM = 3
STATE_CATALOG_OVERWRITE_CONFIRM = 4


async def catalogs_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    context.user_data["menu_level"] = "manage_menu"
    await send_text(update, "مدیریت کاتالوگ:", reply_markup=catalog_menu_keyboard())
    return STATE_CATALOG_MENU


async def catalogs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        await send_text(update, "به منوی تنظیمات برگشتید.", reply_markup=manage_menu_keyboard())
        context.user_data["skip_back_once"] = True
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    mode_map = {
        CATALOG_CREATE_TEXT: "create",
        CATALOG_EDIT_TEXT: "edit",
        CATALOG_DELETE_TEXT: "delete",
    }
    mode = mode_map.get(text)
    if not mode:
        await send_text(update, "یکی از گزینه‌های منو را انتخاب کنید.")
        return STATE_CATALOG_MENU
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "قالب انبار پیدا نشد.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        matches = list_template_rows(template_path)
    except Exception:
        logging.exception("Failed to list template rows for catalog.")
        await send_text(update, "خواندن لیست طرح‌ها ممکن نیست.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if not matches:
        await send_text(update, "طرحی برای انتخاب وجود ندارد.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["catalog_mode"] = mode
    context.user_data["catalog_label_map"] = build_label_map(matches)
    buttons = build_buttons_from_labels(context.user_data["catalog_label_map"])
    await send_text(
        update,
        "طرح موردنظر را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_CATALOG_SELECT


async def catalogs_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await catalogs_cancel(update, context)
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "قالب انبار پیدا نشد.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    label_map = context.user_data.get("catalog_label_map", {})
    if text in label_map:
        rows = label_map[text]
        if len(rows) > 1:
            await send_text(update, "چند طرح مشابه پیدا شد. دقیق‌تر جستجو کنید.")
            return STATE_CATALOG_SELECT
        return await handle_catalog_target(update, context, rows[0])
    try:
        matches = find_template_matches_any(text, template_path)
    except Exception:
        logging.exception("Failed to search template rows for catalog.")
        await send_text(update, "جستجو ممکن نیست. دوباره تلاش کنید.")
        return STATE_CATALOG_SELECT
    if not matches:
        await send_text(update, "طرحی پیدا نشد.")
        return STATE_CATALOG_SELECT
    label_map = build_label_map(matches)
    context.user_data["catalog_label_map"] = label_map
    buttons = build_buttons_from_labels(label_map)
    await send_text(
        update,
        "نتایج فیلتر شده. یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_CATALOG_SELECT


async def handle_catalog_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target: dict
) -> int:
    mode = context.user_data.get("catalog_mode")
    if mode == "delete":
        context.user_data["catalog_target"] = target
        await send_text(
            update,
            "برای حذف کاتالوگ تایید کنید.",
            reply_markup=keyboard_with_back([[CONFIRM_TEXT]]),
        )
        return STATE_CATALOG_DELETE_CONFIRM
    existing = list_catalog_images(context.user_data["warehouse"], target)
    existing_count = len(existing)
    if mode == "create" and existing:
        context.user_data["catalog_target"] = target
        await send_text(
            update,
            "برای جایگزینی کاتالوگ قبلی تایید کنید.",
            reply_markup=keyboard_with_back([[CONFIRM_TEXT]]),
        )
        return STATE_CATALOG_OVERWRITE_CONFIRM
    if mode == "edit" and existing_count >= MAX_CATALOG_IMAGES:
        await send_text(
            update,
            "حداکثر تعداد تصویر قبلا ثبت شده است.",
            reply_markup=manage_menu_keyboard(),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    return await start_catalog_upload(update, context, target)


async def start_catalog_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target: dict
) -> int:
    context.user_data["catalog_target"] = target
    warehouse = context.user_data.get("warehouse")
    existing_count = 0
    if warehouse:
        existing_count = catalog_image_count(warehouse, target)
    remaining = max(0, MAX_CATALOG_IMAGES - existing_count)
    if existing_count:
        message = (
            f"اکنون {existing_count} تصویر ثبت شده است. "
            f"حداکثر {remaining} تصویر دیگر می‌توانید اضافه کنید. "
            f"برای پایان، «{CATALOG_DONE_TEXT}» را بزنید."
        )
    else:
        message = (
            f"حداکثر {MAX_CATALOG_IMAGES} تصویر ارسال کنید. "
            f"برای پایان، «{CATALOG_DONE_TEXT}» را بزنید."
        )
    await send_text(
        update,
        message,
        reply_markup=keyboard_with_back([[CATALOG_DONE_TEXT]]),
    )
    return STATE_CATALOG_UPLOAD


async def catalog_overwrite_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await catalogs_cancel(update, context)
    if text != CONFIRM_TEXT:
        await send_text(update, "برای ادامه، تایید کنید.")
        return STATE_CATALOG_OVERWRITE_CONFIRM
    target = context.user_data.get("catalog_target")
    if not target:
        await send_text(update, "طرح انتخاب نشده است.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    clear_catalog(context.user_data["warehouse"], target)
    return await start_catalog_upload(update, context, target)


async def catalog_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await catalogs_cancel(update, context)
    if text != CONFIRM_TEXT:
        await send_text(update, "برای ادامه، تایید کنید.")
        return STATE_CATALOG_DELETE_CONFIRM
    target = context.user_data.get("catalog_target")
    if not target:
        await send_text(update, "طرح انتخاب نشده است.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if not list_catalog_images(context.user_data["warehouse"], target):
        await send_text(update, "کاتالوگی برای حذف پیدا نشد.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    clear_catalog(context.user_data["warehouse"], target)
    await send_text(update, "کاتالوگ حذف شد.", reply_markup=manage_menu_keyboard())
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def catalog_upload_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        return await catalogs_cancel(update, context)
    if text == CATALOG_DONE_TEXT:
        target = context.user_data.get("catalog_target")
        if not target:
            await send_text(update, "طرح انتخاب نشده است.", reply_markup=manage_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        count = catalog_image_count(context.user_data["warehouse"], target)
        if count == 0:
            await send_text(update, "هنوز تصویری ارسال نشده است.")
            return STATE_CATALOG_UPLOAD
        await send_text(update, "کاتالوگ ذخیره شد.", reply_markup=manage_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    await send_text(update, f"لطفا تصویر ارسال کنید یا «{CATALOG_DONE_TEXT}» را بزنید.")
    return STATE_CATALOG_UPLOAD


async def catalog_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.photo:
        return STATE_CATALOG_UPLOAD
    photo = update.message.photo[-1]
    try:
        file_obj = await photo.get_file()
        await save_catalog_file(update, context, file_obj, ".jpg")
    except (TimedOut, NetworkError):
        logging.exception("Telegram API request failed while downloading photo.")
        await send_text(update, "ارسال تصویر ناموفق بود. دوباره تلاش کنید.")
    except Exception:
        logging.exception("Failed to save catalog photo.")
        await send_text(update, "ذخیره تصویر انجام نشد.")
    return STATE_CATALOG_UPLOAD


async def catalog_receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.document:
        return STATE_CATALOG_UPLOAD
    document = update.message.document
    if not (document.mime_type or "").startswith("image/"):
        await send_text(update, "فقط تصویر ارسال کنید.")
        return STATE_CATALOG_UPLOAD
    ext = ""
    if document.file_name:
        ext = Path(document.file_name).suffix
    if not ext:
        ext = mimetypes.guess_extension(document.mime_type or "") or ".jpg"
    try:
        file_obj = await document.get_file()
        await save_catalog_file(update, context, file_obj, ext)
    except (TimedOut, NetworkError):
        logging.exception("Telegram API request failed while downloading document.")
        await send_text(update, "ارسال تصویر ناموفق بود. دوباره تلاش کنید.")
    except Exception:
        logging.exception("Failed to save catalog document.")
        await send_text(update, "ذخیره تصویر انجام نشد.")
    return STATE_CATALOG_UPLOAD


async def save_catalog_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_obj,
    extension: str,
) -> None:
    warehouse = context.user_data.get("warehouse")
    target = context.user_data.get("catalog_target")
    if not warehouse or not target:
        await send_text(update, "ابتدا طرح را انتخاب کنید.")
        return
    try:
        path = next_catalog_image_path(warehouse, target, extension)
    except ValueError:
        await send_text(update, f"حداکثر {MAX_CATALOG_IMAGES} تصویر مجاز است.")
        return
    await file_obj.download_to_drive(custom_path=str(path))
    count = catalog_image_count(warehouse, target)
    if count >= MAX_CATALOG_IMAGES:
        await send_text(update, "حداکثر تعداد تصویر ذخیره شد. برای پایان، دکمه اتمام را بزنید.")
        return
    await send_text(update, f"تصویر {count} ذخیره شد.")


async def catalogs_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_text(update, "لغو شد.", reply_markup=manage_menu_keyboard())
    context.user_data["skip_back_once"] = True
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


def build_catalog_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{CATALOG_MENU_TEXT}$"), catalogs_start)
        ],
        states={
            STATE_CATALOG_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalogs_menu)
            ],
            STATE_CATALOG_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalogs_select)
            ],
            STATE_CATALOG_OVERWRITE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_overwrite_confirm)
            ],
            STATE_CATALOG_DELETE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_delete_confirm)
            ],
            STATE_CATALOG_UPLOAD: [
                MessageHandler(filters.PHOTO, catalog_receive_photo),
                MessageHandler(filters.Document.ALL, catalog_receive_document),
                MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_upload_text),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), catalogs_cancel),
            CommandHandler("cancel", catalogs_cancel),
        ],
    )
