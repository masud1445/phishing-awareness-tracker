def calculate_awareness_metrics(total_recipients, link_clicks):
    """
    Calculate phishing-awareness campaign metrics.

    Awareness Score:
        ((total_recipients - link_clicks) / total_recipients) * 100

    Click Rate:
        (link_clicks / total_recipients) * 100

    Returns:
        tuple: (click_rate, awareness_score)

    If there are no recipients:
        click_rate = 0.0
        awareness_score = None
    """

    if total_recipients < 0 or link_clicks < 0:
        raise ValueError("Recipient and click counts cannot be negative.")

    if link_clicks > total_recipients:
        raise ValueError("Link clicks cannot exceed total recipients.")

    if total_recipients == 0:
        return 0.0, None

    click_rate = round(
        (link_clicks / total_recipients) * 100,
        1,
    )

    awareness_score = round(
        ((total_recipients - link_clicks) / total_recipients) * 100,
        1,
    )

    return click_rate, awareness_score
