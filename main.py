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
from alpaca.data.timeframe import TimeFrame

# ═══════════════════════════════════════════════════════════════
#  CONNEXION ALPACA
# ═══════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("ALPACA_API_KEY", "PK4XAYAVTANIMZK6YT5DDNXZXT")
API_SECRET = os.environ.get("ALPACA_SECRET", "9iYFsPF1iv3mvVKDqA4dvF3w42RzGinyryixB8SxopsR")

# Client trading
client = TradingClient(API_KEY, API_SECRET, paper=True)

# ✅ Client data Alpaca — pas de rate limit !
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

account = client.get_account()
print("✅ Connexion Alpaca réussie !")
print(f"💰 Capital disponible : {float(account.cash):.2f}$")
print(f"📊 Valeur portefeuille : {float(account.portfolio_value):.2f}$")

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CAPITAL_TOTAL        = float(account.cash)
MAX_LOTS             = 5           # ✅ Maximum 5 actions par trade
MAX_LONG_SWING       = 4           # Max 4 swing long
MAX_SCALP            = 3           # Max 3 scalp/short simultanés
ATR_MULT_SWING       = 1.5
SL_SCALP_PCT         = 0.004       # SL scalp -0.4%
TP_SCALP_PCT         = 0.008       # TP scalp +0.8%
SL_SHORT_PCT         = 0.005       # SL short -0.5%
TP_SHORT_PCT         = 0.010       # TP short +1.0%
MECHE_MULTIPLICATEUR = 2           # Mèche ≥ 2x corps
HEURE_OUVERTURE      = 9
HEURE_FIN_SCALP      = 17
HEURE_FERMETURE      = 21
PAUSE_SWING_MIN      = 15
PAUSE_SCALP_MIN      = 5
TZ_PARIS             = pytz.timezone("Europe/Paris")
TZ_NY                = pytz.timezone("America/New_York")

# ═══════════════════════════════════════════════════════════════
#  3 ACTIFS HALAL
# ═══════════════════════════════════════════════════════════════

actifs = {
    "GLD":  "Or (XAUUSD)",
    "SGOL": "Or physique (GC)",
    "USO":  "Pétrole (CL)",
}

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT VIA ALPACA DATA — ZÉRO RATE LIMIT ✅
# ═══════════════════════════════════════════════════════════════

def get_bars(ticker, timeframe, nb_barres=100):
    """
    Récupère les bougies via Alpaca Data API
    timeframe : TimeFrame.Minute5, TimeFrame.Minute15, TimeFrame.Day
    """
    try:
        now_ny  = datetime.now(TZ_NY)
        debut   = now_ny - timedelta(days=5)

        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=timeframe,
            start=debut,
            end=now_ny,
            limit=nb_barres
        )
        bars = data_client.get_stock_bars(request)
        df   = bars.df

        if df is None or len(df) < 10:
            return None

        # Flatten multi-index si nécessaire
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")

        df = df.rename(columns={
            "open":   "Open",
            "high":   "High",
            "low":    "Low",
            "close":  "Close",
            "volume": "Volume"
        })

        # Garder seulement les colonnes nécessaires
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df = df.dropna()
        return df

    except Exception as e:
        print(f"  ⚠️ Alpaca Data {ticker} : {e}")
        return None

def get_bars_15min(ticker):
    return get_bars(ticker, TimeFrame.Minute15, nb_barres=100)

def get_bars_5min(ticker):
    return get_bars(ticker, TimeFrame.Minute5, nb_barres=60)

def get_bars_daily(ticker):
    return get_bars(ticker, TimeFrame.Day, nb_barres=60)

# ═══════════════════════════════════════════════════════════════
#  CALCUL QUANTITÉ — MAX 5 LOTS GARANTI ✅
# ═══════════════════════════════════════════════════════════════

def calculer_quantite(prix):
    quantite = int((CAPITAL_TOTAL * 0.02) / prix)
    return max(1, min(quantite, MAX_LOTS))

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS 15MIN (SWING)
# ═══════════════════════════════════════════════════════════════

