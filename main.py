import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta
import pytz
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# ═══════════════════════════════════════════════════════════════
#  CONNEXION ALPACA
# ═══════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("ALPACA_API_KEY", "PK4XAYAVTANIMZK6YT5DDNXZXT")
API_SECRET = os.environ.get("ALPACA_SECRET", "9iYFsPF1iv3mvVKDqA4dvF3w42RzGinyryixB8SxopsR")

client      = TradingClient(API_KEY, API_SECRET, paper=True)
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
account     = client.get_account()

print("✅ Connexion Alpaca réussie !")
print(f"💰 Capital : {float(account.cash):.2f}$")
print(f"📊 Portef  : {float(account.portfolio_value):.2f}$")

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION — INSPIRÉE DES MEILLEURS ROBOTS GOLD
# ═══════════════════════════════════════════════════════════════

CAPITAL_TOTAL    = float(account.cash)
MAX_LOTS         = 5              # Max 5 actions par trade
MAX_TRADES_JOUR  = 10             # Max 10 trades par jour
PERTE_MAX_JOUR   = 0.03           # Stop si -3% du capital dans la journée
TP_LONG_PCT      = 0.006          # TP long +0.6%
SL_LONG_PCT      = 0.003          # SL long -0.3%
TP_SHORT_PCT     = 0.006          # TP short +0.6%
SL_SHORT_PCT     = 0.003          # SL short -0.3%
HEURE_OUVERTURE  = 9              # 9h Paris
HEURE_FERMETURE  = 21             # 21h Paris — fermeture forcée
PAUSE_MIN        = 3              # Scan toutes les 3 minutes
TZ_PARIS         = pytz.timezone("Europe/Paris")
TZ_NY            = pytz.timezone("America/New_York")

# Timeframes Alpaca
TF_5MIN  = TimeFrame(5,  TimeFrameUnit.Minute)
TF_1MIN  = TimeFrame(1,  TimeFrameUnit.Minute)
TF_DAY   = TimeFrame(1,  TimeFrameUnit.Day)

# ═══════════════════════════════════════════════════════════════
#  ACTIFS HALAL — OR UNIQUEMENT (comme les meilleurs robots gold)
# ═══════════════════════════════════════════════════════════════

actifs = {
    "GLD":  "Or ETF 🥇",
    "SGOL": "Or physique 🥇",
    "USO":  "Pétrole 🛢️",
}

# ═══════════════════════════════════════════════════════════════
#  COMPTEURS JOURNALIERS
# ═══════════════════════════════════════════════════════════════

trades_jour      = 0
pnl_jour         = 0.0
date_courante    = None

def reset_compteurs():
    global trades_jour, pnl_jour, date_courante
    today = datetime.now(TZ_PARIS).date()
    if date_courante != today:
        date_courante = today
        trades_jour   = 0
        pnl_jour      = 0.0
        print(f"\n🔄 Nouveau jour — compteurs réinitialisés")

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT DONNÉES ALPACA
# ═══════════════════════════════════════════════════════════════

