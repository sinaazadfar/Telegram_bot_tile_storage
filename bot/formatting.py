from html import escape as html_escape

from .utils import (
    format_unit,
    normalize_query,
    split_meter_pallet,
)

HR = "-" * 50

BASE_INFO_HEADERS = {
    normalize_query("کد محصول"),
    normalize_query("نام طرح"),
    normalize_query("سایز محصول"),
    normalize_query("مقدار تقسیم پالت"),
}


def row_label(row: dict) -> str:
    name = row.get("name_display", "")
    code = row.get("code_display", "")
    return f"{name} ({code})"


def build_label_map(rows: list[dict]) -> dict[str, list[dict]]:
    label_map: dict[str, list[dict]] = {}
    for row in rows:
        label = row_label(row)
        label_map.setdefault(label, []).append(row)
    return label_map


def build_buttons_from_labels(label_map: dict[str, list[dict]]) -> list[list[str]]:
    return [[label] for label in label_map.keys()]


def format_details(details: list[tuple[str, str]], use_html: bool = True) -> str:
    base_values: dict[str, tuple[str, str]] = {}
    groups: dict[str, list[tuple[str, str]]] = {
        "physical": [],
        "reserved": [],
        "sellable": [],
        "other": [],
    }
    label_display = {
        "physical": "موجودی",
        "reserved": "رزرو",
        "sellable": "قابل فروش",
        "other": "سایر",
    }
    label_suffix = {
        "physical": "فیزیکی",
        "reserved": "رزرو",
        "sellable": "قابل فروش",
        "other": None,
    }
    label_norms = {
        "physical": normalize_query("فیزیکی"),
        "reserved": normalize_query("رزرو"),
        "sellable": normalize_query("قابل فروش"),
    }
    for header, value in details:
        header_norm = normalize_query(header)
        if header_norm in BASE_INFO_HEADERS and header_norm not in base_values:
            base_values[header_norm] = (header, value)
            continue
        if label_norms["physical"] in header_norm:
            groups["physical"].append((header, value))
        elif label_norms["reserved"] in header_norm:
            groups["reserved"].append((header, value))
        elif label_norms["sellable"] in header_norm:
            groups["sellable"].append((header, value))
        else:
            groups["other"].append((header, value))

    name_value = base_values.get(normalize_query("نام طرح"), ("", ""))[1]
    code_value = base_values.get(normalize_query("کد محصول"), ("", ""))[1]
    size_value = base_values.get(normalize_query("سایز محصول"), ("", ""))[1]
    divisor_value = base_values.get(normalize_query("مقدار تقسیم پالت"), ("", ""))[1]

    def strip_label(text: str, suffix_label: str | None) -> str:
        if not suffix_label:
            return text
        suffix = f" ({suffix_label})"
        if text.endswith(suffix):
            return text[: -len(suffix)].strip()
        return text

    def esc(text: str) -> str:
        return html_escape(text) if use_html else text

    def bold(text: str) -> str:
        return f"<b>{esc(text)}</b>" if use_html else text

    def add_inline_value(lines: list[str], title: str, value: str) -> None:
        lines.append(f"{esc(title)}: {bold(value)}")
        lines.append("")

    empty_messages = {
        "physical": "اتمام موجودی",
        "reserved": "رزرو نشده",
        "sellable": "اتمام موجودی",
    }

    def fmt_group_items(
        pairs: list[tuple[str, str]],
        display_label: str,
        suffix_label: str | None,
        empty_message: str,
    ) -> str:
        lines: list[str] = [display_label, ""]
        if not pairs:
            lines.append(esc(empty_message))
            return "\n".join(lines)
        for header, value in pairs:
            header_text = strip_label(header, suffix_label)
            lines.append(f"{esc(header_text)}:")
            meter, pallet = split_meter_pallet(value)
            if meter:
                meter_text = format_unit(meter, "متر مربع")
                lines.append(bold(meter_text))
            if pallet:
                pallet_text = format_unit(pallet, "پالت")
                lines.append(bold(pallet_text))
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    sections: list[str] = []
    header_line = "جزئیات طرح"
    if name_value:
        header_line = f"{header_line} {bold(name_value)}"
    sections.append(header_line)
    sections.append("")
    if code_value:
        add_inline_value(sections, "کد", code_value)
    if size_value:
        add_inline_value(sections, "سایز", size_value)
    if divisor_value:
        add_inline_value(sections, "مقدار تقسیم پالت", divisor_value)
    sections.append(HR)

    for key in ["physical", "reserved", "sellable"]:
        sections.append(
            fmt_group_items(
                groups[key],
                label_display[key],
                label_suffix[key],
                empty_messages[key],
            )
        )
        sections.append(HR)

    if groups["other"]:
        sections.append(
            fmt_group_items(groups["other"], label_display["other"], label_suffix["other"])
        )
        sections.append(HR)

    if sections and sections[-1] == HR:
        sections.pop()
    return "\n".join(sections).strip()
