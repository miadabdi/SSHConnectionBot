from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 3500


class OutputPager:
    def __init__(self, text: str, page_size: int = PAGE_SIZE) -> None:
        self.text = text
        self.page_size = page_size
        self.pages = self._split_pages()

    def _split_pages(self) -> list[str]:
        if len(self.text) <= self.page_size:
            return [self.text]

        pages: list[str] = []
        remaining = self.text
        while remaining:
            if len(remaining) <= self.page_size:
                pages.append(remaining)
                break

            chunk = remaining[: self.page_size]
            newline_idx = chunk.rfind("\n")
            if newline_idx > self.page_size // 2:
                pages.append(remaining[:newline_idx])
                remaining = remaining[newline_idx + 1 :]
            else:
                pages.append(chunk)
                remaining = remaining[self.page_size :]

        return pages

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    def get_page(self, page_number: int) -> str:
        index = max(0, min(page_number - 1, len(self.pages) - 1))
        return self.pages[index]

    @staticmethod
    def keyboard(current_page: int, total_pages: int, callback_prefix: str) -> InlineKeyboardMarkup:
        buttons = []
        if current_page > 1:
            buttons.append(
                InlineKeyboardButton(
                    text="◀️ Prev",
                    callback_data=f"{callback_prefix}:{current_page - 1}",
                )
            )

        buttons.append(
            InlineKeyboardButton(
                text=f"📄 {current_page}/{total_pages}",
                callback_data="noop",
            )
        )

        if current_page < total_pages:
            buttons.append(
                InlineKeyboardButton(
                    text="Next ▶️",
                    callback_data=f"{callback_prefix}:{current_page + 1}",
                )
            )

        return InlineKeyboardMarkup(inline_keyboard=[buttons])
