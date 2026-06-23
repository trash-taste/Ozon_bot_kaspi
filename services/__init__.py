"""Сервисы сравнения маркетплейсов и формирования отчетов."""

from .internet_compare import (
    compare_with_internet,
    search_internet_sources,
)
from .kaspi_compare import compare_with_kaspi, search_kaspi_product
from .report import (
    save_arbitrage_report,
    save_internet_comparison_report,
)

__all__ = [
    "compare_with_internet",
    "search_internet_sources",
    "compare_with_kaspi",
    "search_kaspi_product",
    "save_arbitrage_report",
    "save_internet_comparison_report",
]
