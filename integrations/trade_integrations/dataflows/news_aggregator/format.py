"""Format merged articles into agent-facing markdown strings."""

from __future__ import annotations

from .models import NewsArticle


def _source_label(article: NewsArticle) -> str:
    if article.attributions:
        labels = [
            f"{attr.publisher} ({attr.vendor})" if attr.publisher != attr.vendor else attr.vendor
            for attr in article.attributions
        ]
        return "; ".join(labels)
    vendors = ", ".join(article.vendors) if article.vendors else article.vendor
    if article.source and vendors:
        return f"{article.source} ({vendors})"
    return article.source or vendors or "unknown"


def _render_articles(articles: list[NewsArticle]) -> str:
    blocks: list[str] = []
    for article in articles:
        block = f"### {article.title}"
        if len(article.attributions) > 1:
            block += f"\nAlso reported by: {_source_label(article)}"
        else:
            block += f" (source: {_source_label(article)})"
        if article.summary:
            block += f"\n{article.summary}"
        if article.link:
            block += f"\nLink: {article.link}"
        blocks.append(block)
    return "\n\n".join(blocks) + ("\n\n" if blocks else "")


def format_ticker_news(
    articles: list[NewsArticle],
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    resolved_suffix: str = "",
) -> str:
    if not articles:
        return (
            f"No news found for {ticker}{resolved_suffix} "
            f"between {start_date} and {end_date}"
        )
    header = f"## {ticker}{resolved_suffix} News, from {start_date} to {end_date}:\n\n"
    return header + _render_articles(articles)


def format_global_news(
    articles: list[NewsArticle],
    *,
    start_date: str,
    end_date: str,
) -> str:
    if not articles:
        return f"No global news found between {start_date} and {end_date}"
    header = f"## Global Market News, from {start_date} to {end_date}:\n\n"
    return header + _render_articles(articles)
