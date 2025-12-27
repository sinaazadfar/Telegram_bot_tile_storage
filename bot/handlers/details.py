import logging
from datetime import datetime
from io import BytesIO

from telegram import InputFile, InputMediaPhoto, Update
from telegram.ext import ConversationHandler, ContextTypes, MessageHandler, filters

from ..config import ensure_warehouse_template_path, warehouse_output_path
from ..formatting import build_buttons_from_labels, build_label_map, format_details
from ..keyboards import keyboard_with_back, main_keyboard, warehouse_menu_keyboard
from ..catalogs import list_catalog_images
from ..pdf_utils import render_pdf
from ..storage import find_template_matches_any, get_output_row_details, list_template_rows
from ..strings import (
    BACK_TEXT,
    DETAILS_TEXT,
    DETAILS_ALL_TEXT,
    CATALOG_GET_TEXT,
    DETAILS_FILTERED_TEXT,
    WAREHOUSE_LABELS,
)
from ..text import send_text
from ..utils import clean_text, format_jalali_date

STATE_DETAILS_LIST = 0
STATE_DETAILS_ACTION = 1


async def send_details_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rows: list[dict],
    output_path,
    status_message: str | None,
) -> int:
    sections: list[str] = []
    sent_any = False
    for row in rows:
        details = get_output_row_details(row, output_path)
        if not details:
            continue
        await send_text(
            update,
            format_details(details),
            parse_mode="HTML",
        )
        sent_any = True
        sections.append(format_details(details, use_html=False))
        sections.append("\n\n" + ("=" * 50) + "\n\n")
    if not sent_any:
        await send_text(update, "جزئیاتی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    content = "".join(sections).strip()
    if status_message:
        await send_text(update, status_message)
    try:
        pdf_bytes = render_pdf(content)
    except Exception:
        logging.exception("Failed to build details PDF.")
        await send_text(update, "ساخت فایل PDF انجام نشد.", reply_markup=warehouse_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    buffer = BytesIO(pdf_bytes)
    buffer.seek(0)
    warehouse_key = context.user_data.get("warehouse", "warehouse")
    warehouse_label = WAREHOUSE_LABELS.get(warehouse_key, warehouse_key).replace(" ", "_")
    date_stamp = format_jalali_date(datetime.now().date())
    filename = f"{warehouse_label}_{date_stamp}.pdf"
    await update.message.reply_document(
        document=InputFile(buffer, filename=filename)
    )
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def send_catalog_images(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target: dict
) -> bool:
    warehouse_key = context.user_data.get("warehouse")
    if not warehouse_key:
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        return False
    images = list_catalog_images(warehouse_key, target)
    if not images:
        await send_text(update, "کاتالوگی برای این طرح پیدا نشد.", reply_markup=warehouse_menu_keyboard())
        return False
    if not update.message:
        return False
    name = clean_text(target.get("name_display", ""))
    code = clean_text(target.get("code_display", ""))
    caption_parts: list[str] = []
    if name:
        caption_parts.append(f"نام طرح: {name}")
    if code:
        caption_parts.append(f"کد طرح: {code}")
    caption = "\n".join(caption_parts)
    if len(images) == 1:
        with images[0].open("rb") as handle:
            if caption:
                await update.message.reply_photo(photo=handle, caption=caption)
            else:
                await update.message.reply_photo(photo=handle)
        return True
    handles = [path.open("rb") for path in images]
    media: list[InputMediaPhoto] = []
    for index, handle in enumerate(handles):
        if index == 0 and caption:
            media.append(InputMediaPhoto(handle, caption=caption))
        else:
            media.append(InputMediaPhoto(handle))
    try:
        await update.message.reply_media_group(media=media)
    finally:
        for handle in handles:
            handle.close()
    return True


async def details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    context.user_data["menu_level"] = "warehouse"
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.", reply_markup=warehouse_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    try:
        matches = list_template_rows(template_path)
    except Exception:
        logging.exception("Failed to list template rows.")
        await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["details_label_map"] = build_label_map(matches)
    buttons = [
        [DETAILS_ALL_TEXT],
        *build_buttons_from_labels(context.user_data["details_label_map"]),
    ]
    await send_text(
        update,
        "لیست طرح‌ها. برای جستجو متن وارد کنید یا یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DETAILS_LIST


async def details_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "?????? ???? ???.", reply_markup=warehouse_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == BACK_TEXT:
        await send_text(update, "به منوی انبار برگشتید.", reply_markup=warehouse_menu_keyboard())
        context.user_data["skip_back_once"] = True
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == DETAILS_ALL_TEXT:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        if not output_path.exists():
            await send_text(update, "فایل خروجی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        try:
            rows = list_template_rows(template_path)
        except Exception:
            logging.exception("Failed to list template rows.")
            await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        return await send_details_report(update, context, rows, output_path, None)
    if text == DETAILS_FILTERED_TEXT:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        filtered_rows = context.user_data.get("details_filtered_rows") or []
        if not filtered_rows:
            await send_text(update, "ابتدا جستجو کنید.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        if not output_path.exists():
            await send_text(update, "فایل خروجی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        return await send_details_report(update, context, filtered_rows, output_path, None)
    if text == DETAILS_ALL_TEXT:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        if not output_path.exists():
            await send_text(update, "فایل خروجی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        try:
            rows = list_template_rows(template_path)
        except Exception:
            logging.exception("Failed to list template rows.")
            await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        sections: list[str] = []
        sent_any = False
        for row in rows:
            details = get_output_row_details(row, output_path)
            if not details:
                continue
            await send_text(
                update,
                format_details(details),
                parse_mode="HTML",
            )
            sent_any = True
            sections.append(format_details(details, use_html=False))
            sections.append("\n\n" + ("=" * 50) + "\n\n")
        if not sent_any:
            await send_text(update, "جزئیاتی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        content = "".join(sections).strip()
        await send_text(update, "در حال ساخت فایل تمام طرح‌ها در یک PDF...")
        try:
            pdf_bytes = render_pdf(content)
        except Exception:
            logging.exception("Failed to build details PDF.")
            await send_text(update, "ساخت فایل PDF انجام نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        buffer = BytesIO(pdf_bytes)
        buffer.seek(0)
        warehouse_key = context.user_data.get("warehouse", "warehouse")
        date_stamp = datetime.now().strftime("%Y%m%d")
        filename = f"details_{warehouse_key}_{date_stamp}.pdf"
        await update.message.reply_document(
            document=InputFile(buffer, filename=filename)
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    label_map = context.user_data.get("details_label_map", {})
    if text in label_map:
        rows = label_map[text]
        if len(rows) > 1:
            await send_text(update, "چند مورد با این عنوان وجود دارد. متن دقیق‌تری وارد کنید.")
            return STATE_DETAILS_LIST
        target = rows[0]
        output_path = warehouse_output_path(context.user_data["warehouse"])
        try:
            details = get_output_row_details(target, output_path)
        except FileNotFoundError:
            await send_text(update, "فایل خروجی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        except Exception:
            logging.exception("Failed to read output file.")
            await send_text(update, "خواندن جزئیات ممکن نیست.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        if not details:
            await send_text(update, "جزئیاتی پیدا نشد.", reply_markup=warehouse_menu_keyboard())
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        await send_text(
            update,
            format_details(details),
            parse_mode="HTML",
        )
        context.user_data["details_selected_row"] = target
        await send_text(
            update,
            "برای دریافت کاتالوگ، دکمه زیر را بزنید.",
            reply_markup=keyboard_with_back([[CATALOG_GET_TEXT]]),
        )
        return STATE_DETAILS_ACTION
    try:
        matches = find_template_matches_any(text, template_path)
    except Exception:
        logging.exception("Failed to search template rows.")
        await send_text(update, "جستجو انجام نشد. دوباره تلاش کنید.")
        return STATE_DETAILS_LIST
    if not matches:
        await send_text(update, "موردی پیدا نشد.")
        return STATE_DETAILS_LIST
    label_map = build_label_map(matches)
    context.user_data["details_label_map"] = label_map
    context.user_data["details_filtered_rows"] = matches
    buttons = [
        [DETAILS_FILTERED_TEXT],
        *build_buttons_from_labels(label_map),
    ]
    await send_text(
        update,
        "نتیجه جستجو. یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DETAILS_LIST


async def details_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        await send_text(update, "به منوی انبار برگشتید.", reply_markup=warehouse_menu_keyboard())
        context.user_data["skip_back_once"] = True
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text != CATALOG_GET_TEXT:
        await send_text(
            update,
            "یکی از گزینه‌ها را انتخاب کنید.",
            reply_markup=keyboard_with_back([[CATALOG_GET_TEXT]]),
        )
        return STATE_DETAILS_ACTION
    target = context.user_data.get("details_selected_row")
    if not target:
        await send_text(update, "طرحی انتخاب نشده است.", reply_markup=warehouse_menu_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    sent = await send_catalog_images(update, context, target)
    if sent:
        await send_text(update, "کاتالوگ ارسال شد.", reply_markup=warehouse_menu_keyboard())
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


def build_details_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{DETAILS_TEXT}$"), details_start)
        ],
        states={
            STATE_DETAILS_LIST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, details_list)
            ],
            STATE_DETAILS_ACTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, details_action)
            ],
        },
        fallbacks=[],
    )
