"""
dexscreener_client.py -- checks a token's CURRENT liquidity to determine
alive vs dead. Public API, no key, confirmed free across multiple independent
sources (300 req/min rate limit). NOTE: the exact base URL below is built
from consistent third-party descriptions, not a directly-fetched official
doc page -- worth a live sanity check on first real deploy, same as any
endpoint I can't test against a live server from this sandbox.
"""

import requests

BASE_URL = "https://api.dexscreener.com/latest/dex/tokens"


def get_current_liquidity_usd(mint: str, timeout: int = 10):
    """
    Returns current USD liquidity for a token, or None if no pair found
    (which itself is a strong signal -- a token with zero indexed pairs
    is likely already dead/never got real liquidity).
    """
    try:
        resp = requests.get(f"{BASE_URL}/{mint}", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [dexscreener] fetch failed for {mint}: {e}")
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return 0.0  # no pairs found = treat as zero liquidity, not unknown

    # A token can have multiple pairs (different DEXs) -- take the max
    # liquidity across all of them as the token's real current liquidity.
    liquidities = []
    for p in pairs:
        liq = p.get("liquidity", {}).get("usd")
        if liq is not None:
            liquidities.append(float(liq))
    return max(liquidities) if liquidities else 0.0
