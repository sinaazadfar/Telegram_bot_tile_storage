import os
import re
from pathlib import Path

from arabic_reshaper import reshape
from bidi.algorithm import get_display
from fpdf import FPDF

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _shape_line(line: str) -> str:
    if not line.strip():
        return ""
    if not _ARABIC_RE.search(line):
        return line
    return get_display(reshape(line))


def _shape_text(text: str) -> str:
    return "\n".join(_shape_line(line) for line in text.splitlines())


def _find_font_path() -> Path | None:
    env_font = os.environ.get("BOT_PDF_FONT_PATH", "").strip()
    if env_font:
        env_path = Path(env_font)
        if env_path.exists():
            return env_path
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [
        windows_dir / "Fonts" / "Tahoma.ttf",
        windows_dir / "Fonts" / "tahoma.ttf",
        windows_dir / "Fonts" / "segoeui.ttf",
        windows_dir / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def render_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    font_path = _find_font_path()
    if font_path:
        pdf.add_font("custom", "", str(font_path), uni=True)
        pdf.set_font("custom", size=12)
    else:
        pdf.set_font("Helvetica", size=12)
    shaped = _shape_text(text)
    pdf.multi_cell(0, 7, shaped, align="R")
    output = pdf.output(dest="S")
    if isinstance(output, (bytes, bytearray)):
        return bytes(output)
    return output.encode("latin-1")
