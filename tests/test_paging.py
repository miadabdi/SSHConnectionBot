from app.utils.paging import OutputPager


def test_pager_splits_long_text() -> None:
    text = ("line\n" * 2000).strip()
    pager = OutputPager(text, page_size=200)
    assert pager.total_pages > 1
    assert pager.get_page(1)
    assert pager.get_page(pager.total_pages)
