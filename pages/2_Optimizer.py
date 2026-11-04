"""Coupon optimizer: split a budget across this week's selections in three scenarios
(singles, a 2-from-N system, accumulators), with live odds from The Odds API when a key is set.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model import live_odds, optimizer as opt
from model.paper import STRATEGIES, read_ledger

st.set_page_config(page_title="Optimizer", page_icon="🎯", layout="wide")

MARKET_LABELS = {"ou25": "Over/Under 2.5", "1x2": "1X2"}
FALLBACK_BOOKS = {"avg": "Μέσος όρος αγοράς", "max": "Καλύτερη τιμή", "b365": "Bet365"}
TOP5 = ["E0", "SP1", "D1", "I1", "F1"]


def odds_api_key() -> str:
    try:
        return st.secrets["ODDS_API_KEY"]
    except Exception:
        return ""


@st.cache_data(ttl=600)
def upcoming(name: str) -> pd.DataFrame:
    df = read_ledger(name)
    return df[df.date >= pd.Timestamp.today().normalize()] if not df.empty else df


def pnl_histogram(pnl: np.ndarray) -> go.Figure:
    fig = go.Figure(go.Histogram(x=pnl, nbinsx=60, histnorm="probability"))
    fig.add_vline(x=0, line_dash="dash")
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Κέρδος / ζημιά (€)",
                      yaxis_title="Πιθανότητα", yaxis_tickformat=".0%", bargap=0.05)
    return fig


st.title("🎯 Optimizer κουπονιού")
st.warning("Ανάλυση, όχι συμβουλή. Στο backtest το μοντέλο **δεν** κέρδισε την αγορά, και τα παρολί "
           "πολλαπλασιάζουν το περιθώριο του bookmaker. Κοίτα πάντα και τη στήλη «κατά την αγορά».")

c = st.columns(4)
budget = c[0].number_input("Ποσό (€)", min_value=5.0, max_value=100_000.0, value=50.0, step=5.0)
markets = c[1].multiselect("Αγορές", list(STRATEGIES), default=list(STRATEGIES), format_func=MARKET_LABELS.get)
min_prob = c[2].slider("Ελάχ. πιθανότητα επιλογής", 0.20, 0.80, 0.35, 0.05,
                       help="Κόβει τα μακρινά φαβορί-αουτσάιντερ· τα παρολί με χαμηλές πιθανότητες σχεδόν πάντα χάνουν.")
min_edge = c[3].slider("Ελάχ. edge", -0.10, 0.15, 0.0, 0.01, format="%.2f",
                       help="Edge = πιθανότητα μοντέλου × απόδοση − 1. Αρνητικό = δέχεσαι επιλογές με αρνητική αναμενόμενη τιμή.")
if not markets:
    st.stop()

# --------------------------------------------------------------------------- odds source
st.subheader("1 · Αποδόσεις")
key = odds_api_key()
source = st.radio("Πηγή", ["Live (The Odds API)", "Snapshot football-data"], horizontal=True,
                  index=0 if key else 1)
live, bookmakers, fallback_book = None, None, "avg"
if source.startswith("Live"):
    if not key:
        key = st.text_input("The Odds API key", type="password",
                            help="Δωρεάν κλειδί (500 credits/μήνα) στο the-odds-api.com. "
                                 "Στο Streamlit Cloud βάλ' το στα Secrets ως ODDS_API_KEY.")
    available = sorted({lg for m in markets for lg in STRATEGIES[m]["leagues"]})
    leagues = st.multiselect("Λίγκες για live αποδόσεις", available, default=[lg for lg in TOP5 if lg in available],
                             help="Κάθε λίγκα κοστίζει 2 credits (1X2 + Over/Under).")
    if st.button(f"Φέρε live αποδόσεις ({2 * len(leagues)} credits)", disabled=not (key and leagues)):
        fixtures = pd.concat([upcoming(m) for m in STRATEGIES])
        try:
            with st.spinner("Κατεβάζω αποδόσεις..."):
                raw, quota = live_odds.fetch(key, leagues)
                mapped, unmatched = live_odds.match_to_fixtures(raw, fixtures)
            st.session_state.live = {"df": mapped, "quota": quota, "unmatched": unmatched,
                                     "at": pd.Timestamp.now().floor("s"), "events": raw[["api_home", "api_away"]].drop_duplicates().shape[0]}
        except Exception as exc:
            st.error(f"Η κλήση στο The Odds API απέτυχε: {exc}")
    cached = st.session_state.get("live")
    if cached:
        live = cached["df"]
        st.caption(f"Live αποδόσεις από {cached['at']:%H:%M:%S} · {cached['events']} αγώνες, "
                   f"{live[opt.MATCH_KEY].drop_duplicates().shape[0]} αντιστοιχίστηκαν με τις προβλέψεις · "
                   f"credits που απομένουν: {cached['quota'].get('remaining', '?')}")
        if cached["unmatched"]:
            with st.expander(f"{len(cached['unmatched'])} αγώνες χωρίς πρόβλεψη μοντέλου"):
                st.write(", ".join(cached["unmatched"]))
        all_books = sorted(live.bookmaker.unique())
        bookmakers = st.multiselect("Bookmakers όπου μπορείς να παίξεις", all_books, default=all_books,
                                    help="Η απόδοση κάθε επιλογής είναι η καλύτερη ανάμεσα σε αυτούς.")
    else:
        st.info("Πάτα «Φέρε live αποδόσεις». Μέχρι τότε χρησιμοποιείται το snapshot του football-data.")
if live is None:
    fallback_book = st.selectbox("Αποδόσεις snapshot", list(FALLBACK_BOOKS), format_func=FALLBACK_BOOKS.get,
                                 help="Από το fixtures.csv του football-data, όπως καταγράφηκε στο τελευταίο run.")

# --------------------------------------------------------------------------- selections
ledgers = {m: upcoming(m) for m in markets}
if all(df.empty for df in ledgers.values()):
    st.info("Δεν υπάρχουν επερχόμενοι αγώνες στα ledgers. Θα γεμίσουν στο επόμενο run (Τρίτη/Παρασκευή).")
    st.stop()
sel = opt.selections({m: df for m, df in ledgers.items() if not df.empty}, live, fallback_book, bookmakers)
legs = opt.candidate_legs(sel, min_prob, min_edge)

st.subheader("2 · Επιλογές")
if legs.empty:
    st.info("Καμία επιλογή δεν περνά τα φίλτρα. Χαμήλωσε το ελάχιστο edge ή την ελάχιστη πιθανότητα.")
    st.stop()
st.caption("Η καλύτερη επιλογή ανά αγώνα (ποτέ 1X2 και Over/Under του ίδιου αγώνα μαζί), ταξινομημένες κατά edge. "
           "Τα σενάρια χρησιμοποιούν τις πρώτες.")
st.dataframe(
    legs[["date", "league", "home", "away", "pick", "odds", "bookmaker", "p_model", "p_market", "edge"]],
    hide_index=True, width="stretch",
    column_config={"date": st.column_config.DateColumn("Ημερομηνία"),
                   "pick": "Επιλογή", "odds": st.column_config.NumberColumn("Απόδοση", format="%.2f"),
                   "p_model": st.column_config.NumberColumn("P μοντέλου", format="percent"),
                   "p_market": st.column_config.NumberColumn("P αγοράς", format="percent"),
                   "edge": st.column_config.NumberColumn("Edge", format="percent")})

# --------------------------------------------------------------------------- scenarios
st.subheader("3 · Σενάρια")
st.caption("Το ποσό μοιράζεται ανάλογα με το Kelly κάθε κουπονιού (ισόποσα αν κανένα δεν έχει θετική αναμενόμενη τιμή). "
           "Οι πιθανότητες προκύπτουν από 20.000 προσομοιώσεις με τις πιθανότητες του μοντέλου.")
results = {}
for name, sc in opt.scenarios(len(legs)).items():
    coupons = opt.build_coupons(legs, sc["combos"], budget)
    if not coupons.empty:
        pnl = opt.simulate(legs, coupons)
        results[name] = (sc["title"], coupons, pnl, opt.risk_summary(coupons, pnl))

if not results:
    st.info("Χρειάζονται τουλάχιστον 1 επιλογή για μονά, 2 για σύστημα και 3 για παρολί.")
    st.stop()

summary = pd.DataFrame({t: s for t, _, _, s in results.values()}).T
fmt = {c: "{:.0%}" if c.startswith("P(") else "{:+.2f} €" for c in summary.columns}
fmt["Ποντάρισμα"] = "{:.2f} €"
st.dataframe(summary.style.format(fmt), width="stretch")

for tab, (title, coupons, pnl, s) in zip(st.tabs([r[0] for r in results.values()]), results.values()):
    with tab:
        m = st.columns(4)
        m[0].metric("Αναμ. κέρδος (μοντέλο)", f"{s['Αναμ. κέρδος (μοντέλο)']:+.2f} €")
        m[1].metric("Αναμ. κέρδος (αγορά)", f"{s['Αναμ. κέρδος (αγορά)']:+.2f} €")
        m[2].metric("P(κέρδος)", f"{s['P(κέρδος)']:.0%}")
        m[3].metric("P(χάνεις όλο το ποσό)", f"{s['P(χάνεις όλο το ποσό)']:.0%}")
        st.dataframe(
            coupons[["coupon", "odds", "p_model", "p_market", "ev_model", "ev_market", "stake", "payout"]],
            hide_index=True, width="stretch",
            column_config={"coupon": st.column_config.TextColumn("Κουπόνι", width="large"),
                           "odds": st.column_config.NumberColumn("Απόδοση", format="%.2f"),
                           "p_model": st.column_config.NumberColumn("P μοντέλου", format="percent"),
                           "p_market": st.column_config.NumberColumn("P αγοράς", format="percent"),
                           "ev_model": st.column_config.NumberColumn("EV μοντέλου", format="percent"),
                           "ev_market": st.column_config.NumberColumn("EV αγοράς", format="percent"),
                           "stake": st.column_config.NumberColumn("Ποσό", format="%.2f €"),
                           "payout": st.column_config.NumberColumn("Επιστροφή αν κερδίσει", format="%.2f €")})
        st.plotly_chart(pnl_histogram(pnl), width="stretch")
