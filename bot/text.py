from telegram import Update

RLM = "\u200f"


def rtl(text: str) -> str:
    if text is None:
        return ""
    lines = str(text).splitlines() or [""]
    return "\n".join(f"{RLM}{line}" for line in lines)


async def send_text(update: Update | None, text: str, **kwargs) -> None:
    message = None
    if update is not None:
        message = update.message or update.effective_message
    if not message:
        return
    await message.reply_text(rtl(text), **kwargs)
