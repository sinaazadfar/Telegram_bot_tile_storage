from telegram import ReplyKeyboardMarkup

from .strings import (
    ADD_ROW_TEXT,
    BACK_TEXT,
    CATALOG_CREATE_TEXT,
    CATALOG_DELETE_TEXT,
    CATALOG_EDIT_TEXT,
    CATALOG_MENU_TEXT,
    DELETE_ROW_TEXT,
    DETAILS_TEXT,
    EDIT_ROW_TEXT,
    MANAGE_MENU_TEXT,
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
        [
            [ADD_ROW_TEXT], 
            [EDIT_ROW_TEXT], 
            [DELETE_ROW_TEXT],
            [BACK_TEXT], 
            ],
        resize_keyboard=True,
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[WAREHOUSE_FAKHAR_TEXT], [WAREHOUSE_DARIN_TEXT]],
        resize_keyboard=True,
    )


def warehouse_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [MANAGE_MENU_TEXT, DETAILS_TEXT],
            [BACK_TEXT],
        ],
        resize_keyboard=True,
    )


def manage_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [MANAGE_ROWS_TEXT, CATALOG_MENU_TEXT],
            [PRODUCTS_MENU_TEXT],
            [BACK_TEXT],
        ],
        resize_keyboard=True,
    )


def products_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [PRODUCTS_UPLOAD_TEXT], 
            [PRODUCTS_DOWNLOAD_TEXT],
            [BACK_TEXT], 
        ],
        resize_keyboard=True,
    )


def catalog_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [CATALOG_CREATE_TEXT], 
            [CATALOG_EDIT_TEXT], 
            [CATALOG_DELETE_TEXT],
            [BACK_TEXT], 
        ],
        resize_keyboard=True,
    )