def indicateurs_15min(df):
    # Stochastique (14,3)
    lmin = df["Low"].rolling(14).min()
    hmax = df["High"].rolling(14).max()
    df["%K"] = 100 * (df["Close"] - lmin) / (hmax - lmin + 1e-9)
    df["%D"] = df["%K"].rolling(3).mean()
    # RSI (14)
    d = df["Close"].diff()
    df["RSI"] = 100 - (100 / (d.clip(lower=0).rolling(14).mean() /
                               (-d.clip(upper=0)).rolling(14).mean() + 1e-9 + 1))
    # MACD (12,26,9)
    df["MACD"]     = df["Close"].ewm(span=12).mean() - df["Close"].ewm(span=26).mean()
    df["MACD_SIG"] = df["MACD"].ewm(span=9).mean()
    # EMA
    df["EMA9"]  = df["Close"].ewm(span=9).mean()
    df["EMA21"] = df["Close"].ewm(span=21).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    # Bollinger (Belkhayat)
    df["BB_MID"] = df["Close"].rolling(20).mean()
    df["BB_STD"] = df["Close"].rolling(20).std()
    df["BB_UP"]  = df["BB_MID"] + 2 * df["BB_STD"]
    df["BB_LO"]  = df["BB_MID"] - 2 * df["BB_STD"]
    # ATR (14)
    df["TR"]  = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"]  - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(14).mean()
    return df

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS 5MIN (SCALP) — RÉACTIFS
# ═══════════════════════════════════════════════════════════════

def indicateurs_5min(df):
    # RSI rapide (7)
    d = df["Close"].diff()
    df["RSI"] = 100 - (100 / (d.clip(lower=0).rolling(7).mean() /
                               (-d.clip(upper=0)).rolling(7).mean() + 1e-9 + 1))
    # MACD rapide (5,13,4)
    df["MACD"]     = df["Close"].ewm(span=5).mean() - df["Close"].ewm(span=13).mean()
    df["MACD_SIG"] = df["MACD"].ewm(span=4).mean()
    # Stochastique rapide (5,3)
    lmin = df["Low"].rolling(5).min()
    hmax = df["High"].rolling(5).max()
    df["%K"] = 100 * (df["Close"] - lmin) / (hmax - lmin + 1e-9)
    df["%D"] = df["%K"].rolling(3).mean()
    # EMA court
    df["EMA9"]  = df["Close"].ewm(span=9).mean()
    df["EMA21"] = df["Close"].ewm(span=21).mean()
    # Bollinger (10)
    df["BB_MID"] = df["Close"].rolling(10).mean()
    df["BB_STD"] = df["Close"].rolling(10).std()
    df["BB_UP"]  = df["BB_MID"] + 2 * df["BB_STD"]
    df["BB_LO"]  = df["BB_MID"] - 2 * df["BB_STD"]
    # ATR rapide (7)
    df["TR"]  = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"]  - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(7).mean()
    return df

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION MÈCHES ×2
# ═══════════════════════════════════════════════════════════════

def detecter_meche(df):
    if df is None or len(df) < 3:
        return None

    def analyse(row):
        o, c = float(row["Open"]), float(row["Close"])
        h, l = float(row["High"]), float(row["Low"])
        corps = abs(c - o)
        if corps < 1e-9: corps = 1e-9
        return corps, h - max(o,c), min(o,c) - l, o, c

    corps,   mh,  mb,  o,  c  = analyse(df.iloc[-1])
    corps_1, mh1, mb1, o1, c1 = analyse(df.iloc[-2])
    corps_2, mh2, mb2, o2, c2 = analyse(df.iloc[-3])

    if mb >= MECHE_MULTIPLICATEUR * corps and mh < corps:
        return "MARTEAU"
    if mh >= MECHE_MULTIPLICATEUR * corps and mb < corps:
        return "MARTEAU_INVERSE"
    if (c2 < o2 and corps_1 < corps_2*0.3 and c > o and c > (o2+c2)/2):
        return "ETOILE_MATIN"
    if (c2 > o2 and corps_1 < corps_2*0.3 and c < o and c < (o2+c2)/2):
        return "ETOILE_SOIR"
    return None

