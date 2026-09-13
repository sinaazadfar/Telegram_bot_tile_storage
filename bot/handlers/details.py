import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from telegram import InputFile, InputMediaPhoto, Update
from telegram.ext import ConversationHandler, ContextTypes, MessageHandler, filters

from ..config import ensure_warehouse_template_path, warehouse_output_path
from ..formatting import build_buttons_from_labels, build_label_map, format_details
from ..keyboards import keyboard_with_back, main_keyboard, warehouse_menu_keyboard
from ..catalogs import list_catalog_images
from ..pdf_utils import render_pdf
from ..storage import (
    find_template_matches_any,
    get_output_row_details,
    list_template_rows,
)
from ..strings import (
    AVAILABLE_CATALOGS_TEXT,
    BACK_TEXT,
    DETAILS_TEXT,
    DETAILS_ALL_TEXT,
    DETAILS_ALL_PDF_OUTPUT,
    DETAILS_ALL_TEXT_OUTPUT,
    DETAILS_CONTINUE_TEXT_OUTPUT,
    CATALOG_GET_TEXT,
    DETAILS_FILTERED_TEXT,
    DETAILS_FILTERED_PDF_OUTPUT,
    DETAILS_FILTERED_TEXT_OUTPUT,
    WAREHOUSE_LABELS,
)
from ..text import send_text
from ..utils import (
    clean_text,
    format_jalali_date,
    normalize_digits,
    normalize_query,
    split_meter_pallet,
)
from ..auth import is_admin

STATE_DETAILS_LIST = 0
STATE_DETAILS_ACTION = 1
SELLABLE_TOTAL_HEADER = normalize_query("مجموع طرح (قابل فروش)")
TELEGRAM_TEXT_LIMIT = 3500
TEXT_REPORT_BATCH_SIZE = 10


def chunk_report_sections(sections: list[str]) -> list[str]:
    separator = "\n\n" + ("=" * 50) + "\n\n"
    chunks: list[str] = []
    current = ""
    for section in sections:
        parts = [
            section[index : index + TELEGRAM_TEXT_LIMIT]
            for index in range(0, len(section), TELEGRAM_TEXT_LIMIT)
        ] or [""]
        for part in parts:
            candidate = f"{current}{separator if current else ''}{part}"
            if current and len(candidate) > TELEGRAM_TEXT_LIMIT:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def details_menu_buttons(
    output_button: str, label_map: dict[str, list[dict]]
) -> list[list[str]]:
    if output_button == DETAILS_ALL_TEXT:
        output_buttons = [DETAILS_ALL_TEXT_OUTPUT, DETAILS_ALL_PDF_OUTPUT]
    else:
        output_buttons = [DETAILS_FILTERED_TEXT_OUTPUT, DETAILS_FILTERED_PDF_OUTPUT]
    return [
        output_buttons,
        [AVAILABLE_CATALOGS_TEXT],
        *build_buttons_from_labels(label_map),
    ]


