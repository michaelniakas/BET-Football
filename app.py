"""
FIFA World Cup 2026 — Betting Optimization Tool (mock money, 1X2)
================================================================
A Streamlit app that pulls 1X2 (home / draw / away) match odds, asks Claude for a
quantitative read on each fixture, sizes value bets with the Kelly criterion, and
tracks a mock-money bankroll across a bet slip and performance dashboard.

Run with:
    pip install -r requirements.txt
    streamlit run app.py

Nothing here uses real money. Odds and analysis are for educational/simulation use.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from dateutil import parser as dtparser

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Default model. Model identifiers change over time — if this errors with a
# "model not found" message, set the correct string for your account from
# https://docs.claude.com/en/docs/about-claude/models
DEFAULT_MODEL = "claude-sonnet-4-5"

ODDS_ENDPOINT = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"

TOURNAMENT_START = date(2026, 6, 11)
TOURNAMENT_END = date(2026, 7, 19)

OUTCOME_KEYS = ["Home", "Draw", "Away"]

# 10 mock fixtures (Jun 11–19) used when no Odds API key is supplied or the
# request fails. Odds are decimal, 1X2.
MOCK_FIXTURES = [
    {"id": "mock_usa_mex", "home": "USA", "away": "Mexico",
     "round": "Group A", "kickoff": "2026-06-11T19:00:00Z", "h": 2.40, "d": 3.20, "a": 3.10},
    {"id": "mock_bra_arg", "home": "Brazil", "away": "Argentina",
     "round": "Group B", "kickoff": "2026-06-12T22:00:00Z", "h": 2.10, "d": 3.30, "a": 3.60},
    {"id": "mock_fra_eng", "home": "France", "away": "England",
     "round": "Group C", "kickoff": "2026-06-13T19:00:00Z", "h": 2.20, "d": 3.40, "a": 3.30},
    {"id": "mock_esp_ger", "home": "Spain", "away": "Germany",
     "round": "Group D", "kickoff": "2026-06-14T19:00:00Z", "h": 2.30, "d": 3.25, "a": 3.20},
    {"id": "mock_por_mar", "home": "Portugal", "away": "Morocco",
     "round": "Group E", "kickoff": "2026-06-15T16:00:00Z", "h": 1.85, "d": 3.50, "a": 4.50},
    {"id": "mock_jpn_kor", "home": "Japan", "away": "South Korea",
     "round": "Group F", "kickoff": "2026-06-16T13:00:00Z", "h": 2.70, "d": 3.20, "a": 2.70},
    {"id": "mock_ned_sen", "home": "Netherlands", "away": "Senegal",
     "round": "Group G", "kickoff": "2026-06-17T19:00:00Z", "h": 1.90, "d": 3.40, "a": 4.20},
    {"id": "mock_aus_sau", "home": "Australia", "away": "Saudi Arabia",
     "round": "Group H", "kickoff": "2026-06-18T16:00:00Z", "h": 2.00, "d": 3.30, "a": 4.00},
    {"id": "mock_ita_cro", "home": "Italy", "away": "Croatia",
     "round": "Group A", "kickoff": "2026-06-19T19:00:00Z", "h": 2.15, "d": 3.35, "a": 3.50},
    {"id": "mock_bel_pol", "home": "Belgium", "away": "Poland",
     "round": "Group B", "kickoff": "2026-06-19T22:00:00Z", "h": 1.95, "d": 3.45, "a": 4.10},
]

# Risk level controls staking discipline / variance: the Kelly fraction applied
# to every stake and the hard per-bet cap as a share of balance. "Balanced" is
# identical to the original Half-Kelly / 5%-cap default.
RISK_PRESETS = {
    "Conservative": {"kelly": 0.25, "cap": 0.025},
    "Balanced":     {"kelly": 0.50, "cap": 0.05},
    "Aggressive":   {"kelly": 1.00, "cap": 0.10},
}

# Allocation methods spread a stake budget across the chosen picks.
ALLOC_METHODS = ["Kelly-weighted", "Edge-weighted", "Equal"]


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #

def init_state() -> None:
    ss = st.session_state
    ss.setdefault("initial_balance", 1000.0)
    ss.setdefault("balance", 1000.0)
    ss.setdefault("bets", [])            # list[dict]
    ss.setdefault("analysis_cache", {})  # match_id -> analysis dict
    ss.setdefault("odds_cache", {})      # cache_key -> list[match]
    ss.setdefault("bet_seq", 0)          # incrementing id for placed bets
    ss.setdefault("match_pool", {})      # match_id -> match (accumulated)
    ss.setdefault("alloc_plan", [])      # last built allocation plan


# --------------------------------------------------------------------------- #
# Odds data
# --------------------------------------------------------------------------- #

def _normalize_api_match(m: dict):
    """Turn one Odds API record into our internal match shape, averaging odds
    across all returned bookmakers."""
    home = m.get("home_team")
    away = m.get("away_team")
    if not home or not away:
        return None

    h_prices, d_prices, a_prices = [], [], []
    for bk in m.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes", []):
                name, price = o.get("name"), o.get("price")
                if price is None:
                    continue
                if name == home:
                    h_prices.append(price)
                elif name == away:
                    a_prices.append(price)
                elif name == "Draw":
                    d_prices.append(price)

    if not (h_prices and d_prices and a_prices):
        return None

    def avg(xs):
        return round(sum(xs) / len(xs), 2)

    return {
        "id": m.get("id", f"{home}_{away}"),
        "home": home,
        "away": away,
        "round": "Group Stage",
        "kickoff": m.get("commence_time", ""),
        "h": avg(h_prices),
        "d": avg(d_prices),
        "a": avg(a_prices),
    }


def _mock_for_day(day: date):
    day_iso = day.isoformat()
    matches = [m for m in MOCK_FIXTURES if m["kickoff"].startswith(day_iso)]
    # If the picked day has no scripted fixture, show the full mock card so the
    # app is always explorable.
    return matches if matches else MOCK_FIXTURES


def fetch_odds(api_key: str, day: date):
    """Return (matches, source_label). Falls back to mock fixtures whenever the
    key is missing or the request fails."""
    day_iso = day.isoformat()

    if not api_key:
        return _mock_for_day(day), "mock"

    cache_key = f"{day_iso}:{api_key[:6]}"
    if cache_key in st.session_state.odds_cache:
        return st.session_state.odds_cache[cache_key], "live"

    params = {
        "apiKey": api_key,
        "regions": "eu",
        "markets": "h2h",
        "dateFrom": f"{day_iso}T00:00:00Z",
        "dateTo": f"{day_iso}T23:59:59Z",
    }
    try:
        resp = requests.get(ODDS_ENDPOINT, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        matches = [x for x in (_normalize_api_match(m) for m in raw) if x]
        if not matches:
            return _mock_for_day(day), "mock-empty"
        st.session_state.odds_cache[cache_key] = matches
        return matches, "live"
    except Exception as exc:  # network, auth, quota, parse — all fall back
        st.session_state["_last_odds_error"] = str(exc)
        return _mock_for_day(day), "mock-error"


# --------------------------------------------------------------------------- #
# Probability / Kelly helpers
# --------------------------------------------------------------------------- #

def implied_probs(h: float, d: float, a: float) -> dict:
    """Raw (un-normalized) market implied probabilities, 1/odds per outcome."""
    return {"Home": 1 / h, "Draw": 1 / d, "Away": 1 / a}


def kelly_fraction(p: float, decimal_odds: float) -> float:
    """f* = (p*(b+1) - 1) / b  where b = decimal_odds - 1."""
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    f = (p * (b + 1) - 1) / b
    return max(0.0, f)


def risk_tier(edge: float) -> str:
    if edge <= 0:
        return "No bet"
    if edge < 0.03:
        return "🟡 Speculative"
    if edge < 0.08:
        return "🟢 Value"
    return "🔵 Strong value"


# --------------------------------------------------------------------------- #
# Analysis (Claude or mock fallback)
# --------------------------------------------------------------------------- #

def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence (``` or ```json) and anything after a closing fence
        t = t[3:]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        if "```" in t:
            t = t[: t.index("```")]
    return t.strip()


def _coerce_analysis(data: dict, match: dict) -> dict:
    """Defensive normalization so the UI never crashes on a malformed model
    response."""
    def num(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    model_p = {
        "Home": num(data.get("home_prob")),
        "Draw": num(data.get("draw_prob")),
        "Away": num(data.get("away_prob")),
    }
    total = sum(model_p.values())
    if total > 0:  # renormalize to guard against rounding drift
        model_p = {k: v / total for k, v in model_p.items()}

    imp = implied_probs(match["h"], match["d"], match["a"])
    edges = {k: model_p[k] - imp[k] for k in OUTCOME_KEYS}

    vbs = data.get("value_bets") or []
    clean_vbs = []
    if isinstance(vbs, list):
        for vb in vbs:
            if not isinstance(vb, dict):
                continue
            clean_vbs.append({
                "outcome": str(vb.get("outcome", "")),
                "edge": num(vb.get("edge")),
                "reasoning": str(vb.get("reasoning", "")),
            })

    conf = str(data.get("confidence", "low")).lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"

    kf = data.get("key_factors") or []
    kf = [str(x) for x in kf][:3] if isinstance(kf, list) else []

    return {
        "model_probs": model_p,
        "implied": imp,
        "edges": edges,
        "value_bets": clean_vbs,
        "confidence": conf,
        "key_factors": kf,
    }


def analyze_with_claude(api_key: str, model: str, match: dict) -> dict:
    """Call the Anthropic SDK for a quant read. Raises on failure so the caller
    can decide whether to fall back."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    system = ("You are a quantitative football analyst. "
              "Return ONLY valid JSON, no markdown.")
    user = (
        f"WC 2026: {match['home']} vs {match['away']}, {match['round']}. "
        f"Market odds H={match['h']} D={match['d']} A={match['a']}. "
        "Return: {home_prob, draw_prob, away_prob, "
        "implied:{h,d,a}, edges:{h,d,a}, "
        "value_bets:[{outcome, edge, reasoning}], "
        "confidence:'high'|'medium'|'low', "
        "key_factors:[str,str,str]}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    )
    data = json.loads(_strip_json(text))
    return _coerce_analysis(data, match)


def mock_analysis(match: dict) -> dict:
    """Deterministic offline analysis: de-vig the market, then nudge each
    outcome by a small seeded perturbation to manufacture edges."""
    imp = implied_probs(match["h"], match["d"], match["a"])
    overround = sum(imp.values())
    fair = {k: v / overround for k, v in imp.items()}

    # Seeded, reproducible heuristic: pick one "model favourite" outcome and
    # shift probability mass onto it from the others. A directed boost (rather
    # than symmetric noise) reliably produces dispersion that can beat the raw
    # implied line, so the offline demo surfaces a realistic mix of value bets.
    rng = random.Random(match["id"])
    fav = rng.choice(OUTCOME_KEYS)
    boost = rng.uniform(0.05, 0.10)              # mass added to the favourite
    others = [k for k in OUTCOME_KEYS if k != fav]
    model_p = dict(fair)
    model_p[fav] += boost
    for k in others:                              # take the boost from the rest
        model_p[k] = max(0.01, model_p[k] - boost / 2)
    s = sum(model_p.values())
    model_p = {k: model_p[k] / s for k in OUTCOME_KEYS}  # renormalize to sum 1

    edges = {k: model_p[k] - imp[k] for k in OUTCOME_KEYS}
    value_bets = [
        {"outcome": k, "edge": round(edges[k], 4),
         "reasoning": f"Model favours {k.lower()} relative to the priced line."}
        for k in OUTCOME_KEYS if edges[k] > 0.03
    ]
    max_edge = max(edges.values())
    conf = "high" if max_edge > 0.07 else ("medium" if max_edge > 0.03 else "low")

    data = {
        "home_prob": model_p["Home"],
        "draw_prob": model_p["Draw"],
        "away_prob": model_p["Away"],
        "implied": {"h": imp["Home"], "d": imp["Draw"], "a": imp["Away"]},
        "edges": {"h": edges["Home"], "d": edges["Draw"], "a": edges["Away"]},
        "value_bets": value_bets,
        "confidence": conf,
        "key_factors": [
            "Market de-vigged to a fair baseline",
            f"Implied overround ~= {(overround - 1) * 100:.1f}%",
            "Offline heuristic — connect an Anthropic key for a real read",
        ],
    }
    return _coerce_analysis(data, match)


def get_probs_devig(match: dict) -> dict:
    """Strip bookmaker margin and return fair probabilities."""
    imp = {"Home": 1 / match["h"], "Draw": 1 / match["d"], "Away": 1 / match["a"]}
    total = sum(imp.values())
    fair = {k: v / total for k, v in imp.items()}
    return {
        "model_probs": fair,
        "implied": imp,
        "edges": {k: 0.0 for k in OUTCOME_KEYS},  # no edge vs itself
        "value_bets": [],
        "confidence": "medium",
        "key_factors": [
            f"Overround removed: {(total - 1) * 100:.2f}%",
            "Fair probs = 1/odds normalized to 1.0",
            "No edge over market by definition — use as calibration baseline",
        ],
    }


def get_probs_apifootball(api_key: str, match: dict) -> dict:
    """Fetch win/draw/loss probabilities from API-Football predictions endpoint.
    Endpoint: GET https://v3.football.api-sports.io/predictions?fixture={fixture_id}
    Headers: x-rapidapi-key: {api_key}, x-rapidapi-host: v3.football.api-sports.io
    The fixture_id for WC 2026 matches won't exist yet — fall back to get_probs_devig
    with a warning if the API returns empty or errors."""
    if not api_key:
        st.warning("No API-Football key supplied — using de-vigged market odds "
                   "as the probability source instead.")
        return get_probs_devig(match)

    # Real predictions require a numeric API-Football fixture id. WC 2026 mock /
    # odds-api fixtures don't carry one, so the call will return empty and we
    # fall back — exactly the documented behaviour.
    fixture_id = (match.get("fixture_id")
                  or match.get("api_fixture_id")
                  or match.get("id"))
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/predictions",
            params={"fixture": fixture_id},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": "v3.football.api-sports.io",
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("response") or []
        if not results:
            raise ValueError("empty predictions response")

        percent = results[0]["predictions"]["percent"]

        def pct(v):
            return float(str(v).replace("%", "").strip())

        raw = {
            "Home": pct(percent.get("home", 0)),
            "Draw": pct(percent.get("draw", 0)),
            "Away": pct(percent.get("away", 0)),
        }
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("non-positive probability total")
        model_p = {k: raw[k] / total for k in OUTCOME_KEYS}  # normalize to 1.0

        imp = {"Home": 1 / match["h"], "Draw": 1 / match["d"], "Away": 1 / match["a"]}
        edges = {k: model_p[k] - imp[k] for k in OUTCOME_KEYS}
        value_bets = [
            {"outcome": k, "edge": round(edges[k], 4),
             "reasoning": f"API-Football model favours {k.lower()} vs the priced line."}
            for k in OUTCOME_KEYS if edges[k] > 0.03
        ]
        max_edge = max(edges.values())
        conf = "high" if max_edge > 0.07 else ("medium" if max_edge > 0.03 else "low")

        return {
            "model_probs": model_p,
            "implied": imp,
            "edges": edges,
            "value_bets": value_bets,
            "confidence": conf,
            "key_factors": [
                f"API-Football predicted H {model_p['Home']*100:.0f}% / "
                f"D {model_p['Draw']*100:.0f}% / A {model_p['Away']*100:.0f}%",
                f"Fixture id: {fixture_id}",
                "Probabilities normalized to sum to 1.0",
            ],
        }
    except Exception as exc:
        st.warning(f"API-Football lookup failed ({exc}). Falling back to "
                   "de-vigged market odds.")
        return get_probs_devig(match)


# --------------------------------------------------------------------------- #
# Balance accounting (derived — no double counting on reruns)
# --------------------------------------------------------------------------- #

def recompute_balance() -> None:
    """balance = initial - all stakes committed + returns on settled winners."""
    ss = st.session_state
    total_staked = sum(b["stake"] for b in ss.bets)
    total_returned = sum(
        b["stake"] * b["odds"] for b in ss.bets
        if b["result"] != "Pending" and b["result"] == b["outcome"]
    )
    ss.balance = ss.initial_balance - total_staked + total_returned


def add_bet(match: dict, outcome: str, odds: float, stake: float,
            ai_prob: float, edge: float) -> None:
    ss = st.session_state
    ss.bet_seq += 1
    label = (match["home"] if outcome == "Home"
             else match["away"] if outcome == "Away" else "Draw")
    ss.bets.append({
        "bet_id": ss.bet_seq,
        "match_id": match["id"],
        "match": f"{match['home']} vs {match['away']}",
        "selection": label,
        "outcome": outcome,
        "odds": round(float(odds), 2),
        "stake": round(float(stake), 2),
        "ai_prob": round(float(ai_prob), 4),
        "edge": round(float(edge), 4),
        "result": "Pending",
    })
    recompute_balance()


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

CONF_BADGE = {
    "high": ("HIGH CONFIDENCE", "#1a7f37"),
    "medium": ("MEDIUM CONFIDENCE", "#9a6700"),
    "low": ("LOW CONFIDENCE", "#82071e"),
}


def render_analysis(match: dict, analysis: dict,
                    kelly_frac: float, cap_pct: float) -> None:
    mp = analysis["model_probs"]
    imp = analysis["implied"]
    edges = analysis["edges"]

    # ---- Probability comparison chart -----------------------------------
    fig = go.Figure()
    fig.add_bar(name="AI probability", x=OUTCOME_KEYS,
                y=[mp[k] for k in OUTCOME_KEYS],
                marker_color="#2563eb",
                text=[f"{mp[k]*100:.1f}%" for k in OUTCOME_KEYS],
                textposition="outside")
    fig.add_bar(name="Market implied", x=OUTCOME_KEYS,
                y=[imp[k] for k in OUTCOME_KEYS],
                marker_color="#94a3b8",
                text=[f"{imp[k]*100:.1f}%" for k in OUTCOME_KEYS],
                textposition="outside")
    fig.update_layout(barmode="group", height=320,
                      yaxis_title="Probability", yaxis_tickformat=".0%",
                      legend=dict(orientation="h", y=1.15),
                      margin=dict(t=30, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True,
                    key=f"chart_{match['id']}")

    # ---- Confidence badge -----------------------------------------------
    label, color = CONF_BADGE[analysis["confidence"]]
    st.markdown(
        f"<span style='background:{color};color:white;padding:4px 12px;"
        f"border-radius:12px;font-size:0.8rem;font-weight:600'>{label}</span>",
        unsafe_allow_html=True,
    )

    # ---- Value bet highlights -------------------------------------------
    st.markdown("**Value bets**")
    if analysis["value_bets"]:
        for vb in analysis["value_bets"]:
            edge = vb["edge"]
            color = "#dcfce7" if edge > 0.03 else "#fef9c3"
            border = "#16a34a" if edge > 0.03 else "#ca8a04"
            st.markdown(
                f"<div style='background:{color};border-left:4px solid {border};"
                f"padding:8px 12px;border-radius:6px;margin-bottom:6px'>"
                f"<b>{vb['outcome']}</b> &nbsp; edge "
                f"<b>{edge*100:+.1f}%</b><br>"
                f"<span style='font-size:0.85rem'>{vb['reasoning']}</span></div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No outcome cleared the value threshold.")

    # ---- Key factors -----------------------------------------------------
    if analysis["key_factors"]:
        st.markdown("**Key factors**")
        for kf in analysis["key_factors"]:
            st.markdown(f"- {kf}")

    # ---- Bet optimizer ---------------------------------------------------
    st.divider()
    st.markdown("### 🎯 Bet optimizer")
    st.caption(f"Kelly × {kelly_frac:g} · hard-capped at {cap_pct*100:g}% of balance")

    balance = st.session_state.balance
    odds_map = {"Home": match["h"], "Draw": match["d"], "Away": match["a"]}

    rows = []
    for k in OUTCOME_KEYS:
        p = mp[k]
        o = odds_map[k]
        edge = edges[k]
        f = kelly_fraction(p, o) * kelly_frac
        kelly_stake = f * balance
        stake = min(kelly_stake, cap_pct * balance) if edge > 0 else 0.0
        ev = stake * (p * o - 1)  # expected profit of the staked amount
        rows.append({
            "Outcome": k,
            "AI Prob": p,
            "Market Prob": imp[k],
            "Edge": edge,
            "Stake $": round(stake, 2),
            "EV $": round(ev, 2),
            "Risk tier": risk_tier(edge),
        })

    # Pre-format for display (Streamlit's printf formatter does not rescale
    # 0-1 probabilities to %, so we format the strings ourselves).
    disp = pd.DataFrame([{
        "Outcome": r["Outcome"],
        "AI Prob": f"{r['AI Prob']*100:.1f}%",
        "Market Prob": f"{r['Market Prob']*100:.1f}%",
        "Edge": f"{r['Edge']*100:+.2f}%",
        "Stake $": f"${r['Stake $']:.2f}",
        "EV $": f"${r['EV $']:+.2f}",
        "Risk tier": r["Risk tier"],
    } for r in rows])
    st.dataframe(disp, hide_index=True, use_container_width=True)

    add_cols = st.columns(3)
    for i, k in enumerate(OUTCOME_KEYS):
        row = rows[i]
        with add_cols[i]:
            disabled = row["Edge"] <= 0 or row["Stake $"] <= 0
            if st.button(f"➕ Add {k}  (${row['Stake $']:.2f})",
                         key=f"add_{match['id']}_{k}",
                         disabled=disabled,
                         use_container_width=True):
                add_bet(match, k, odds_map[k], row["Stake $"],
                        mp[k], edges[k])
                st.success(f"Added {k} @ {odds_map[k]} for ${row['Stake $']:.2f}")
                st.rerun()


# --------------------------------------------------------------------------- #
# Allocation planner helpers
# --------------------------------------------------------------------------- #

def register_matches(matches: list) -> None:
    """Accumulate every fetched match so the planner can offer them later."""
    for m in matches:
        st.session_state.match_pool[m["id"]] = m


def candidate_pool(odds_key: str) -> dict:
    """match_id -> match. Seeds with all mock fixtures when running keyless so
    'choose specific matches' works immediately offline."""
    pool = dict(st.session_state.match_pool)
    if not odds_key:
        for m in MOCK_FIXTURES:
            pool.setdefault(m["id"], m)
    return pool


def get_or_make_analysis(match: dict, prob_source: str, anthropic_key: str,
                         model: str, apifootball_key: str = "") -> dict:
    """Reuse cached analysis; otherwise compute it using the selected
    probability source (matching the Analyze button) and cache it."""
    mid = match["id"]
    cached = st.session_state.analysis_cache.get(mid)
    if cached:
        return cached
    if prob_source == "De-vigged Market Odds":
        a = get_probs_devig(match)
    elif prob_source == "API-Football":
        a = get_probs_apifootball(apifootball_key, match)
    elif prob_source == "Claude AI" and anthropic_key:
        try:
            a = analyze_with_claude(anthropic_key, model, match)
        except Exception:
            a = mock_analysis(match)
    else:  # Claude AI without a key → offline heuristic
        a = mock_analysis(match)
    st.session_state.analysis_cache[mid] = a
    return a


def best_value_pick(match: dict, analysis: dict):
    """Return the single highest positive-edge outcome for a match, or None."""
    edges = analysis["edges"]
    mp = analysis["model_probs"]
    odds_map = {"Home": match["h"], "Draw": match["d"], "Away": match["a"]}
    best_k = max(OUTCOME_KEYS, key=lambda k: edges[k])
    if edges[best_k] <= 0:
        return None
    return {
        "match": match,
        "outcome": best_k,
        "p": mp[best_k],
        "odds": odds_map[best_k],
        "edge": edges[best_k],
    }


def build_allocation_plan(picks: list, balance: float, budget: float,
                          method: str, kelly_frac: float, cap_pct: float) -> list:
    """Size a stake for each pick under the chosen method, respecting the total
    `budget` (level of bet) and per-bet cap (level of risk). Never recommends
    more than the Kelly stake for the Kelly method."""
    if not picks or budget <= 0:
        return []

    per_cap = cap_pct * balance

    if method == "Kelly-weighted":
        raw = []
        for pk in picks:
            f = kelly_fraction(pk["p"], pk["odds"]) * kelly_frac
            raw.append(min(f * balance, per_cap))
        total = sum(raw)
        scale = min(1.0, budget / total) if total > 0 else 0.0
        stakes = [r * scale for r in raw]

    elif method == "Edge-weighted":
        total_edge = sum(pk["edge"] for pk in picks)
        stakes = [
            min(budget * (pk["edge"] / total_edge) if total_edge > 0 else 0.0,
                per_cap)
            for pk in picks
        ]

    else:  # Equal
        each = budget / len(picks)
        stakes = [min(each, per_cap) for _ in picks]

    plan = []
    for pk, stake in zip(picks, stakes):
        stake = round(stake, 2)
        if stake <= 0:
            continue
        ev = stake * (pk["p"] * pk["odds"] - 1)
        plan.append({**pk, "stake": stake, "ev": round(ev, 2)})
    return plan


# --------------------------------------------------------------------------- #
# App layout
# --------------------------------------------------------------------------- #

st.set_page_config(page_title="WC 2026 Betting Optimizer",
                   page_icon="⚽", layout="wide")
init_state()

# ----------------------------- Sidebar ------------------------------------- #
with st.sidebar:
    st.header("⚽ WC 2026 Optimizer")
    st.caption("Mock-money 1X2 betting simulator")

    anthropic_key = st.text_input(
        "Anthropic API key", type="password",
        help="Used for the Analyze step. Blank → offline heuristic analysis.")
    prob_source = st.selectbox(
        "Probability source",
        ["Claude AI", "API-Football", "De-vigged Market Odds"],
        help="Where match probabilities come from when you Analyze.")
    model_name = st.text_input(
        "Claude model", value=DEFAULT_MODEL,
        help="Change if your account uses a different model identifier.")
    apifootball_key = ""
    if prob_source == "API-Football":
        apifootball_key = st.text_input(
            "API-Football key (RapidAPI)", type="password",
            help="API-Sports / RapidAPI key for the predictions endpoint.")
    odds_key = st.text_input(
        "Odds API key", type="password",
        help="the-odds-api.com key. Blank → mock fixtures.")

    new_bankroll = st.number_input(
        "Starting bankroll ($)", min_value=1.0,
        value=float(st.session_state.initial_balance), step=50.0)

    if st.button("Reset bankroll & bets", use_container_width=True):
        st.session_state.initial_balance = float(new_bankroll)
        st.session_state.balance = float(new_bankroll)
        st.session_state.bets = []
        st.session_state.bet_seq = 0
        st.rerun()

    # Sync starting bankroll if changed before any bets are placed.
    if (not st.session_state.bets
            and new_bankroll != st.session_state.initial_balance):
        st.session_state.initial_balance = float(new_bankroll)
        st.session_state.balance = float(new_bankroll)

    recompute_balance()

    st.divider()
    st.markdown("**🎚️ Risk & bet level**")
    risk_level = st.radio(
        "Risk level", list(RISK_PRESETS), index=1, horizontal=True,
        help="Staking discipline / variance. Conservative = ¼ Kelly & 2.5% "
             "cap, Balanced = ½ Kelly & 5% cap, Aggressive = full Kelly & "
             "10% cap.")
    kelly_frac = RISK_PRESETS[risk_level]["kelly"]
    cap_pct = RISK_PRESETS[risk_level]["cap"]
    st.caption(f"Kelly × {kelly_frac:g} · per-bet cap {cap_pct*100:g}% of balance")

    bet_level_pct = st.slider(
        "Bet level (budget to deploy)", min_value=1, max_value=50, value=10,
        step=1, format="%d%%",
        help="How much of your balance to put in play in one allocation plan. "
             "This sizes the total wager; risk level sizes each bet.")
    st.caption(f"Plan budget ≈ ${bet_level_pct/100*st.session_state.balance:,.2f}")

    pnl = st.session_state.balance - st.session_state.initial_balance
    st.divider()
    m1, m2 = st.columns(2)
    m1.metric("Balance", f"${st.session_state.balance:,.2f}",
              delta=f"{pnl:+,.2f}")
    roi_pct = (pnl / st.session_state.initial_balance * 100
               if st.session_state.initial_balance else 0.0)
    m2.metric("Session P&L", f"${pnl:+,.2f}", delta=f"{roi_pct:+.1f}%")

# ------------------------------- Tabs -------------------------------------- #
tab_matches, tab_alloc, tab_slip, tab_perf = st.tabs(
    ["📊 Matches & Analysis", "🎰 Allocation Planner",
     "🧾 Bet Slip", "📈 Performance"])


# ======================= TAB 1 — MATCHES & ANALYSIS ======================== #
with tab_matches:
    st.subheader("Matches & Analysis")

    picked = st.date_input(
        "Match date", value=TOURNAMENT_START,
        min_value=TOURNAMENT_START, max_value=TOURNAMENT_END,
        format="YYYY-MM-DD")

    matches, source = fetch_odds(odds_key, picked)
    register_matches(matches)
    if source == "mock":
        st.info("Showing **mock fixtures** (no Odds API key supplied).")
    elif source == "mock-error":
        st.warning("Odds request failed — falling back to mock fixtures. "
                   f"Detail: {st.session_state.get('_last_odds_error', 'unknown')}")
    elif source == "mock-empty":
        st.info("No live fixtures for that date — showing mock fixtures.")
    else:
        st.success(f"Loaded {len(matches)} live fixture(s) from the Odds API.")

    st.caption(f"Staking uses the **{risk_level}** risk level (set in sidebar). "
               "To spread a budget across several matches or days, use the "
               "Allocation Planner tab.")

    if not matches:
        st.write("No fixtures to show.")

    for match in matches:
        mid = match["id"]
        try:
            ko = dtparser.isoparse(match["kickoff"])
            ko_label = ko.strftime("%a %d %b %Y · %H:%M UTC")
        except Exception:
            ko_label = match.get("kickoff", "TBD")

        title = (f"{match['home']}  vs  {match['away']}    ·    "
                 f"{match['round']}    ·    {ko_label}")
        with st.expander(title, expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric(f"🏠 {match['home']}", match["h"])
            c2.metric("🤝 Draw", match["d"])
            c3.metric(f"✈️ {match['away']}", match["a"])

            if st.button("🔍 Analyze", key=f"analyze_{mid}"):
                with st.spinner("Analyzing..."):
                    if prob_source == "Claude AI":
                        if anthropic_key:
                            try:
                                st.session_state.analysis_cache[mid] = \
                                    analyze_with_claude(anthropic_key, model_name, match)
                            except Exception as exc:
                                st.warning(f"Claude call failed ({exc}). Using "
                                           "offline heuristic analysis instead.")
                                st.session_state.analysis_cache[mid] = mock_analysis(match)
                        else:
                            st.session_state.analysis_cache[mid] = mock_analysis(match)
                    elif prob_source == "De-vigged Market Odds":
                        st.session_state.analysis_cache[mid] = get_probs_devig(match)
                    else:  # API-Football
                        st.session_state.analysis_cache[mid] = \
                            get_probs_apifootball(apifootball_key, match)

            analysis = st.session_state.analysis_cache.get(mid)
            if analysis:
                render_analysis(match, analysis, kelly_frac, cap_pct)


# ===================== TAB — ALLOCATION PLANNER ============================ #
with tab_alloc:
    st.subheader("Allocation Planner")
    st.caption("Choose where to bet (specific matches or whole days), then let "
               "the planner spread your **bet-level budget** across the value "
               "picks at your chosen **risk level**.")

    balance = st.session_state.balance
    budget = round(bet_level_pct / 100 * balance, 2)

    a1, a2, a3 = st.columns(3)
    a1.metric("Budget to deploy", f"${budget:,.2f}", help="Bet level (sidebar)")
    a2.metric("Risk level", risk_level,
              help=f"Kelly × {kelly_frac:g}, cap {cap_pct*100:g}%")
    a3.metric("Balance", f"${balance:,.2f}")

    scope = st.radio("Allocate by", ["Specific matches", "Whole days"],
                     horizontal=True)
    method = st.selectbox(
        "Allocation method", ALLOC_METHODS,
        help="Kelly-weighted: stakes follow Kelly sizing, scaled down to fit "
             "the budget (never above Kelly). Edge-weighted: budget split in "
             "proportion to edge. Equal: budget split evenly.")

    selected_matches: list = []

    if scope == "Specific matches":
        pool = candidate_pool(odds_key)
        if not pool:
            st.info("No matches loaded yet. Open the **Matches & Analysis** tab "
                    "and pick a date to load fixtures (or run keyless for the "
                    "mock card), then come back here.")
        else:
            ordered = sorted(pool.values(), key=lambda m: m.get("kickoff", ""))
            labels = {f"{m['home']} vs {m['away']}  ·  "
                      f"{m.get('kickoff','')[:10]}  ·  {m['round']}": m
                      for m in ordered}
            chosen = st.multiselect("Choose matches", list(labels.keys()))
            selected_matches = [labels[c] for c in chosen]

    else:  # Whole days
        # Build the list of selectable tournament days.
        all_days = []
        cur = TOURNAMENT_START
        while cur <= TOURNAMENT_END:
            all_days.append(cur)
            cur += timedelta(days=1)
        default_days = [d for d in all_days if d <= date(2026, 6, 19)][:3]
        chosen_days = st.multiselect(
            "Choose days", all_days,
            default=default_days,
            format_func=lambda d: d.strftime("%a %d %b %Y"))
        for d in chosen_days:
            day_matches, _src = fetch_odds(odds_key, d)
            register_matches(day_matches)
            # only include fixtures actually on that day (mock fallback may
            # return the full card)
            for m in day_matches:
                if m.get("kickoff", "").startswith(d.isoformat()):
                    selected_matches.append(m)
        # de-dup
        seen = {}
        for m in selected_matches:
            seen[m["id"]] = m
        selected_matches = list(seen.values())

    st.divider()
    build = st.button("⚙️ Build allocation plan", type="primary",
                      disabled=not selected_matches, use_container_width=True)

    if build:
        with st.spinner(f"Analyzing {len(selected_matches)} match(es)..."):
            picks = []
            for m in selected_matches:
                a = get_or_make_analysis(m, prob_source, anthropic_key,
                                         model_name, apifootball_key)
                pk = best_value_pick(m, a)
                if pk:
                    picks.append(pk)
            st.session_state.alloc_plan = build_allocation_plan(
                picks, balance, budget, method, kelly_frac, cap_pct)
            st.session_state["_alloc_skipped"] = len(selected_matches) - len(picks)

    plan = st.session_state.alloc_plan
    if plan:
        skipped = st.session_state.get("_alloc_skipped", 0)
        if skipped:
            st.caption(f"{skipped} selected match(es) had no positive-edge "
                       "outcome and were skipped.")

        disp = pd.DataFrame([{
            "Match": f"{p['match']['home']} vs {p['match']['away']}",
            "Selection": (p['match']['home'] if p['outcome'] == 'Home'
                          else p['match']['away'] if p['outcome'] == 'Away'
                          else 'Draw'),
            "Odds": f"{p['odds']:.2f}",
            "AI Prob": f"{p['p']*100:.1f}%",
            "Edge": f"{p['edge']*100:+.2f}%",
            "Stake $": f"${p['stake']:.2f}",
            "EV $": f"${p['ev']:+.2f}",
            "Risk tier": risk_tier(p["edge"]),
        } for p in plan])
        st.dataframe(disp, hide_index=True, use_container_width=True)

        total_stake = sum(p["stake"] for p in plan)
        total_ev = sum(p["ev"] for p in plan)
        util = (total_stake / budget * 100) if budget else 0.0
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Picks", len(plan))
        p2.metric("Total stake", f"${total_stake:,.2f}")
        p3.metric("Budget used", f"{util:.0f}%")
        p4.metric("Expected value", f"${total_ev:+,.2f}")

        b1, b2 = st.columns([1, 3])
        if b1.button("➕ Add plan to Bet Slip", type="primary",
                     use_container_width=True):
            for p in plan:
                add_bet(p["match"], p["outcome"], p["odds"], p["stake"],
                        p["p"], p["edge"])
            st.session_state.alloc_plan = []
            st.success(f"Added {len(plan)} bet(s) to the slip.")
            st.rerun()
        if b2.button("Clear plan", use_container_width=True):
            st.session_state.alloc_plan = []
            st.rerun()
    elif build:
        st.warning("None of the selected matches had a positive-edge value bet "
                   "at the current odds. Try different matches/days or a higher "
                   "risk level.")


# ============================ TAB 2 — BET SLIP ============================= #
with tab_slip:
    st.subheader("Bet Slip")

    if not st.session_state.bets:
        st.info("No bets yet. Analyze a match and add value selections to the slip.")
    else:
        df = pd.DataFrame([{
            "bet_id": b["bet_id"],
            "Match": b["match"],
            "Selection": b["selection"],
            "Odds": b["odds"],
            "Stake": b["stake"],
            "Potential Return": round(b["stake"] * b["odds"], 2),
            "Result": b["result"],
        } for b in st.session_state.bets])

        edited = st.data_editor(
            df,
            hide_index=True,
            use_container_width=True,
            key="slip_editor",
            column_config={
                "bet_id": None,  # hidden
                "Match": st.column_config.TextColumn(disabled=True),
                "Selection": st.column_config.TextColumn(disabled=True),
                "Odds": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                "Stake": st.column_config.NumberColumn(
                    min_value=0.0, step=1.0, format="$%.2f"),
                "Potential Return": st.column_config.NumberColumn(
                    disabled=True, format="$%.2f"),
                "Result": st.column_config.SelectboxColumn(
                    options=["Pending", "Home", "Draw", "Away"], required=True),
            },
        )

        # Sync edits back into session state, then re-settle.
        by_id = {b["bet_id"]: b for b in st.session_state.bets}
        changed = False
        for _, r in edited.iterrows():
            b = by_id.get(int(r["bet_id"]))
            if b is None:
                continue
            new_stake = round(float(r["Stake"]), 2)
            new_result = str(r["Result"])
            if b["stake"] != new_stake or b["result"] != new_result:
                b["stake"] = new_stake
                b["result"] = new_result
                changed = True
        if changed:
            recompute_balance()
            st.rerun()

        # ---- Summary --------------------------------------------------------
        bets = st.session_state.bets
        total_staked = sum(b["stake"] for b in bets)
        settled = [b for b in bets if b["result"] != "Pending"]
        total_returned = sum(
            b["stake"] * b["odds"] for b in settled if b["result"] == b["outcome"])
        settled_staked = sum(b["stake"] for b in settled)
        pnl = total_returned - settled_staked
        roi = (pnl / settled_staked * 100) if settled_staked else 0.0

        st.divider()
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total staked", f"${total_staked:,.2f}")
        s2.metric("Returned (settled)", f"${total_returned:,.2f}")
        s3.metric("Realized P&L", f"${pnl:+,.2f}")
        s4.metric("ROI (settled)", f"{roi:+.1f}%")

        cclear, _ = st.columns([1, 3])
        if cclear.button("🗑️ Clear all bets"):
            st.session_state.bets = []
            recompute_balance()
            st.rerun()


# =========================== TAB 3 — PERFORMANCE =========================== #
with tab_perf:
    st.subheader("Performance")
    bets = st.session_state.bets

    if not bets:
        st.info("Place and settle some bets to see performance analytics.")
    else:
        # Running balance over bets placed (in placement order).
        run_bal = st.session_state.initial_balance
        xs, ys = [0], [run_bal]
        for i, b in enumerate(bets, start=1):
            run_bal -= b["stake"]
            if b["result"] != "Pending" and b["result"] == b["outcome"]:
                run_bal += b["stake"] * b["odds"]
            xs.append(i)
            ys.append(round(run_bal, 2))

        line = go.Figure()
        line.add_scatter(x=xs, y=ys, mode="lines+markers",
                         line=dict(color="#2563eb", width=2),
                         name="Balance")
        line.add_hline(y=st.session_state.initial_balance,
                       line_dash="dash", line_color="#94a3b8",
                       annotation_text="Start")
        line.update_layout(height=320, xaxis_title="Bets placed",
                           yaxis_title="Balance ($)",
                           margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(line, use_container_width=True, key="perf_balance")

        # P&L per bet (settled bets show realized P&L; pending show 0).
        labels, pnls, colors = [], [], []
        for b in bets:
            if b["result"] == "Pending":
                pnl_b = 0.0
            elif b["result"] == b["outcome"]:
                pnl_b = b["stake"] * (b["odds"] - 1)
            else:
                pnl_b = -b["stake"]
            labels.append(f"{b['selection']}\n({b['match']})")
            pnls.append(round(pnl_b, 2))
            colors.append("#16a34a" if pnl_b > 0 else
                          ("#dc2626" if pnl_b < 0 else "#94a3b8"))

        bar = go.Figure()
        bar.add_bar(x=labels, y=pnls, marker_color=colors,
                    text=[f"${v:+.0f}" for v in pnls], textposition="outside")
        bar.update_layout(height=340, yaxis_title="P&L ($)",
                          margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(bar, use_container_width=True, key="perf_pnl")

        # Metrics row.
        settled = [b for b in bets if b["result"] != "Pending"]
        wins = [b for b in settled if b["result"] == b["outcome"]]
        win_rate = (len(wins) / len(settled) * 100) if settled else 0.0
        avg_odds = sum(b["odds"] for b in bets) / len(bets)
        avg_edge = sum(b["edge"] for b in bets) / len(bets)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win rate", f"{win_rate:.0f}%",
                  help=f"{len(wins)}/{len(settled)} settled")
        c2.metric("Avg odds backed", f"{avg_odds:.2f}")
        c3.metric("Avg edge", f"{avg_edge*100:+.2f}%")
        c4.metric("Total bets", len(bets))

st.caption("Mock-money simulator · 1X2 markets · not financial or betting advice.")