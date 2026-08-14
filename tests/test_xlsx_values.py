from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

import pytest
from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH

from opendocs.parsers.xlsx.values import format_saved_value


@pytest.mark.parametrize(
    ("value", "number_format", "expected"),
    [
        (True, "General", "TRUE"),
        ("#DIV/0!", "General", "#DIV/0!"),
        (Decimal("1234"), "0", "1234"),
        (Decimal("1234.5"), "0.00", "1234.50"),
        (Decimal("1234.5"), "#,##0.00", "1,234.50"),
        (Decimal("0.125"), "0.00%", "12.50%"),
        (Decimal("1234.5"), "$#,##0.00", "$1,234.50"),
        (Decimal("1234"), "¥#,##0", "¥1,234"),
        (Decimal("-1234.5"), "#,##0.00;(#,##0.00)", "(1,234.50)"),
        (
            Decimal("-1234.5"),
            '_(€* #,##0.00_);_(€* (#,##0.00);_(€* "-"??_);_(@_)',
            "(€1,234.50)",
        ),
    ],
)
def test_format_saved_value_supports_core_and_accounting_formats(
    value: object,
    number_format: str,
    expected: str,
) -> None:
    result = format_saved_value(value, number_format, epoch=WINDOWS_EPOCH)

    assert result.text == expected
    assert result.warning is None


def test_format_saved_value_supports_both_date_systems_and_elapsed_time() -> None:
    windows = format_saved_value(43831, "yyyy-mm-dd", epoch=WINDOWS_EPOCH)
    mac = format_saved_value(42369, "yyyy-mm-dd", epoch=MAC_EPOCH)
    timestamp = format_saved_value(
        datetime(2020, 1, 2, 3, 4, 5),
        "yyyy-mm-dd hh:mm:ss",
        epoch=WINDOWS_EPOCH,
    )
    elapsed = format_saved_value(1.5, "[h]:mm:ss", epoch=WINDOWS_EPOCH)

    assert windows.text == "2020-01-01"
    assert mac.text == "2020-01-01"
    assert timestamp.text == "2020-01-02 03:04:05"
    assert elapsed.text == "36:00:00"
    assert not any(item.warning for item in (windows, mac, timestamp, elapsed))


def test_format_saved_value_supports_common_twelve_hour_time() -> None:
    result = format_saved_value(
        time(15, 4, 5),
        "h:mm:ss AM/PM",
        epoch=WINDOWS_EPOCH,
    )

    assert result.text == "3:04:05 PM"
    assert result.warning is None


@pytest.mark.parametrize(
    "number_format",
    [
        "0.00E+00",
        "# ?/?",
        "[Red]0.00",
        "[$-409]d-mmm-yy",
        "[>100]0;0",
        '0.00" kg"',
        "0.00\\k",
    ],
)
def test_format_saved_value_falls_back_for_unsupported_number_formats(
    number_format: str,
) -> None:
    result = format_saved_value(1234.5, number_format, epoch=WINDOWS_EPOCH)

    assert result.text == "1234.5"
    assert result.warning == "unsupported number format"
