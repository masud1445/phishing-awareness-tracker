import pytest

from scoring import calculate_awareness_metrics


def test_all_recipients_clicked():
    click_rate, awareness_score = calculate_awareness_metrics(1, 1)

    assert click_rate == 100.0
    assert awareness_score == 0.0


def test_partial_click_rate():
    click_rate, awareness_score = calculate_awareness_metrics(10, 2)

    assert click_rate == 20.0
    assert awareness_score == 80.0


def test_no_clicks():
    click_rate, awareness_score = calculate_awareness_metrics(10, 0)

    assert click_rate == 0.0
    assert awareness_score == 100.0


def test_no_recipients():
    click_rate, awareness_score = calculate_awareness_metrics(0, 0)

    assert click_rate == 0.0
    assert awareness_score is None


def test_negative_recipient_count_rejected():
    with pytest.raises(ValueError):
        calculate_awareness_metrics(-1, 0)


def test_negative_click_count_rejected():
    with pytest.raises(ValueError):
        calculate_awareness_metrics(10, -1)


def test_clicks_cannot_exceed_recipients():
    with pytest.raises(ValueError):
        calculate_awareness_metrics(2, 3)
