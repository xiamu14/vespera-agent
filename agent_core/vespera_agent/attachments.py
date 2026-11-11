from pathlib import Path
import stat

from pydantic_ai import BinaryContent


MAX_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 4


def media_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    raise ValueError("附件须为 PNG、JPEG、GIF、WebP 或 PDF 文件")


def load_attachments(paths):
    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError("每条消息最多添加 4 个附件")
    remaining = MAX_BYTES
    attachments = []
    for value in paths:
        path = Path(value).resolve(strict=True)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("附件必须是普通文件")
        with path.open("rb") as stream:
            data = stream.read(remaining + 1)
        remaining -= len(data)
        if remaining < 0:
            raise ValueError("附件总大小不能超过 10 MiB")
        attachments.append(BinaryContent(data=data, media_type=media_type(data)))
    return attachments
