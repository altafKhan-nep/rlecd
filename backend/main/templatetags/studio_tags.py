"""Small presentation helpers for the custom admin templates.

Kept as a filter rather than logic in the view so the tiering rule lives in one
place and both the dashboard and any future template can share it.
"""
from django import template

register = template.Library()


@register.filter
def score_tier(score):
    """Map a 0-100 lead score to a colour tier.

    The admin stylesheet defines .s-hi, .s-mid and .s-lo. Without this the chip
    falls back to the untiered base rule and every score looks identical, which
    defeats the point of colouring them.
    """
    try:
        score = int(score)
    except (TypeError, ValueError):
        return "s-lo"
    if score >= 70:
        return "s-hi"
    if score >= 40:
        return "s-mid"
    return "s-lo"
