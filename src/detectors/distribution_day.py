"""
Distribution Day Detector V3 — 4-card scoring system.
Extracted from server.py for modular reuse across dashboards.

signal_types: standard | heavy | special | reversal
"""
import math


def has_long_upper_shadow(bar, body_ratio=1.5):
    """上影 >= body * body_ratio OR 上影 >= amplitude * 0.5"""
    body = abs(bar['close'] - bar['open'])
    amplitude = bar['high'] - bar['low']
    upper_shadow = bar['high'] - max(bar['close'], bar['open'])
    return (
        (body > 0 and upper_shadow >= body * body_ratio)
        or (amplitude > 0 and upper_shadow >= amplitude * 0.5)
    )


def detect(klines, params):
    """
    Apply 4-card distribution day rules.

    params.cards = {
        card1: {enabled, decline, vol},                    # Standard
        card2: {enabled, chg_min, chg_max, surge, vol, shadow},  # Stealth
        card3: {enabled, surge, vol, shadow, midpt},       # Reversal
        card4: {enabled, decline, vol},                    # Heavy x2
    }
    Returns: list of signal dicts (includes all bar fields + signal_type, weight, total_score)
    """
    cards = params.get('cards', {})
    c1 = cards.get('card1', {})
    c2 = cards.get('card2', {})
    c3 = cards.get('card3', {})
    c4 = cards.get('card4', {})

    results = []

    for bar in klines:
        cp = bar.get('change_pct', 0)
        close_pos = bar.get('close_position', 50)
        vol_ratio = bar.get('volume_ratio', 1.0)
        upper_shadow = bar.get('upper_shadow_pct', 0)
        body_pct = bar.get('body_pct', 0)
        hl_range = bar['high'] - bar['low']
        amplitude = (hl_range / bar['prev_close'] * 100) if bar.get('prev_close', 0) > 0 else 0
        prev_close = bar.get('prev_close', bar['close'])
        surge_pct = ((bar['high'] - prev_close) / prev_close * 100) if prev_close > 0 else 0
        midpoint = (bar['high'] + bar['low']) / 2

        signal_type = None
        weight = 1

        # Card 1: Standard — close < prev_close + volume >= threshold + decline >= threshold
        if c1.get('enabled', True):
            if cp <= c1.get('decline', -0.10) and vol_ratio >= c1.get('vol', 1.00):
                signal_type = 'standard'

        # Card 4: Heavy (overrides standard)
        if c4.get('enabled', True):
            if cp <= c4.get('decline', -1.50) and vol_ratio >= c4.get('vol', 0.98):
                signal_type = 'heavy'
                weight = 2

        # Card 2: Stealth (假阳线) — narrow decline + surge + volume + long upper shadow
        if signal_type is None and c2.get('enabled', True):
            p_chg_min = c2.get('chg_min', -0.30)
            p_chg_max = c2.get('chg_max', 0.20)
            has_shadow = has_long_upper_shadow(bar, c2.get('shadow', 1.5))
            if (p_chg_min <= cp <= p_chg_max
                and surge_pct >= c2.get('surge', 0.50)
                and vol_ratio >= c2.get('vol', 1.10)
                and has_shadow
                and bar['close'] <= midpoint):
                signal_type = 'special'

        # Card 3: Reversal — close down + surge + volume + long upper shadow + close <= midpt%
        if signal_type is None and c3.get('enabled', True):
            has_shadow = has_long_upper_shadow(bar, c3.get('shadow', 1.5))
            if (cp < 0
                and surge_pct >= c3.get('surge', 0.50)
                and vol_ratio >= c3.get('vol', 1.10)
                and has_shadow
                and close_pos <= c3.get('midpt', 50)):
                signal_type = 'reversal'

        if signal_type:
            results.append({
                **bar,
                'type': 'bearish',
                'signal_type': signal_type,
                'weight': weight,
                'total_score': (1 if vol_ratio >= 1.0 else 0) + (4 if cp <= -1.0 else (2 if cp <= -0.5 else (1 if cp <= -0.2 else 0))),
            })

    return results
