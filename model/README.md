# Football 1X2 model — walk-forward backtest

Πηγή: football-data.co.uk (16 λίγκες, 2012/13 → σήμερα, ~79k αγώνες με αποτελέσματα, σουτ και αποδόσεις).

```
model/
  data.py       κατέβασμα + ενιαίο schema (Pinnacle / Betfair / μέσος όρος / max, pre-match και closing)
  features.py   Elo + EWM φόρμα (γκολ, σουτ στην εστία, xG), υπολογισμένα ΠΡΙΝ από κάθε αγώνα
  xg.py         xG ανά αγώνα από Understat (top-5 λίγκες, 2014/15+)
  train.py      walk-forward: κάθε σεζόν εκπαιδεύεται μόνο σε προηγούμενες
  backtest.py   log loss vs αγορά, επιλογή value bets, ROI ± SE, CLV
  run.py        backtest CLI
  predict.py    value picks για τους επόμενους αγώνες (fixtures.csv)
```

## Εκτέλεση (από τον φάκελο `Foot_BET_proj`)

```
python -m model.run                                         # stats-only
python -m model.run --features stats+market --book avg --edge 0.05
python -m model.run --leagues G1 T1 B1 --kind hgb
python -m model.predict --features stats+market --book avg --edge 0.04
python -m model.run --market ou25 --features stats+xg+market --leagues E0 SP1 D1 I1 F1
python -m model.predict --market ou25 --features stats+market --leagues E0 SP1 D1 I1 F1
```

`--market`: `1x2` ή `ou25`. Τα features `xg` υπάρχουν μόνο για E0, SP1, D1, I1, F1.

`--book` = οι αποδόσεις στις οποίες θα πόνταρες πραγματικά: `avg` (ρεαλιστικό), `b365`, `ps`, `max` (αισιόδοξο).

## Τι μετράμε

- **Log loss vs closing**: αν το μοντέλο δεν πλησιάζει τη closing αγορά, δεν έχει edge.
- **CLV** = απόδοση που πήρες × no-vig closing πιθανότητα − 1. Θετικό CLV σε χιλιάδες bets είναι
  το μόνο αξιόπιστο σημάδι edge· το ROI έχει πολύ θόρυβο (δες `roi_se_%`).

## Αποτελέσματα baseline (out-of-sample 2016/17 → 2026/27, ~56k αγώνες)

| Μοντέλο | Log loss | Bets (edge>5%) | ROI | CLV |
|---|---|---|---|---|
| Closing αγορά (όριο) | 0.985 | | | |
| Pre-match αγορά | 0.988 | | | |
| stats (Elo + φόρμα) @ avg | 1.006 | 30k | −12.6% | −6.7% |
| stats+market @ avg | 0.988 | 2.6k | +2.3% ± 2.9 | −4.7% |
| stats+market @ max | 0.988 | 17.5k | +4.0% ± 1.2 | +1.7% |
| μόνο market @ max | 0.988 | 9.1k | +5.3% ± 1.7 | +2.8% |

Συμπέρασμα: τα δημόσια stats (Elo, γκολ, σουτ) δεν προσθέτουν πληροφορία πέρα από τις αποδόσεις.
Το θετικό αποτέλεσμα στο `max` οφείλεται στο line shopping, όχι στο μοντέλο (το market-only είναι καλύτερο),
και το Max του football-data περιέχει τιμές που συχνά δεν είναι διαθέσιμες στην πράξη.

## xG + Over/Under 2.5 (top-5 λίγκες, 2016/17 → σήμερα, ~18k αγώνες)

xG από Understat (`xg.py`, αντιστοίχιση ονομάτων ομάδων αυτόματα μέσω ημερομηνίας + σκορ, 100% κάλυψη).
Όλα τα μοντέλα στους ίδιους αγώνες, edge > 5%. Πλήρης πίνακας: logreg ≥ hgb παντού (το hgb κάνει overfit).

| Αγορά | Features | Log loss (αγορά pre / close) | ROI @ avg | CLV @ avg |
|---|---|---|---|---|
| 1X2 | stats | 0.981 (0.965 / 0.962) | −9.3% | −5.7% |
| 1X2 | stats+xg | 0.979 | −7.9% | −5.2% |
| 1X2 | stats+market | 0.965 | 0.0% ± 3.0 | −4.1% |
| 1X2 | stats+xg+market | 0.966 | −0.9% ± 2.6 | −4.2% |
| O/U | stats | 0.680 (0.670 / 0.667) | −8.7% | −5.7% |
| O/U | stats+xg | 0.679 | −7.4% | −5.6% |
| O/U | stats+market | 0.670 | +1.0% ± 7.3 (223 bets) | +2.6% |
| O/U | stats+xg+market | 0.670 | −1.0% ± 4.1 | −2.5% |

