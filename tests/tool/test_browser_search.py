from app.tool.browser_use_tool import _is_navigable_http_url


def test_search_navigation_accepts_only_real_http_urls():
    assert _is_navigable_http_url("https://example.com/news")
    assert _is_navigable_http_url("http://example.com")

    assert not _is_navigable_http_url("")
    assert not _is_navigable_http_url("   ")
    assert not _is_navigable_http_url("javascript:alert(1)")
    assert not _is_navigable_http_url("example.com/no-scheme")
