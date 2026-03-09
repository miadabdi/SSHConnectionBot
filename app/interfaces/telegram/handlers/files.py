import os
import shutil
import tempfile

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from app.application.services import FileTransferService
from app.domain.errors import SessionUnavailableError
from app.utils.formatting import Formatter

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


class FileHandler:
    def __init__(self, service: FileTransferService) -> None:
        self.service = service
        self.router = Router(name="files")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_download, Command("download"))
        self.router.message.register(
            self.handle_upload,
            lambda m: m.document and m.caption and m.caption.strip().startswith("/upload"),
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

    async def handle_upload(self, message: Message, bot: Bot) -> None:
        if not message.document or not message.caption:
            return

        parts = message.caption.strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("❌ Usage: upload with caption <code>/upload &lt;remote_path&gt;</code>")
            return

        remote_path = parts[1].strip()
        status = await message.answer(f"📤 Uploading to <code>{Formatter.escape_html(remote_path)}</code>...")

        temp_dir = tempfile.mkdtemp(prefix="sshbot-ul-")
        local_path = os.path.join(temp_dir, message.document.file_name or "upload.bin")

        try:
            file_info = await bot.get_file(message.document.file_id)
            await bot.download_file(file_info.file_path, local_path)
            await self.service.upload_local_file(
                user_id=message.from_user.id,
                local_path=local_path,
                remote_path=remote_path,
            )
        except SessionUnavailableError:
            await status.edit_text("ℹ️ No active SSH session. Use /connect first.")
            return
        except Exception as exc:
            await status.edit_text(f"❌ Upload failed: {Formatter.escape_html(str(exc))}")
            return
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        await status.edit_text(
            f"✅ Uploaded to <code>{Formatter.escape_html(remote_path)}</code>\n"
            f"📊 Size: {message.document.file_size:,} bytes"
        )
