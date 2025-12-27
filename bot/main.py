import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from .config import (
    BOT_TOKEN,
    CONNECT_TIMEOUT,
    POOL_TIMEOUT,
    PROXY_URL,
    READ_TIMEOUT,
    REQUEST_POOL_SIZE,
    UPDATES_POOL_SIZE,
    WRITE_TIMEOUT,
)
from .handlers.catalogs import build_catalog_handler
from .handlers.details import build_details_handler
from .handlers.documents import handle_document
from .handlers.menu import (
    back_to_menu,
    error_handler,
    help_command,
    manage_menu,
    manage_rows,
    select_warehouse,
    start,
)
from .handlers.products import build_products_handler
from .handlers.rows import (
    build_add_row_handler,
    build_delete_row_handler,
    build_edit_row_handler,
)
from .strings import (
    MANAGE_MENU_TEXT,
    MANAGE_ROWS_TEXT,
    WAREHOUSE_DARIN_TEXT,
    WAREHOUSE_FAKHAR_TEXT,
    BACK_TEXT,
)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required.")
    logging.basicConfig(level=logging.INFO)
    request = HTTPXRequest(
        connection_pool_size=REQUEST_POOL_SIZE,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT,
        pool_timeout=POOL_TIMEOUT,
        proxy=PROXY_URL or None,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=UPDATES_POOL_SIZE,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT,
        pool_timeout=POOL_TIMEOUT,
        proxy=PROXY_URL or None,
    )
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(filters.Regex(f"^{WAREHOUSE_FAKHAR_TEXT}$"), select_warehouse)
    )
    app.add_handler(
        MessageHandler(filters.Regex(f"^{WAREHOUSE_DARIN_TEXT}$"), select_warehouse)
    )
    app.add_handler(
        MessageHandler(filters.Regex(f"^{MANAGE_MENU_TEXT}$"), manage_menu)
    )
    app.add_handler(MessageHandler(filters.Regex(f"^{MANAGE_ROWS_TEXT}$"), manage_rows))
    app.add_handler(build_add_row_handler())
    app.add_handler(build_edit_row_handler())
    app.add_handler(build_delete_row_handler())
    app.add_handler(build_details_handler())
    app.add_handler(build_catalog_handler())
    app.add_handler(build_products_handler())
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), back_to_menu), group=1)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