async def send_next_text_report_batch(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    chunks = context.user_data.get("details_text_chunks") or []
    start = context.user_data.get("details_text_chunk_index", 0)
    end = min(start + TEXT_REPORT_BATCH_SIZE, len(chunks))
    try:
        for index in range(start, end):
            await send_text(update, chunks[index])
            context.user_data["details_text_chunk_index"] = index + 1
            if index + 1 < end:
                await asyncio.sleep(0.25)
    except Exception:
        logging.exception("Failed to send details text report batch.")
        await send_text(
            update,
            "ارسال این بخش کامل نشد. برای ادامه دوباره دکمه زیر را بزنید.",
            reply_markup=keyboard_with_back([[DETAILS_CONTINUE_TEXT_OUTPUT]]),
        )
        return STATE_DETAILS_LIST

    if end < len(chunks):
        await send_text(
            update,
            f"{end} از {len(chunks)} پیام ارسال شد. برای دریافت ادامه، دکمه زیر را بزنید.",
            reply_markup=keyboard_with_back([[DETAILS_CONTINUE_TEXT_OUTPUT]]),
        )
        return STATE_DETAILS_LIST

    context.user_data.pop("details_text_chunks", None)
    context.user_data.pop("details_text_chunk_index", None)
    output_button = context.user_data.pop(
        "details_text_return_button", DETAILS_ALL_TEXT
    )
    buttons = details_menu_buttons(
        output_button, context.user_data.get("details_label_map", {})
    )
    await send_text(
        update,
        "خروجی متنی کامل شد.",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DETAILS_LIST


def sellable_total(
    details: list[tuple[str, str]], *, positive_only: bool = False
) -> tuple[str, str | None] | None:
    for header, value in details:
        if normalize_query(header) != SELLABLE_TOTAL_HEADER:
            continue
        meter, pallet = split_meter_pallet(value)
        if not meter:
            return None
        try:
            amount = Decimal(normalize_digits(meter).replace(",", ""))
        except InvalidOperation:
            return None
        if amount < 0 or (positive_only and amount == 0):
            return None
        return meter, pallet
    return None


def catalog_caption(target: dict, total: tuple[str, str | None] | None = None) -> str:
    name = clean_text(target.get("name_display", ""))
    code = clean_text(target.get("code_display", ""))
    caption_parts: list[str] = []
    if name:
        caption_parts.append(f"نام طرح: {name}")
    if code:
        caption_parts.append(f"کد طرح: {code}")
    if total:
        meter, pallet = total
        amount = f"{meter} متر مربع"
        if pallet:
            amount += f" ({pallet} پالت)"
        caption_parts.append(f"موجودی قابل فروش: {amount}")
    return "\n".join(caption_parts)


async def send_details_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rows: list[dict],
    output_path,
    status_message: str | None,
    output_format: str,
) -> int:
    sections: list[str] = []
    details_by_row: list[list[tuple[str, str]]] = []
    try:
        for row in rows:
            details = get_output_row_details(row, output_path)
            if details:
                details_by_row.append(details)
    except Exception:
        logging.exception("Failed to read details report data.")
        await send_text(
            update,
            "خواندن اطلاعات خروجی انجام نشد.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END

    if not details_by_row:
        await send_text(
            update,
            "جزئیاتی پیدا نشد.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END

    if status_message:
        await send_text(update, status_message)

    if output_format == "text":
        text_sections = [
            format_details(details, use_html=False) for details in details_by_row
        ]
        context.user_data["details_text_chunks"] = chunk_report_sections(text_sections)
        context.user_data["details_text_chunk_index"] = 0
        return await send_next_text_report_batch(update, context)

    for details in details_by_row:
        sections.append(format_details(details, use_html=False))
        sections.append("\n\n" + ("=" * 50) + "\n\n")
    content = "".join(sections).strip()
    try:
        pdf_bytes = render_pdf(content)
    except Exception:
        logging.exception("Failed to build details PDF.")
        await send_text(
            update,
            "ساخت فایل PDF انجام نشد.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    buffer = BytesIO(pdf_bytes)
    buffer.seek(0)
    warehouse_key = context.user_data.get("warehouse", "warehouse")
    warehouse_label = WAREHOUSE_LABELS.get(warehouse_key, warehouse_key).replace(
        " ", "_"
    )
    date_stamp = format_jalali_date(datetime.now().date())
    filename = f"{warehouse_label}_{date_stamp}.pdf"
    message = update.message or update.effective_message
    try:
        await message.reply_document(document=InputFile(buffer, filename=filename))
    except Exception:
        logging.exception("Failed to send details PDF.")
        await send_text(
            update,
            "ارسال فایل PDF انجام نشد. لطفاً دوباره تلاش کنید.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    return STATE_DETAILS_LIST


async def send_catalog_images(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target: dict,
    total: tuple[str, str | None] | None = None,
    notify_missing: bool = True,
) -> bool:
    warehouse_key = context.user_data.get("warehouse")
    if not warehouse_key:
        await send_text(
            update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard()
        )
        return False
    images = list_catalog_images(warehouse_key, target)
    if not images:
        if notify_missing:
            await send_text(
                update,
                "کاتالوگی برای این طرح پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
        return False
    if not update.message:
        return False
    caption = catalog_caption(target, total)
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


async def send_available_catalogs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rows: list[dict],
    output_path,
) -> int:
    eligible_count = 0
    sent_count = 0
    missing_count = 0
    for row in rows:
        details = get_output_row_details(row, output_path)
        total = sellable_total(details, positive_only=True)
        if total is None:
            continue
        eligible_count += 1
        sent = await send_catalog_images(
            update,
            context,
            row,
            total=total,
            notify_missing=False,
        )
        if sent:
            sent_count += 1
        else:
            missing_count += 1

    if sent_count == 0:
        if eligible_count == 0:
            message = "طرحی با موجودی قابل فروش پیدا نشد."
        else:
            message = "برای طرح‌های دارای موجودی قابل فروش، کاتالوگی پیدا نشد."
    else:
        message = f"کاتالوگ {sent_count} طرح ارسال شد."
        if missing_count:
            message += f"\nبرای {missing_count} طرح موجود، کاتالوگی ثبت نشده است."
    await send_text(
        update,
        message,
        reply_markup=warehouse_menu_keyboard(is_admin(update)),
    )
    context.user_data["conversation_active"] = False
    return ConversationHandler.END


async def details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(
            update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard()
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    context.user_data["menu_level"] = "warehouse"
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(
            update,
            "?????? ???? ???.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
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
    context.user_data["details_catalog_rows"] = matches
    buttons = details_menu_buttons(
        DETAILS_ALL_TEXT, context.user_data["details_label_map"]
    )
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
        await send_text(
            update,
            "?????? ???? ???.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == BACK_TEXT:
        context.user_data.pop("details_text_chunks", None)
        context.user_data.pop("details_text_chunk_index", None)
        context.user_data.pop("details_text_return_button", None)
        await send_text(
            update,
            "به منوی انبار برگشتید.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["skip_back_once"] = True
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text in {DETAILS_ALL_TEXT_OUTPUT, DETAILS_ALL_PDF_OUTPUT}:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        if not output_path.exists():
            await send_text(
                update,
                "فایل خروجی پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        try:
            rows = list_template_rows(template_path)
        except Exception:
            logging.exception("Failed to list template rows.")
            await send_text(update, "لیست طرح‌ها قابل دریافت نیست.")
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        output_format = "text" if text == DETAILS_ALL_TEXT_OUTPUT else "pdf"
        if output_format == "text":
            context.user_data["details_text_return_button"] = DETAILS_ALL_TEXT
        return await send_details_report(
            update, context, rows, output_path, None, output_format
        )
    if text in {DETAILS_FILTERED_TEXT_OUTPUT, DETAILS_FILTERED_PDF_OUTPUT}:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        filtered_rows = context.user_data.get("details_filtered_rows") or []
        if not filtered_rows:
            await send_text(
                update,
                "ابتدا جستجو کنید.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        if not output_path.exists():
            await send_text(
                update,
                "فایل خروجی پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        output_format = "text" if text == DETAILS_FILTERED_TEXT_OUTPUT else "pdf"
        if output_format == "text":
            context.user_data["details_text_return_button"] = DETAILS_FILTERED_TEXT
        return await send_details_report(
            update, context, filtered_rows, output_path, None, output_format
        )
    if text == DETAILS_CONTINUE_TEXT_OUTPUT:
        if not context.user_data.get("details_text_chunks"):
            await send_text(update, "خروجی متنی در حال انتظاری وجود ندارد.")
            return STATE_DETAILS_LIST
        return await send_next_text_report_batch(update, context)
    if text == AVAILABLE_CATALOGS_TEXT:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        if not output_path.exists():
            await send_text(
                update,
                "فایل خروجی پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        try:
            rows = context.user_data.get("details_catalog_rows") or []
            return await send_available_catalogs(update, context, rows, output_path)
        except Exception:
            logging.exception("Failed to send available catalogs.")
            await send_text(
                update,
                "ارسال کاتالوگ‌ها انجام نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
    label_map = context.user_data.get("details_label_map", {})
    if text in label_map:
        rows = label_map[text]
        if len(rows) > 1:
            await send_text(
                update, "چند مورد با این عنوان وجود دارد. متن دقیق‌تری وارد کنید."
            )
            return STATE_DETAILS_LIST
        target = rows[0]
        output_path = warehouse_output_path(context.user_data["warehouse"])
        try:
            details = get_output_row_details(target, output_path)
        except FileNotFoundError:
            await send_text(
                update,
                "فایل خروجی پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        except Exception:
            logging.exception("Failed to read output file.")
            await send_text(
                update,
                "خواندن جزئیات ممکن نیست.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
            context.user_data["conversation_active"] = False
            return ConversationHandler.END
        if not details:
            await send_text(
                update,
                "جزئیاتی پیدا نشد.",
                reply_markup=warehouse_menu_keyboard(is_admin(update)),
            )
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
    context.user_data["details_catalog_rows"] = matches
    buttons = details_menu_buttons(DETAILS_FILTERED_TEXT, label_map)
    await send_text(
        update,
        "نتیجه جستجو. یکی را انتخاب کنید:",
        reply_markup=keyboard_with_back(buttons),
    )
    return STATE_DETAILS_LIST


async def details_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        await send_text(
            update,
            "به منوی انبار برگشتید.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
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
        await send_text(
            update,
            "طرحی انتخاب نشده است.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    output_path = warehouse_output_path(context.user_data["warehouse"])
    try:
        total = sellable_total(get_output_row_details(target, output_path))
    except FileNotFoundError:
        total = None
    sent = await send_catalog_images(update, context, target, total=total)
    if sent:
        await send_text(
            update,
            "کاتالوگ ارسال شد.",
            reply_markup=warehouse_menu_keyboard(is_admin(update)),
        )
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
