from telegram import ReplyKeyboardMarkup

from .strings import (
    ADD_ROW_TEXT,
    BACK_TEXT,
    DELETE_ROW_TEXT,
    DETAILS_TEXT,
    EDIT_ROW_TEXT,
    MANAGE_ROWS_TEXT,
    PRODUCTS_DOWNLOAD_TEXT,
    PRODUCTS_MENU_TEXT,
    PRODUCTS_UPLOAD_TEXT,
    WAREHOUSE_DARIN_TEXT,
    WAREHOUSE_FAKHAR_TEXT,
)


def keyboard_with_back(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BACK_TEXT], *rows], resize_keyboard=True)


def manage_rows_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BACK_TEXT], [ADD_ROW_TEXT], [EDIT_ROW_TEXT], [DELETE_ROW_TEXT]],
        resize_keyboard=True,
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[WAREHOUSE_FAKHAR_TEXT], [WAREHOUSE_DARIN_TEXT]],
        resize_keyboard=True,
    )


def warehouse_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BACK_TEXT], [MANAGE_ROWS_TEXT], [DETAILS_TEXT], [PRODUCTS_MENU_TEXT]],
        resize_keyboard=True,
    )


def products_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BACK_TEXT], [PRODUCTS_UPLOAD_TEXT], [PRODUCTS_DOWNLOAD_TEXT]],
        resize_keyboard=True,
    )
