"""Paper trading dashboard: this week's model picks and the live CLV / ROI of settled picks.

The ledgers in model/paper/ are refreshed twice a week by the GitHub Actions workflow
(.github/workflows/paper-trading.yml), which runs `python -m model.paper weekly`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model import backtest
from model.data import MARKETS
from model.features import odds_cols
from model.paper import STRATEGIES, read_ledger

st.set_page_config(page_title="Paper Trading", page_icon="📒", layout="wide")

LABELS = {"ou25": "Over/Under 2.5 (top-5 λίγκες)", "1x2": "1X2 (16 λίγκες, control)"}
PICK_COLS = ["date", "league", "home", "away", "pick", "odds", "p_model", "edge"]


@st.cache_data(ttl=600)
def load(name: str) -> pd.DataFrame:
    spec = MARKETS[STRATEGIES[name]["market"]]
    df = read_ledger(name)
    for c in [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]]:
        if not df.empty and c not in df:
            df[c] = np.nan
    return df


def all_predictions(df: pd.DataFrame, market: str, book: str) -> pd.DataFrame:
    """Model vs market probability for every outcome, plus the outcome with the largest edge at `book` odds."""
    spec = MARKETS[market]
    out = df[["date", "league", "home", "away"]].copy()
    for o, s in zip(spec["outcomes"], spec["suffixes"]):
        out[f"Μοντέλο {o}"] = df[f"p_{o}"]
        out[f"Αγορά {o}"] = df[f"mkt_{s}"]
    probs = df[[f"p_{o}" for o in spec["outcomes"]]].to_numpy()
    edges = probs * df[odds_cols(book, market)].to_numpy(dtype=float) - 1
    priced = ~np.isnan(edges).all(axis=1)
    best = np.where(priced, np.nanargmax(np.where(np.isnan(edges), -np.inf, edges), axis=1), 0)
    out["Καλύτερο"] = np.where(priced, np.array(spec["outcomes"])[best], None)
    out["Edge"] = np.where(priced, edges[np.arange(len(df)), best], np.nan)
    return out.sort_values(["date", "Edge"], ascending=[True, False])


def cumulative_chart(bets: pd.DataFrame) -> go.Figure:
    b = bets.sort_values("date")
    fig = go.Figure()
    fig.add_scatter(x=b.date, y=b.profit.cumsum(), name="Κέρδος (μονάδες)", mode="lines")
    fig.add_scatter(x=b.date, y=100 * b.clv.expanding().mean(), name="Μέσο CLV %", mode="lines", yaxis="y2")
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.12),
        yaxis=dict(title="Κέρδος"), yaxis2=dict(title="CLV %", overlaying="y", side="right"),
    )
    return fig


st.title("📒 Paper trading")
st.caption("Κανένα πραγματικό πόνταρισμα. Καταγράφουμε κάθε αγώνα με τις πιθανότητες του μοντέλου και τις αποδόσεις, "
           "και μετά τη λήξη συγκρίνουμε με αποτέλεσμα και closing odds. Το CLV είναι ο δείκτης που μετράει· "
           "το ROI χρειάζεται εκατοντάδες bets για να σημαίνει κάτι.")

for tab, name in zip(st.tabs([LABELS[n] for n in STRATEGIES]), STRATEGIES):
    with tab:
        cfg = STRATEGIES[name]
        market, spec = cfg["market"], MARKETS[cfg["market"]]
        ledger = load(name)
        if ledger.empty:
            st.info("Το ledger είναι ακόμα άδειο. Θα γεμίσει στο επόμενο προγραμματισμένο run.")
            continue

        c1, c2 = st.columns([1, 1])
        edge = c1.slider("Ελάχιστο edge", 0.0, 0.15, float(cfg["edge"]), 0.01, key=f"edge_{name}", format="%.2f")
        book = c2.selectbox("Αποδόσεις", list(backtest.BOOKS), format_func=backtest.BOOKS.get, key=f"book_{name}")
        st.caption(f"Τελευταία ενημέρωση: {ledger.logged_at.max():%Y-%m-%d %H:%M} · "
                   f"{len(ledger)} αγώνες στο ledger")

        today = pd.Timestamp.today().normalize()
        upcoming = ledger[(ledger.date >= today) & ledger[spec["target"]].isna()]
        settled = ledger[ledger[spec["target"]].notna()]

        st.subheader("Επερχόμενα picks")
        picks = backtest.find_bets(upcoming, book, edge, market) if not upcoming.empty else pd.DataFrame()
        if picks.empty:
            st.write("Κανένα pick πάνω από το όριο για τους επόμενους αγώνες.")
        else:
            st.dataframe(picks[PICK_COLS].sort_values("date"), hide_index=True, width="stretch",
                         column_config={"date": st.column_config.DateColumn("Ημερομηνία"),
                                        "odds": st.column_config.NumberColumn(format="%.2f"),
                                        "p_model": st.column_config.NumberColumn("P μοντέλου", format="%.3f"),
                                        "edge": st.column_config.NumberColumn(format="percent")})

        st.subheader("Όλες οι προβλέψεις")
        if upcoming.empty:
            st.write("Δεν υπάρχουν επερχόμενοι αγώνες στο ledger.")
        else:
            preds = all_predictions(upcoming, market, book)
            leagues = st.multiselect("Λίγκες", sorted(preds.league.unique()), key=f"leagues_{name}",
                                     placeholder="Όλες")
            if leagues:
                preds = preds[preds.league.isin(leagues)]
            prob_cols = [c for c in preds if c.startswith(("Μοντέλο", "Αγορά"))]
            st.caption("Πιθανότητα μοντέλου δίπλα στη no-vig πιθανότητα της αγοράς. "
                       "Edge = πιθανότητα μοντέλου × απόδοση − 1, για το αποτέλεσμα με το μεγαλύτερο edge.")
            st.dataframe(
                preds, hide_index=True, width="stretch",
                column_config={"date": st.column_config.DateColumn("Ημερομηνία"),
                               "Edge": st.column_config.NumberColumn(format="percent"),
                               **{c: st.column_config.NumberColumn(format="percent") for c in prob_cols}})

        st.subheader("Αποτελέσματα")
        if settled.empty:
            st.write("Δεν έχει λήξει ακόμα κανένας καταγεγραμμένος αγώνας.")
            continue
        bets = backtest.find_bets(settled, book, edge, market)
        if bets.empty:
            st.write(f"{len(settled)} αγώνες έληξαν, κανένα pick πάνω από το όριο.")
        else:
            s = backtest.summarize(bets).iloc[0]
            m = st.columns(5)
            m[0].metric("Bets", f"{int(s.bets)}")
            m[1].metric("Κέρδος", f"{s.profit:+.2f} μον.")
            m[2].metric("ROI", f"{s['roi_%']:+.1f}%", help=f"± {s['roi_se_%']:.1f}% (1 SE)")
            m[3].metric("Μέσο CLV", f"{s['clv_%']:+.2f}%")
            m[4].metric("Beat close", f"{s['beat_close_%']:.0f}%")
            st.plotly_chart(cumulative_chart(bets), width="stretch")
            with st.expander("Όλα τα settled picks"):
                st.dataframe(bets[PICK_COLS + ["result", "profit", "clv"]].sort_values("date", ascending=False),
                             hide_index=True, width="stretch")
        with st.expander("Ποιότητα πιθανοτήτων και όλα τα όρια edge"):
            st.dataframe(backtest.probability_report(settled, market))
            st.dataframe(backtest.edge_sweep(settled, book, market))
