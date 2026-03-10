import os
import shutil
import tempfile
import time
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from app.application.services import FileTransferService
from app.domain.errors import SessionUnavailableError, ValidationError
from app.utils.formatting import Formatter

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


@dataclass(slots=True)
class UploadSource:
    file_id: str
    file_name: str
    file_size: int


class FileHandler:
    def __init__(self, service: FileTransferService) -> None:
        self.service = service
        self.router = Router(name="files")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_download, Command("download"))
        self.router.message.register(self.cmd_upload, Command("upload"))
        self.router.message.register(
            self.handle_caption_upload,
            lambda m: bool(m.caption and m.caption.strip().startswith("/upload")),
        )

    async def cmd_download(self, message: Message, bot: Bot) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/download &lt;remote_path&gt;</code>")
            return

        remote_path = args[1].strip()
        status = await message.answer(f"📥 Downloading <code>{Formatter.escape_html(remote_path)}</code>...")

        try:
            temp_dir, local_path = await self.service.download_to_temp_file(
                user_id=message.from_user.id,
                remote_path=remote_path,
            )
        except SessionUnavailableError:
            await status.edit_text("ℹ️ No active SSH session. Use /connect first.")
            return
        except Exception as exc:
            await status.edit_text(f"❌ Download failed: {Formatter.escape_html(str(exc))}")
            return

        try:
            file_size = os.path.getsize(local_path)
            if file_size > MAX_TELEGRAM_FILE_SIZE:
                await status.edit_text(
                    f"❌ File too large ({file_size // (1024 * 1024)}MB). Telegram limit is 50MB."
                )
                return

            input_file = FSInputFile(local_path, filename=os.path.basename(local_path))
            await bot.send_document(
                chat_id=message.chat.id,
                document=input_file,
                caption=(
                    f"📄 <code>{Formatter.escape_html(remote_path)}</code>\n"
                    f"📊 Size: {file_size:,} bytes"
                ),
            )
            await status.delete()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def cmd_upload(self, message: Message, bot: Bot) -> None:
        args = (message.text or "").strip().split(maxsplit=1)
        remote_path = args[1].strip() if len(args) > 1 else None

        reply_message = message.reply_to_message
        if not reply_message:
            await message.answer(
                "❌ Reply to a file/media message with <code>/upload [remote_path]</code>."
            )
            return

        source = self._extract_upload_source(reply_message)
        if not source:
            await message.answer("❌ Replied message has no uploadable attachment.")
            return

        await self._perform_upload(
            message=message,
            bot=bot,
            source=source,
            remote_path=remote_path,
        )

    async def handle_caption_upload(self, message: Message, bot: Bot) -> None:
        if not message.caption:
            return

        source = self._extract_upload_source(message)
        if not source:
            return

        parts = message.caption.strip().split(maxsplit=1)
        remote_path = parts[1].strip() if len(parts) > 1 else None
        await self._perform_upload(
            message=message,
            bot=bot,
            source=source,
            remote_path=remote_path,
        )

    async def _perform_upload(
        self,
        message: Message,
        bot: Bot,
        source: UploadSource,
        remote_path: str | None,
    ) -> None:
        if remote_path:
            status = await message.answer(f"📤 Uploading to <code>{Formatter.escape_html(remote_path)}</code>...")
        else:
            status = await message.answer("📤 Uploading to active shell directory...")

        temp_dir = tempfile.mkdtemp(prefix="sshbot-ul-")
        local_path = os.path.join(temp_dir, source.file_name)

        try:
            file_info = await bot.get_file(source.file_id)
            await bot.download_file(file_info.file_path, local_path)
            uploaded_path = await self.service.upload_local_file(
                user_id=message.from_user.id,
                local_path=local_path,
                remote_path=remote_path,
            )
        except SessionUnavailableError:
            await status.edit_text("ℹ️ No active SSH session. Use /connect first.")
            return
        except ValidationError:
            await status.edit_text(
                "❌ Missing remote path and no active interactive shell.\n"
                "Use <code>/shell</code> first or provide <code>/upload &lt;remote_path&gt;</code>."
            )
            return
        except Exception as exc:
            await status.edit_text(f"❌ Upload failed: {Formatter.escape_html(str(exc))}")
            return
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        await status.edit_text(
            f"✅ Uploaded to <code>{Formatter.escape_html(uploaded_path)}</code>\n"
            f"📊 Size: {source.file_size:,} bytes"
        )

    def _extract_upload_source(self, message: Message) -> UploadSource | None:
        if message.document:
            file_name = message.document.file_name or self._generated_file_name(
                prefix="document",
                file_id=message.document.file_id,
                extension="bin",
            )
            return UploadSource(
                file_id=message.document.file_id,
                file_name=self._safe_file_name(file_name),
                file_size=message.document.file_size or 0,
            )

        if message.photo:
            photo = max(message.photo, key=lambda item: item.file_size or 0)
            return UploadSource(
                file_id=photo.file_id,
                file_name=self._generated_file_name(
                    prefix="photo",
                    file_id=photo.file_id,
                    extension="jpg",
                ),
                file_size=photo.file_size or 0,
            )

        if message.video:
            file_name = message.video.file_name or self._generated_file_name(
                prefix="video",
                file_id=message.video.file_id,
                extension="mp4",
            )
            return UploadSource(
                file_id=message.video.file_id,
                file_name=self._safe_file_name(file_name),
                file_size=message.video.file_size or 0,
            )

        if message.audio:
            file_name = message.audio.file_name or self._generated_file_name(
                prefix="audio",
                file_id=message.audio.file_id,
                extension="mp3",
            )
            return UploadSource(
                file_id=message.audio.file_id,
                file_name=self._safe_file_name(file_name),
                file_size=message.audio.file_size or 0,
            )

        if message.voice:
            return UploadSource(
                file_id=message.voice.file_id,
                file_name=self._generated_file_name(
                    prefix="voice",
                    file_id=message.voice.file_id,
                    extension="ogg",
                ),
                file_size=message.voice.file_size or 0,
            )

        if message.animation:
            file_name = message.animation.file_name or self._generated_file_name(
                prefix="animation",
                file_id=message.animation.file_id,
                extension="mp4",
            )
            return UploadSource(
                file_id=message.animation.file_id,
                file_name=self._safe_file_name(file_name),
                file_size=message.animation.file_size or 0,
            )

        if message.video_note:
            return UploadSource(
                file_id=message.video_note.file_id,
                file_name=self._generated_file_name(
                    prefix="video_note",
                    file_id=message.video_note.file_id,
                    extension="mp4",
                ),
                file_size=message.video_note.file_size or 0,
            )

        if message.sticker:
            extension = (
                "webm"
                if message.sticker.is_video
                else "tgs"
                if message.sticker.is_animated
                else "webp"
            )
            return UploadSource(
                file_id=message.sticker.file_id,
                file_name=self._generated_file_name(
                    prefix="sticker",
                    file_id=message.sticker.file_id,
                    extension=extension,
                ),
                file_size=message.sticker.file_size or 0,
            )

        return None

    def _generated_file_name(self, prefix: str, file_id: str, extension: str) -> str:
        safe_id = "".join(char for char in file_id[-8:] if char.isalnum()) or "file"
        return f"upload_{prefix}_{int(time.time())}_{safe_id}.{extension}"

    def _safe_file_name(self, file_name: str) -> str:
        candidate = os.path.basename(file_name.strip())
        return candidate or self._generated_file_name(prefix="file", file_id="default", extension="bin")