def calcul_tendance(ticker):
    """Tendance via données daily Alpaca"""
    try:
        df = get_bars_daily(ticker)
        if df is None or len(df) < 20:
            return "NEUTRE"
        df["MA20"] = df["Close"].rolling(20).mean()
        close = float(df.iloc[-1]["Close"])
        ma20  = float(df.iloc[-1]["MA20"])
        if close > ma20 * 1.01:   return "HAUSSE"
        elif close < ma20 * 0.99: return "BAISSE"
        else:                     return "NEUTRE"
    except:
        return "NEUTRE"

# ═══════════════════════════════════════════════════════════════
#  TYPE 1 — SWING LONG 15MIN
# ═══════════════════════════════════════════════════════════════

def signal_swing_long(ticker):
    df = get_bars_15min(ticker)
    if df is None or len(df) < 30:
        return None
    df = indicateurs_15min(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix  = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    dv    = float(d["%D"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    atr        = float(d["ATR"])
    bbl, bbm   = float(d["BB_LO"]), float(d["BB_MID"])
    e9, e21    = float(d["EMA9"]),  float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    tendance    = calcul_tendance(ticker)
    tendance_ok = tendance in ["HAUSSE", "NEUTRE"]

    sig, score = None, 0

    # LONG A : Rebond zone basse Belkhayat
    if prix <= bbl * 1.015 and k < 30 and dv < 35 and tendance_ok:
        sig, score = "BELKHAYAT_REBOND", 3
        if vol_ok: score+=1
        if k > kp: score+=1
        if rsi < 40: score+=1

    # LONG B : Croisement MACD haussier
    elif macd > msig and mp <= sp and rsi > 35 and rsi < 65 and tendance_ok:
        sig, score = "MACD_HAUSSIER", 3
        if vol_ok: score+=1
        if prix < bbm: score+=1
        if k < 60: score+=1

    # LONG C : RSI survendu
    elif rsi < 35 and k > kp and k < 45 and tendance_ok and vol_ok:
        sig, score = "RSI_SURVENDU", 3
        if prix <= bbl*1.02: score+=1
        if macd > mp: score+=1
        if k > dv: score+=1

    # LONG D : Golden Cross EMA9/EMA21
    elif e9 > e21 and e9p <= e21p and tendance_ok and rsi < 65:
        sig, score = "EMA_GOLDEN_CROSS", 3
        if vol_ok: score+=1
        if rsi > 40: score+=1
        if prix > bbm: score+=1

    if sig is None or score < 4:
        return None

    q = calculer_quantite(prix)
    return {
        "ticker": ticker, "type": "SWING_LONG", "signal": sig,
        "score": score, "prix": prix,
        "sl": round(prix - (atr * ATR_MULT_SWING), 2),
        "quantite": q, "capital": round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 2 — SCALP LONG 5MIN
# ═══════════════════════════════════════════════════════════════

def signal_scalp_long(ticker):
    df = get_bars_5min(ticker)
    if df is None or len(df) < 20:
        return None
    df = indicateurs_5min(df)

    pattern = detecter_meche(df)
    if pattern not in ["MARTEAU", "ETOILE_MATIN"]:
        return None

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix  = float(d["Close"])
    rsi   = float(d["RSI"])
    macd  = float(d["MACD"])
    mp    = float(d1["MACD"])
    k     = float(d["%K"])
    bbl   = float(d["BB_LO"])

    score = 2
    if rsi < 60:         score += 1
    if macd >= mp:       score += 1
    if k < 65:           score += 1
    if prix <= bbl*1.02: score += 1

    if score < 3:
        return None

    q = calculer_quantite(prix)
    return {
        "ticker": ticker, "type": "SCALP_LONG", "signal": pattern,
        "direction": "LONG", "score": score, "prix": prix,
        "sl": round(prix * (1 - SL_SCALP_PCT), 2),
        "tp": round(prix * (1 + TP_SCALP_PCT), 2),
        "quantite": q, "capital": round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 3 — 7 SIGNAUX SHORT 5MIN 🔴
# ═══════════════════════════════════════════════════════════════

def signal_short(ticker):
    df = get_bars_5min(ticker)
    if df is None or len(df) < 20:
        return None
    df = indicateurs_5min(df)

    d, d1, d2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    prix  = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    dv    = float(d["%D"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    bbu, bbm   = float(d["BB_UP"]), float(d["BB_MID"])
    e9, e21    = float(d["EMA9"]),  float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])
    rsi_prev   = float(d2["RSI"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    sig, score = None, 0

    # SHORT 1 : Mèche haute ×2
    pattern = detecter_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        sig, score = f"MECHE_{pattern}", 3
        if rsi > 55: score+=1
        if macd <= mp: score+=1
        if k > 60: score+=1
        if vol_ok: score+=1

    # SHORT 2 : Zone haute Belkhayat
    elif prix >= bbu * 0.985 and k > 70 and rsi > 65:
        sig, score = "ZONE_HAUTE_BELKHAYAT", 3
        if k < kp: score+=1
        if macd < msig: score+=1
        if vol_ok: score+=1
        if rsi > 70: score+=1

    # SHORT 3 : MACD baissier 5min
    elif macd < msig and mp >= sp and rsi > 50:
        sig, score = "MACD_BAISSIER_5MIN", 3
        if prix > bbm: score+=1
        if k > 60: score+=1
        if vol_ok: score+=1
        if rsi > 55: score+=1

    # SHORT 4 : RSI suracheté + stoch redescend
    elif rsi > 70 and k < kp and k > 55:
        sig, score = "RSI_SURACHETÉ_5MIN", 3
        if prix >= bbu*0.97: score+=1
        if macd < mp: score+=1
        if vol_ok: score+=1
        if dv > 70: score+=1

    # SHORT 5 : EMA Death Cross 5min
    elif e9 < e21 and e9p >= e21p and rsi > 45:
        sig, score = "EMA_DEATH_CROSS_5MIN", 3
        if prix < bbm: score+=1
        if macd < msig: score+=1
        if vol_ok: score+=1
        if rsi > 50: score+=1

    # SHORT 6 : Cassure sous EMA21
    elif prix < e21 and float(d1["Close"]) >= e21p and k < 50 and vol_ok:
        sig, score = "CASSURE_EMA21_5MIN", 3
        if macd < msig: score+=1
        if k < kp: score+=1
        if rsi < 50: score+=1
        if prix < bbm: score+=1

    # SHORT 7 : Divergence baissière
    elif prix > float(d2["Close"]) and rsi < rsi_prev and rsi > 55 and k > 60:
        sig, score = "DIVERGENCE_BAISSIERE_5MIN", 3
        if macd < mp: score+=1
        if k < kp: score+=1
        if prix > bbm: score+=1
        if vol_ok: score+=1

    if sig is None or score < 3:
        return None

    q = calculer_quantite(prix)
    return {
        "ticker": ticker, "type": "SHORT", "signal": sig,
        "score": score, "direction": "SHORT", "prix": prix,
        "sl": round(prix * (1 + SL_SHORT_PCT), 2),
        "tp": round(prix * (1 - TP_SHORT_PCT), 2),
        "quantite": q, "capital": round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL SORTIE SWING
# ═══════════════════════════════════════════════════════════════

def signal_sortie_swing(ticker, prix_entree):
    df = get_bars_15min(ticker)
    if df is None or len(df) < 30:
        return False, None
    df = indicateurs_15min(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix  = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    bbu        = float(d["BB_UP"])
    e9, e21    = float(d["EMA9"]),  float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])

    if prix < prix_entree:
        return False, None

    if prix >= bbu*0.985 and k > 70 and k < kp and rsi > 65:
        return True, "ZONE_HAUTE_BELKHAYAT"
    if macd < msig and mp >= sp and rsi > 50:
        return True, "MACD_BAISSIER"
    if rsi > 72 and k < kp:
        return True, "RSI_SURACHETÉ"
    if e9 < e21 and e9p >= e21p:
        return True, "EMA_DEATH_CROSS"

    pattern = detecter_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        return True, f"PATTERN_{pattern}"

    return False, None

# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════

def passer_achat(signal):
    try:
        ordre = MarketOrderRequest(
            symbol=signal["ticker"], qty=signal["quantite"],
            side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=signal["sl"])
        )
        result = client.submit_order(ordre)
        tp_str = f" TP:{signal['tp']:.2f}$" if "tp" in signal else ""
        print(f"  ✅ LONG {signal['ticker']} | {signal['signal']}")
        print(f"     {signal['quantite']}x{signal['prix']:.2f}$ | SL:{signal['sl']:.2f}${tp_str}")
        return result.id
    except Exception as e:
        print(f"  ❌ Achat {signal['ticker']} : {e}")
        try:
            r = client.submit_order(MarketOrderRequest(
                symbol=signal["ticker"], qty=signal["quantite"],
                side=OrderSide.BUY, time_in_force=TimeInForce.DAY
            ))
            print(f"  ✅ LONG simple {signal['ticker']}")
            return r.id
        except Exception as e2:
            print(f"  ❌ Échec : {e2}")
            return None

def passer_short(signal):
    try:
        ordre = MarketOrderRequest(
            symbol=signal["ticker"], qty=signal["quantite"],
            side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=signal["tp"]),
            stop_loss=StopLossRequest(stop_price=signal["sl"])
        )
        result = client.submit_order(ordre)
        print(f"  🔴 SHORT {signal['ticker']} | {signal['signal']} | score {signal['score']}/7")
        print(f"     {signal['quantite']}x{signal['prix']:.2f}$ | SL:{signal['sl']:.2f}$ TP:{signal['tp']:.2f}$")
        return result.id
    except Exception as e:
        print(f"  ❌ Short {signal['ticker']} : {e}")
        return None

def passer_vente(ticker, quantite, raison):
    try:
        r = client.submit_order(MarketOrderRequest(
            symbol=ticker, qty=quantite,
            side=OrderSide.SELL, time_in_force=TimeInForce.DAY
        ))
        print(f"  ✅ VENTE {quantite}x{ticker} | {raison}")
        return r.id
    except Exception as e:
        print(f"  ❌ Vente {ticker} : {e}")
        return None

def fermeture_forcee_tout():
    """✅ Fermeture GARANTIE à 21h — toutes positions fermées"""
    print("\n🔔 21h00 — FERMETURE FORCÉE TOUTES POSITIONS")
    try:
        positions = client.get_all_positions()
        if not positions:
            print("  ℹ️ Aucune position ouverte")
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
        print(f"  ❌ Erreur fermeture : {e}")

# ═══════════════════════════════════════════════════════════════
#  PORTEFEUILLES
# ═══════════════════════════════════════════════════════════════

swing_positions = {}
scalp_positions = {}

def sync_alpaca():
    try:
        reelles = {p.symbol for p in client.get_all_positions()}
        for t in list(swing_positions.keys()):
            if t not in reelles:
                print(f"  🛑 {t} swing fermée Alpaca")
                del swing_positions[t]
        for k in list(scalp_positions.keys()):
            if scalp_positions[k]["ticker"] not in reelles:
                print(f"  ✅ {scalp_positions[k]['ticker']} scalp/short fermée Alpaca")
                del scalp_positions[k]
    except Exception as e:
        print(f"  ⚠️ Sync : {e}")

def gerer_swing():
    if not swing_positions: return
    print("\n📊 Swing :")
    try:
        pa = {p.symbol: p for p in client.get_all_positions()}
    except: return
    for ticker, pos in list(swing_positions.items()):
        if ticker not in pa:
            print(f"  🛑 {ticker} SL Alpaca")
            del swing_positions[ticker]; continue
        prix_actuel = float(pa[ticker].current_price)
        entree      = pos["prix_entree"]
        gain        = ((prix_actuel - entree) / entree) * 100
        q           = int(float(pa[ticker].qty))
        print(f"  📌 {ticker} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain:+.2f}%")
        try:
            sortir, raison = signal_sortie_swing(ticker, entree)
            if sortir:
                passer_vente(ticker, q, raison)
                del swing_positions[ticker]
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

def gerer_scalp():
    if not scalp_positions: return
    print("\n⚡ Scalp/Short :")
    try:
        pa = {p.symbol: p for p in client.get_all_positions()}
    except: return
    for key, pos in list(scalp_positions.items()):
        ticker    = pos["ticker"]
        direction = pos["direction"]
        if ticker not in pa:
            print(f"  ✅ {ticker} {direction} TP/SL Alpaca")
            del scalp_positions[key]; continue
        prix_actuel = float(pa[ticker].current_price)
        entree      = pos["prix_entree"]
        gain        = ((prix_actuel - entree) / entree) * 100
        if direction == "SHORT": gain = -gain
        q = int(float(pa[ticker].qty))
        print(f"  ⚡ {ticker} {direction} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain:+.2f}%")
        try:
            df = get_bars_5min(ticker)
            if df is not None:
                pattern = detecter_meche(df)
                if direction == "LONG" and pattern in ["MARTEAU_INVERSE","ETOILE_SOIR"]:
                    passer_vente(ticker, q, "PATTERN_OPPOSE")
                    del scalp_positions[key]
                elif direction == "SHORT" and pattern in ["MARTEAU","ETOILE_MATIN"]:
                    try:
                        client.submit_order(MarketOrderRequest(
                            symbol=ticker, qty=q,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY
                        ))
                        print(f"  ✅ SHORT {ticker} clôturé")
                    except Exception as e:
                        print(f"  ❌ {e}")
                    del scalp_positions[key]
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

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
    print("\n🤖 ROBOT HALAL V7 — ALPACA DATA — SWING 15MIN + SCALP 5MIN")
    print("=" * 65)
    print(f"💰 Capital          : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs           : GLD | SGOL | USO")
    print(f"📈 Swing LONG       : max {MAX_LONG_SWING} | 15min | Belkhayat+MACD+RSI+EMA")
    print(f"⚡ Scalp LONG       : mèches ×2 | 5min | TP+0.8% SL-0.4%")
    print(f"🔴 SHORT            : 7 signaux | 5min | TP+1.0% SL-0.5%")
    print(f"📦 Max lots/trade   : {MAX_LOTS} actions ✅")
    print(f"🕘 Fermeture forcée : 21h00 ✅")
    print(f"🚫 Yahoo Finance    : REMPLACÉ par Alpaca Data ✅")
    print("=" * 65)

    fermeture_faite = False
    last_swing_scan = 0
    cycle_scalp     = 0

    while True:
        h, heure_str = get_heure()

        # ── FERMETURE FORCÉE 21h00 ✅ ──────────────────────────
        if h >= HEURE_FERMETURE:
            if not fermeture_faite:
                fermeture_forcee_tout()
                swing_positions.clear()
                scalp_positions.clear()
                fermeture_faite = True
                print("✅ Toutes positions fermées")
            print(f"🌙 {heure_str} — En attente 9h00")
            time.sleep(PAUSE_SCALP_MIN * 60)
            continue

        # Reset flag le matin
        if h >= HEURE_OUVERTURE:
            fermeture_faite = False

        # Marché pas encore ouvert
        if h < HEURE_OUVERTURE:
            print(f"⏳ {heure_str} — Marché fermé")
            time.sleep(PAUSE_SCALP_MIN * 60)
            continue

        cycle_scalp += 1
        now_ts = time.time()
        print(f"\n{'='*65}")
        print(f"⚡ Cycle {cycle_scalp} | 🕐 {heure_str} Paris")

        try:
            acc = client.get_account()
            print(f"💰 Cash: {float(acc.cash):.2f}$ | Portef: {float(acc.portfolio_value):.2f}$")
        except: pass

        sync_alpaca()

        # ── SCALP + SHORT toutes les 5min ──────────────────────
        if h < HEURE_FIN_SCALP:
            gerer_scalp()
            places = MAX_SCALP - len(scalp_positions)
            if places > 0:
                print(f"\n⚡🔴 Scan SCALP+SHORT ({places} place(s))...")
                for ticker in actifs:
                    if len(scalp_positions) >= MAX_SCALP: break
                    short_key = f"{ticker}_short"
                    scalp_key = f"{ticker}_scalp"

                    # SHORT priorité
                    if short_key not in scalp_positions:
                        try:
                            sig = signal_short(ticker)
                            if sig:
                                print(f"  🔴 {ticker} — {sig['signal']} score {sig['score']}/7")
                                oid = passer_short(sig)
                                if oid:
                                    scalp_positions[short_key] = {
                                        "ticker": ticker,
                                        "prix_entree": sig["prix"],
                                        "direction": "SHORT",
                                        "sl": sig["sl"],
                                        "tp": sig["tp"],
                                        "order_id": oid,
                                    }
                        except Exception as e:
                            print(f"  ⚠️ {ticker} short : {e}")

                    # SCALP LONG
                    if scalp_key not in scalp_positions and len(scalp_positions) < MAX_SCALP:
                        try:
                            sig = signal_scalp_long(ticker)
                            if sig:
                                print(f"  🟢 {ticker} — {sig['signal']} score {sig['score']}")
                                oid = passer_achat(sig)
                                if oid:
                                    scalp_positions[scalp_key] = {
                                        "ticker": ticker,
                                        "prix_entree": sig["prix"],
                                        "direction": "LONG",
                                        "sl": sig["sl"],
                                        "tp": sig["tp"],
                                        "order_id": oid,
                                    }
                        except Exception as e:
                            print(f"  ⚠️ {ticker} scalp : {e}")
            else:
                print("⚡ Scalp/Short plein (3/3)")
        else:
            print("⏰ Scalp/Short terminé (après 17h)")

        # ── SWING LONG toutes les 15min ────────────────────────
        if now_ts - last_swing_scan >= PAUSE_SWING_MIN * 60:
            last_swing_scan = now_ts
            gerer_swing()
            places_sw = MAX_LONG_SWING - len(swing_positions)
            if places_sw > 0:
                print(f"\n📈 Scan SWING 15min ({places_sw} place(s))...")
                signaux = []
                for ticker in actifs:
                    if ticker in swing_positions: continue
                    try:
                        sig = signal_swing_long(ticker)
                        if sig:
                            signaux.append(sig)
                            print(f"  🚨 SWING {ticker} — {sig['signal']} score {sig['score']}/6")
                    except Exception as e:
                        print(f"  ⚠️ {ticker} swing : {e}")

                signaux.sort(key=lambda x: x["score"], reverse=True)
                for sig in signaux:
                    if len(swing_positions) >= MAX_LONG_SWING: break
                    oid = passer_achat(sig)
                    if oid:
                        swing_positions[sig["ticker"]] = {
                            "prix_entree": sig["prix"],
                            "sl": sig["sl"],
                            "order_id": oid,
                        }

        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Swing:{len(swing_positions)}/{MAX_LONG_SWING} | Scalp/Short:{len(scalp_positions)}/{MAX_SCALP}")
        for t, p in swing_positions.items():
            print(f"   📈 {t} | {p['prix_entree']:.2f}$ SL:{p['sl']:.2f}$")
        for k, p in scalp_positions.items():
            e = "🔴" if p["direction"]=="SHORT" else "🟢"
            print(f"   {e} {p['direction']} {p['ticker']} | {p['prix_entree']:.2f}$ TP:{p['tp']:.2f}$ SL:{p['sl']:.2f}$")

        print(f"\n⏳ Prochain scan dans {PAUSE_SCALP_MIN}min...")
        time.sleep(PAUSE_SCALP_MIN * 60)

# LANCEMENT
lancer_robot()