Συμπέρασμα: το xG βελτιώνει λίγο το καθαρά στατιστικό μοντέλο, αλλά απέχει πολύ από την αγορά και δεν προσθέτει
τίποτα όταν δίνονται και οι αποδόσεις. Το O/U stats+market έχει θετικό CLV αλλά σε μόλις 223 bets — υπόθεση προς
έλεγχο με paper trading, όχι εύρημα. O/U closing odds υπάρχουν μόνο από 2019/20.

## Paper trading (`paper.py`)

Καταγράφει **κάθε** αγώνα που βαθμολογεί το μοντέλο (όχι μόνο τα picks): πιθανότητες μοντέλου και αποδόσεις
(avg / max / b365 / ps / bfe) τη στιγμή της καταγραφής. Κρατιέται η πρώτη καταγραφή κάθε αγώνα, δηλαδή η τιμή που
θα είχες πάρει. Μετά τον αγώνα, το `settle` προσθέτει αποτέλεσμα και no-vig closing πιθανότητες, ώστε το CLV να
μετριέται σε οποιοδήποτε όριο edge.

| Strategy | Αγορά | Λίγκες | Features | Edge pick |
|---|---|---|---|---|
| `ou25` (υπόθεση) | O/U 2.5 | top-5 | stats+market | > 6% |
| `1x2` (control) | 1X2 | όλες οι 16 | stats+market | > 5% |

```
python -m model.paper weekly        # settle + log + report
python -m model.paper report --strategy ou25 --edge 0.04
```

Ledgers: `model/paper/ledger_ou25.csv`, `ledger_1x2.csv`. Log του προγραμματισμένου τρεξίματος: `model/paper/weekly.log`.
Ο κύκλος τρέχει αυτόματα στο GitHub Actions (`.github/workflows/paper-trading.yml`), Τρίτη και Παρασκευή 07:00 UTC,
και κάνει commit τα ledgers πίσω στο repo. Χειροκίνητα: Actions → paper-trading → Run workflow.
Το `paper_weekly.ps1` είναι η τοπική εναλλακτική (Windows Task Scheduler).

## App

FastAPI (`api/`, συμβόλαιο στο `openapi.yaml`) + React (`web/`), ένα container. Τοπικά:
`uvicorn --factory api.main:from_env --port 8000` και σε δεύτερο τερματικό `cd web && npm ci && npm run dev`.
Deploy: το Toft χτίζει το `Dockerfile` από το GitHub. Μετά από κάθε run του workflow το app δείχνει τα νέα ledgers
(στο επόμενο deploy, μέχρι τη φάση 3 του `docs/rebuild-plan.md`).

Η σελίδα **Paper trading** δείχνει τα επερχόμενα picks και ROI / CLV των settled picks, διαβάζοντας τα ledgers.
Η σελίδα **Optimizer** (λογική στα `optimizer.py` και `live_odds.py`) παίρνει ένα ποσό και
το μοιράζει σε τρία σενάρια: μονά, σύστημα 2/4 και παρολί 3 & 4, με βάρη ανάλογα του Kelly κάθε κουπονιού.
Για κάθε σενάριο δείχνει αναμενόμενο κέρδος κατά το μοντέλο **και** κατά την αγορά, P(κέρδος), P(χάνεις όλο το ποσό)
και κατανομή από 20.000 προσομοιώσεις. Κάθε επιλογή έχει και «ελάχ. απόδοση»: παίξε μόνο σε αυτή ή καλύτερη.

Live αποδόσεις: The Odds API (δωρεάν κλειδί, 500 credits/μήνα, 2 credits ανά λίγκα). Το κλειδί είναι μόνο στον
server, ως μεταβλητή περιβάλλοντος `ODDS_API_KEY` (secret στο Toft, ποτέ στο repo).
Χωρίς κλειδί χρησιμοποιείται το snapshot του football-data από το τελευταίο run.

Ρεαλιστικός ρυθμός: στο backtest το O/U με edge > 6% βγάζει ~13 picks/σεζόν (2 στο Σεπ–Οκτ 2026). Το CLV σε όλα τα
όρια (που μετριέται σε ~120 αγώνες/εβδομάδα) θα δώσει σήμα πολύ νωρίτερα από το ROI των picks.

## Σημειώσεις

- Pinnacle odds σταματούν στο football-data από τέλη 2025· fallback σε Betfair Exchange → μέσο όρο.
- Οι ψευδείς τιμές του exchange (overround εκτός [0.97, 1.15]) αγνοούνται.
- Επόμενα βήματα για πραγματικό edge: xG/lineups/τραυματισμοί (πληροφορία που η αγορά ενσωματώνει αργά),
  αγορές O/U και Asian handicap, χρονισμός (νωρίς στη γραμμή) και καταγραφή CLV σε live paper trading.
