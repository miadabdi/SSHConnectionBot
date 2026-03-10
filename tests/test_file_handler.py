from types import SimpleNamespace

import pytest

from app.interfaces.telegram.handlers.files import FileHandler


class _FakeService:
    async def upload_local_file(
        self,
        user_id: int,
        local_path: str,
        remote_path: str | None = None,
    ) -> str:
        return remote_path or "/tmp/file.bin"


class _FakeMessage:
    def __init__(
        self,
        text: str | None = None,
        caption: str | None = None,
        reply_to_message=None,
        **media,
    ) -> None:
        self.text = text
        self.caption = caption
        self.reply_to_message = reply_to_message
        self.from_user = SimpleNamespace(id=1)
        self.chat = SimpleNamespace(id=10)
        self.answers: list[str] = []
        self.document = media.get("document")
        self.photo = media.get("photo")
        self.video = media.get("video")
        self.audio = media.get("audio")
        self.voice = media.get("voice")
        self.animation = media.get("animation")
        self.video_note = media.get("video_note")
        self.sticker = media.get("sticker")

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return self

    async def edit_text(self, text: str, **kwargs):
        self.answers.append(text)
        return self


@pytest.mark.asyncio
async def test_cmd_upload_requires_reply_message() -> None:
    handler = FileHandler(service=_FakeService())
    message = _FakeMessage(text="/upload /tmp/test.txt")

    await handler.cmd_upload(message=message, bot=SimpleNamespace())

    assert message.answers
    assert "Reply to a file/media message" in message.answers[-1]


@pytest.mark.asyncio
async def test_cmd_upload_requires_attachment_in_reply() -> None:
    handler = FileHandler(service=_FakeService())
    reply = _FakeMessage(text="hello")
    message = _FakeMessage(text="/upload /tmp/test.txt", reply_to_message=reply)

    await handler.cmd_upload(message=message, bot=SimpleNamespace())

    assert message.answers
    assert "no uploadable attachment" in message.answers[-1].lower()


def test_extract_upload_source_document_keeps_file_name() -> None:
    handler = FileHandler(service=_FakeService())
    document = SimpleNamespace(
        file_id="abc123",
        file_name="archive.tar.gz",
        file_size=2048,
    )
    message = _FakeMessage(document=document)

    source = handler._extract_upload_source(message)

    assert source is not None
    assert source.file_id == "abc123"
    assert source.file_name == "archive.tar.gz"
    assert source.file_size == 2048


def test_extract_upload_source_photo_generates_name(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = FileHandler(service=_FakeService())
    monkeypatch.setattr("app.interfaces.telegram.handlers.files.time.time", lambda: 1700000000)
    photo_small = SimpleNamespace(file_id="small_photo_id", file_size=100)
    photo_big = SimpleNamespace(file_id="big_photo_id", file_size=500)
    message = _FakeMessage(photo=[photo_small, photo_big])

    source = handler._extract_upload_source(message)

    assert source is not None
    assert source.file_id == "big_photo_id"
    assert source.file_name.startswith("upload_photo_1700000000_")
    assert source.file_name.endswith(".jpg")