def get_bars(ticker, timeframe, nb=100):
    try:
        now_ny = datetime.now(TZ_NY)
        debut  = now_ny - timedelta(days=5)
        req    = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=timeframe,
            start=debut,
            end=now_ny,
            limit=nb
        )
        bars = data_client.get_stock_bars(req)
        df   = bars.df
        if df is None or len(df) < 10:
            return None
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        df = df[["Open","High","Low","Close","Volume"]].dropna()
        return df
    except Exception as e:
        print(f"  ⚠️ Data {ticker} : {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS SIMPLES ET EFFICACES
#  Inspiré des meilleurs robots gold : EMA + RSI + Bougies
# ═══════════════════════════════════════════════════════════════

def calcul_indicateurs(df):
    # EMA rapides
    df["EMA5"]  = df["Close"].ewm(span=5).mean()
    df["EMA10"] = df["Close"].ewm(span=10).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    # RSI rapide (7 périodes)
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(7).mean()
    perte = (-delta.clip(upper=0)).rolling(7).mean()
    df["RSI"] = 100 - (100 / (gain / (perte + 1e-9) + 1))

    # MACD rapide (5,13,4)
    df["MACD"]     = df["Close"].ewm(span=5).mean() - df["Close"].ewm(span=13).mean()
    df["MACD_SIG"] = df["MACD"].ewm(span=4).mean()

    # Stochastique rapide (5,3)
    lmin = df["Low"].rolling(5).min()
    hmax = df["High"].rolling(5).max()
    df["%K"] = 100 * (df["Close"] - lmin) / (hmax - lmin + 1e-9)
    df["%D"] = df["%K"].rolling(3).mean()

    # Bollinger Bands (20)
    df["BB_MID"] = df["Close"].rolling(20).mean()
    df["BB_STD"] = df["Close"].rolling(20).std()
    df["BB_UP"]  = df["BB_MID"] + 2 * df["BB_STD"]
    df["BB_LO"]  = df["BB_MID"] - 2 * df["BB_STD"]

    # ATR (7)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"]  - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(7).mean()

    # Volume moyen
    df["VOL_MOY"] = df["Volume"].rolling(20).mean()

    return df

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION PATTERNS BOUGIES
# ═══════════════════════════════════════════════════════════════

def detecter_pattern(df):
    if len(df) < 3:
        return None
    d, d1, d2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    def bougie(r):
        o, c = float(r["Open"]), float(r["Close"])
        h, l = float(r["High"]), float(r["Low"])
        corps = abs(c - o)
        if corps < 1e-9: corps = 1e-9
        return corps, h - max(o,c), min(o,c) - l, o, c

    c0, mh0, mb0, o0, cl0 = bougie(d)
    c1, mh1, mb1, o1, cl1 = bougie(d1)
    c2, mh2, mb2, o2, cl2 = bougie(d2)

    # Marteau → LONG (mèche basse ≥ 2× corps)
    if mb0 >= 2 * c0 and mh0 < c0:
        return "MARTEAU"
    # Marteau inverse → SHORT (mèche haute ≥ 2× corps)
    if mh0 >= 2 * c0 and mb0 < c0:
        return "MARTEAU_INV"
    # Engulfing haussier → LONG
    if cl0 > o0 and cl1 < o1 and cl0 > o1 and o0 < cl1:
        return "ENGULFING_HAUSSIER"
    # Engulfing baissier → SHORT
    if cl0 < o0 and cl1 > o1 and cl0 < o1 and o0 > cl1:
        return "ENGULFING_BAISSIER"
    # Étoile du matin → LONG
    if cl2 < o2 and c1 < c2*0.3 and cl0 > o0 and cl0 > (o2+cl2)/2:
        return "ETOILE_MATIN"
    # Étoile du soir → SHORT
    if cl2 > o2 and c1 < c2*0.3 and cl0 < o0 and cl0 < (o2+cl2)/2:
        return "ETOILE_SOIR"
    # Doji → indécision
    if c0 < (float(d["High"]) - float(d["Low"])) * 0.1:
        return "DOJI"
    return None

# ═══════════════════════════════════════════════════════════════
#  SIGNAL LONG — CONDITIONS SOUPLES MAIS CONFIRMÉES
# ═══════════════════════════════════════════════════════════════

def signal_long(ticker):
    df = get_bars(ticker, TF_5MIN, nb=80)
    if df is None or len(df) < 30:
        return None
    df = calcul_indicateurs(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix   = float(d["Close"])
    rsi    = float(d["RSI"])
    k      = float(d["%K"])
    kp     = float(d1["%K"])
    macd   = float(d["MACD"])
    msig   = float(d["MACD_SIG"])
    mp     = float(d1["MACD"])
    e5     = float(d["EMA5"])
    e10    = float(d["EMA10"])
    e20    = float(d["EMA20"])
    e5p    = float(d1["EMA5"])
    e10p   = float(d1["EMA10"])
    bbl    = float(d["BB_LO"])
    bbm    = float(d["BB_MID"])
    vol    = float(d["Volume"])
    volm   = float(d["VOL_MOY"])
    pattern = detecter_pattern(df)

    score = 0
    raison = []

    # ── CONDITIONS LONG (souples) ──────────────────────────────

    # RSI survendu ou neutre
    if rsi < 50:
        score += 2
        raison.append(f"RSI={rsi:.0f}")

    # Stoch remonte
    if k > kp and k < 70:
        score += 2
        raison.append(f"STOCH↑{k:.0f}")

    # MACD haussier
    if macd > msig or macd > mp:
        score += 2
        raison.append("MACD↑")

    # EMA5 > EMA10 (tendance court terme haussière)
    if e5 > e10:
        score += 1
        raison.append("EMA5>10")

    # Prix près de la bande basse ou sous moyenne
    if prix <= bbl * 1.02 or prix < bbm:
        score += 2
        raison.append("BB_LO")

    # Pattern bougie haussier
    if pattern in ["MARTEAU", "ENGULFING_HAUSSIER", "ETOILE_MATIN"]:
        score += 3
        raison.append(f"📊{pattern}")

    # Volume fort
    if vol > volm * 0.8:
        score += 1
        raison.append("VOL✓")

    # ── SEUIL : score ≥ 5 (très souple) ──────────────────────
    if score < 5:
        return None

    q = max(1, min(int((CAPITAL_TOTAL * 0.02) / prix), MAX_LOTS))
    return {
        "ticker":    ticker,
        "direction": "LONG",
        "signal":    " | ".join(raison),
        "score":     score,
        "prix":      prix,
        "sl":        round(prix * (1 - SL_LONG_PCT), 2),
        "tp":        round(prix * (1 + TP_LONG_PCT), 2),
        "quantite":  q,
        "capital":   round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL SHORT — 7 CONDITIONS SOUPLES
# ═══════════════════════════════════════════════════════════════

def signal_short(ticker):
    df = get_bars(ticker, TF_5MIN, nb=80)
    if df is None or len(df) < 30:
        return None
    df = calcul_indicateurs(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix   = float(d["Close"])
    rsi    = float(d["RSI"])
    k      = float(d["%K"])
    kp     = float(d1["%K"])
    macd   = float(d["MACD"])
    msig   = float(d["MACD_SIG"])
    mp     = float(d1["MACD"])
    e5     = float(d["EMA5"])
    e10    = float(d["EMA10"])
    e5p    = float(d1["EMA5"])
    e10p   = float(d1["EMA10"])
    bbu    = float(d["BB_UP"])
    bbm    = float(d["BB_MID"])
    vol    = float(d["Volume"])
    volm   = float(d["VOL_MOY"])
    pattern = detecter_pattern(df)

    score = 0
    raison = []

    # RSI suracheté ou élevé
    if rsi > 50:
        score += 2
        raison.append(f"RSI={rsi:.0f}")

    # Stoch redescend
    if k < kp and k > 30:
        score += 2
        raison.append(f"STOCH↓{k:.0f}")

    # MACD baissier
    if macd < msig or macd < mp:
        score += 2
        raison.append("MACD↓")

    # EMA5 < EMA10 (tendance court terme baissière)
    if e5 < e10:
        score += 1
        raison.append("EMA5<10")

    # Prix près de la bande haute ou au-dessus moyenne
    if prix >= bbu * 0.98 or prix > bbm:
        score += 2
        raison.append("BB_UP")

    # Pattern bougie baissier
    if pattern in ["MARTEAU_INV", "ENGULFING_BAISSIER", "ETOILE_SOIR"]:
        score += 3
        raison.append(f"📊{pattern}")

    # Volume fort
    if vol > volm * 0.8:
        score += 1
        raison.append("VOL✓")

    # ── SEUIL : score ≥ 5 ────────────────────────────────────
    if score < 5:
        return None

    q = max(1, min(int((CAPITAL_TOTAL * 0.02) / prix), MAX_LOTS))
    return {
        "ticker":    ticker,
        "direction": "SHORT",
        "signal":    " | ".join(raison),
        "score":     score,
        "prix":      prix,
        "sl":        round(prix * (1 + SL_SHORT_PCT), 2),
        "tp":        round(prix * (1 - TP_SHORT_PCT), 2),
        "quantite":  q,
        "capital":   round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════

def passer_ordre(signal):
    global trades_jour
    try:
        if signal["direction"] == "LONG":
            ordre = MarketOrderRequest(
                symbol=signal["ticker"],
                qty=signal["quantite"],
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=signal["tp"]),
                stop_loss=StopLossRequest(stop_price=signal["sl"])
            )
        else:
            ordre = MarketOrderRequest(
                symbol=signal["ticker"],
                qty=signal["quantite"],
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=signal["tp"]),
                stop_loss=StopLossRequest(stop_price=signal["sl"])
            )

        result = client.submit_order(ordre)
        trades_jour += 1
        emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
        print(f"\n  {emoji} {signal['direction']} {signal['ticker']} | Score:{signal['score']}")
        print(f"     {signal['quantite']}x{signal['prix']:.2f}$ | TP:{signal['tp']:.2f}$ SL:{signal['sl']:.2f}$")
        print(f"     Signaux: {signal['signal']}")
        print(f"     Trades aujourd'hui: {trades_jour}/{MAX_TRADES_JOUR}")
        return result.id

    except Exception as e:
        print(f"  ❌ Ordre {signal['ticker']} : {e}")
        return None

def fermeture_forcee():
    print("\n🔔 21h00 — FERMETURE FORCÉE")
    try:
        positions = client.get_all_positions()
        if not positions:
            print("  ℹ️ Aucune position")
            return
        for pos in positions:
            try:
                side = OrderSide.SELL if "long" in str(pos.side) else OrderSide.BUY
                client.submit_order(MarketOrderRequest(
                    symbol=pos.symbol,
                    qty=abs(int(float(pos.qty))),
                    side=side,
                    time_in_force=TimeInForce.DAY
                ))
                print(f"  ✅ {pos.symbol} fermée")
                time.sleep(0.5)
            except Exception as e:
                print(f"  ❌ {pos.symbol} : {e}")
    except Exception as e:
        print(f"  ❌ {e}")

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS OUVERTES
# ═══════════════════════════════════════════════════════════════

positions_robot = {}   # ticker → info

def sync_positions():
    try:
        reelles = {p.symbol for p in client.get_all_positions()}
        for t in list(positions_robot.keys()):
            if t not in reelles:
                print(f"  ✅ {t} fermée (TP/SL Alpaca)")
                del positions_robot[t]
    except Exception as e:
        print(f"  ⚠️ Sync : {e}")

def afficher_positions():
    if not positions_robot:
        return
    try:
        pa = {p.symbol: p for p in client.get_all_positions()}
        for ticker, pos in positions_robot.items():
            if ticker in pa:
                prix_actuel = float(pa[ticker].current_price)
                entree      = pos["prix_entree"]
                gain        = ((prix_actuel - entree) / entree) * 100
                if pos["direction"] == "SHORT":
                    gain = -gain
                print(f"   {'🟢' if pos['direction']=='LONG' else '🔴'} {ticker} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain:+.2f}%")
    except:
        pass

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def get_heure():
    now = datetime.now(TZ_PARIS)
    return now.hour + now.minute/60, now.strftime("%H:%M")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot():
    print("\n🤖 ROBOT HALAL V8 — INSPIRÉ DES MEILLEURS ROBOTS GOLD")
    print("=" * 65)
    print(f"💰 Capital          : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs           : GLD | SGOL | USO")
    print(f"🟢 LONG             : EMA + RSI + MACD + Bougies | Score ≥5")
    print(f"🔴 SHORT            : EMA + RSI + MACD + Bougies | Score ≥5")
    print(f"🎯 TP/SL            : +0.6% / -0.3% (serré comme les pros)")
    print(f"📦 Max lots/trade   : {MAX_LOTS}")
    print(f"📊 Max trades/jour  : {MAX_TRADES_JOUR}")
    print(f"🛡️  Perte max/jour   : {PERTE_MAX_JOUR*100:.0f}%")
    print(f"⏱️  Scan             : toutes les {PAUSE_MIN} minutes")
    print(f"🕘 Fermeture forcée : 21h00")
    print("=" * 65)

    fermeture_faite = False
    cycle = 0

    while True:
        h, heure_str = get_heure()

        # ── FERMETURE 21h ──────────────────────────────────────
        if h >= HEURE_FERMETURE:
            if not fermeture_faite:
                fermeture_forcee()
                positions_robot.clear()
                fermeture_faite = True
            print(f"🌙 {heure_str} — Fermeture 21h | Reprise 9h")
            time.sleep(PAUSE_MIN * 60)
            continue

        if h >= HEURE_OUVERTURE:
            fermeture_faite = False

        if h < HEURE_OUVERTURE:
            print(f"⏳ {heure_str} — Marché fermé")
            time.sleep(PAUSE_MIN * 60)
            continue

        cycle += 1
        reset_compteurs()

        print(f"\n{'='*65}")
        print(f"🔄 Cycle {cycle} | 🕐 {heure_str} | Trades: {trades_jour}/{MAX_TRADES_JOUR}")

        # ── INFO COMPTE ────────────────────────────────────────
        try:
            acc = client.get_account()
            cash    = float(acc.cash)
            portef  = float(acc.portfolio_value)
            pnl_jour = portef - CAPITAL_TOTAL
            print(f"💰 Cash:{cash:.2f}$ | Portef:{portef:.2f}$ | PnL jour:{pnl_jour:+.2f}$")

            # Stop si perte max journalière atteinte
            if pnl_jour < -(CAPITAL_TOTAL * PERTE_MAX_JOUR):
                print(f"🛑 PERTE MAX JOURNALIÈRE ATTEINTE ({pnl_jour:.2f}$) — Pause jusqu'à 21h")
                fermeture_forcee()
                positions_robot.clear()
                time.sleep(PAUSE_MIN * 60)
                continue
        except Exception as e:
            print(f"  ⚠️ Compte : {e}")

        # ── SYNC POSITIONS ─────────────────────────────────────
        sync_positions()
        afficher_positions()

        # ── CHERCHER SIGNAUX ───────────────────────────────────
        if trades_jour < MAX_TRADES_JOUR:
            print(f"\n🔍 Scan signaux...")

            try:
                pos_reelles = {p.symbol for p in client.get_all_positions()}
            except:
                pos_reelles = set()

            for ticker in actifs:
                # Max 1 position par actif à la fois
                if ticker in pos_reelles:
                    continue

                # Cherche LONG
                try:
                    sig = signal_long(ticker)
                    if sig:
                        print(f"  🟢 LONG {ticker} score {sig['score']}")
                        oid = passer_ordre(sig)
                        if oid:
                            positions_robot[ticker] = {
                                "direction":   "LONG",
                                "prix_entree": sig["prix"],
                                "order_id":    oid,
                            }
                        continue  # pas de short si long trouvé
                except Exception as e:
                    print(f"  ⚠️ {ticker} long : {e}")

                # Cherche SHORT
                try:
                    sig = signal_short(ticker)
                    if sig:
                        print(f"  🔴 SHORT {ticker} score {sig['score']}")
                        oid = passer_ordre(sig)
                        if oid:
                            positions_robot[ticker] = {
                                "direction":   "SHORT",
                                "prix_entree": sig["prix"],
                                "order_id":    oid,
                            }
                except Exception as e:
                    print(f"  ⚠️ {ticker} short : {e}")

                time.sleep(1)

        else:
            print(f"📦 Max trades jour atteint ({MAX_TRADES_JOUR})")

        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Positions: {len(positions_robot)} | Trades jour: {trades_jour}/{MAX_TRADES_JOUR}")
        for t, p in positions_robot.items():
            print(f"   {'🟢' if p['direction']=='LONG' else '🔴'} {t} | {p['direction']} | {p['prix_entree']:.2f}$")

        print(f"\n⏳ Prochain scan dans {PAUSE_MIN}min...")
        time.sleep(PAUSE_MIN * 60)

# LANCEMENT
lancer_robot()
