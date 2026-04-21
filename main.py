import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime
import pytz
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

# ═══════════════════════════════════════════════════════════════
#  CONNEXION ALPACA PAPER TRADING
# ═══════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("ALPACA_API_KEY", "PK4XAYAVTANIMZK6YT5DDNXZXT")
API_SECRET = os.environ.get("ALPACA_SECRET", "9iYFsPF1iv3mvVKDqA4dvF3w42RzGinyryixB8SxopsR")

client = TradingClient(API_KEY, API_SECRET, paper=True)
account = client.get_account()

print("✅ Connexion Alpaca réussie !")
print(f"💰 Capital disponible : {float(account.cash):.2f}$")
print(f"📊 Valeur portefeuille : {float(account.portfolio_value):.2f}$")

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CAPITAL_TOTAL        = float(account.cash)
RISQUE_PAR_TRADE     = 0.02
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
DELAI_REQUETE        = 8           # Anti rate-limit Yahoo
PAUSE_SWING_MIN      = 15          # Swing scan toutes les 15min
PAUSE_SCALP_MIN      = 5           # ✅ Scalp scan toutes les 5min
TZ_PARIS             = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  3 ACTIFS HALAL UNIQUEMENT
# ═══════════════════════════════════════════════════════════════

actifs = {
    "GLD":  "Or (XAUUSD)",
    "SGOL": "Or physique (GC)",
    "USO":  "Pétrole (CL)",
}

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT AVEC RETRY
# ═══════════════════════════════════════════════════════════════

def telecharger_donnees(ticker, period="5d", interval="15m"):
    for tentative in range(4):
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 20:
                df.columns = df.columns.get_level_values(0)
                return df
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            attente = DELAI_REQUETE * (tentative + 2)
            print(f"  ⚠️ {ticker} tentative {tentative+1}/4 — {attente}s")
            time.sleep(attente)
    return None

def telecharger_5min(ticker):
    """Données 5min pour scalp rapide"""
    for tentative in range(4):
        try:
            df = yf.download(ticker, period="2d", interval="5m",
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 20:
                df.columns = df.columns.get_level_values(0)
                return df
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            attente = DELAI_REQUETE * (tentative + 2)
            print(f"  ⚠️ {ticker} 5min tentative {tentative+1}/4 — {attente}s")
            time.sleep(attente)
    return None

# ═══════════════════════════════════════════════════════════════
#  CALCUL QUANTITÉ — MAX 5 LOTS
# ═══════════════════════════════════════════════════════════════

def calculer_quantite(prix):
    """Max 5 lots pour simuler petit capital réel"""
    quantite = int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix)
    return max(1, min(quantite, MAX_LOTS))

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS 15MIN (SWING)
# ═══════════════════════════════════════════════════════════════

def indicateurs_15min(df):
    # Stochastique standard
    low_min  = df["Low"].rolling(14).min()
    high_max = df["High"].rolling(14).max()
    df["%K"]  = 100 * (df["Close"] - low_min) / (high_max - low_min + 1e-9)
    df["%D"]  = df["%K"].rolling(3).mean()
    # RSI standard
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    perte = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (gain / (perte + 1e-9) + 1))
    # MACD standard
    ema12            = df["Close"].ewm(span=12).mean()
    ema26            = df["Close"].ewm(span=26).mean()
    df["MACD"]       = ema12 - ema26
    df["MACD_SIG"]   = df["MACD"].ewm(span=9).mean()
    # EMA
    df["EMA9"]  = df["Close"].ewm(span=9).mean()
    df["EMA21"] = df["Close"].ewm(span=21).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    # Bollinger / Zones Belkhayat
    df["BB_MID"] = df["Close"].rolling(20).mean()
    df["BB_STD"] = df["Close"].rolling(20).std()
    df["BB_UP"]  = df["BB_MID"] + 2 * df["BB_STD"]
    df["BB_LO"]  = df["BB_MID"] - 2 * df["BB_STD"]
    # ATR
    df["H-L"]  = df["High"] - df["Low"]
    df["H-CP"] = abs(df["High"] - df["Close"].shift(1))
    df["L-CP"] = abs(df["Low"]  - df["Close"].shift(1))
    df["ATR"]  = df[["H-L","H-CP","L-CP"]].max(axis=1).rolling(14).mean()
    return df

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS 5MIN (SCALP) — PLUS RÉACTIFS
# ═══════════════════════════════════════════════════════════════

def indicateurs_5min(df):
    # RSI rapide période 7
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(7).mean()
    perte = (-delta.clip(upper=0)).rolling(7).mean()
    df["RSI"] = 100 - (100 / (gain / (perte + 1e-9) + 1))
    # MACD rapide (5,13,4)
    ema5           = df["Close"].ewm(span=5).mean()
    ema13          = df["Close"].ewm(span=13).mean()
    df["MACD"]     = ema5 - ema13
    df["MACD_SIG"] = df["MACD"].ewm(span=4).mean()
    # Stochastique rapide (5,3)
    low_min  = df["Low"].rolling(5).min()
    high_max = df["High"].rolling(5).max()
    df["%K"]  = 100 * (df["Close"] - low_min) / (high_max - low_min + 1e-9)
    df["%D"]  = df["%K"].rolling(3).mean()
    # EMA court
    df["EMA9"]  = df["Close"].ewm(span=9).mean()
    df["EMA21"] = df["Close"].ewm(span=21).mean()
    # Bollinger 5min
    df["BB_MID"] = df["Close"].rolling(10).mean()
    df["BB_STD"] = df["Close"].rolling(10).std()
    df["BB_UP"]  = df["BB_MID"] + 2 * df["BB_STD"]
    df["BB_LO"]  = df["BB_MID"] - 2 * df["BB_STD"]
    # ATR rapide
    df["H-L"]  = df["High"] - df["Low"]
    df["H-CP"] = abs(df["High"] - df["Close"].shift(1))
    df["L-CP"] = abs(df["Low"]  - df["Close"].shift(1))
    df["ATR"]  = df[["H-L","H-CP","L-CP"]].max(axis=1).rolling(7).mean()
    return df

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION GRANDES MÈCHES (×2)
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

    if mb  >= MECHE_MULTIPLICATEUR * corps  and mh  < corps:  return "MARTEAU"
    if mh  >= MECHE_MULTIPLICATEUR * corps  and mb  < corps:  return "MARTEAU_INVERSE"
    if (c2 < o2 and corps_1 < corps_2*0.3 and c > o and c > (o2+c2)/2): return "ETOILE_MATIN"
    if (c2 > o2 and corps_1 < corps_2*0.3 and c < o and c < (o2+c2)/2): return "ETOILE_SOIR"
    return None

def calcul_tendance(ticker):
    for tentative in range(3):
        try:
            df = yf.download(ticker, period="3mo", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or len(df) < 20: return "NEUTRE"
            df.columns = df.columns.get_level_values(0)
            df["MA20"] = df["Close"].rolling(20).mean()
            close = float(df.iloc[-1]["Close"])
            ma20  = float(df.iloc[-1]["MA20"])
            if close > ma20 * 1.01:   return "HAUSSE"
            elif close < ma20 * 0.99: return "BAISSE"
            else:                     return "NEUTRE"
        except:
            time.sleep(DELAI_REQUETE * (tentative+1))
    return "NEUTRE"

# ═══════════════════════════════════════════════════════════════
#  TYPE 1 — SWING LONG 15MIN (Belkhayat + RSI + MACD + EMA)
# ═══════════════════════════════════════════════════════════════

def signal_swing_long(ticker):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30: return None
    df = indicateurs_15min(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    dv    = float(d["%D"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    atr        = float(d["ATR"])
    bbl        = float(d["BB_LO"])
    bbm        = float(d["BB_MID"])
    e9, e21    = float(d["EMA9"]), float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    time.sleep(DELAI_REQUETE)
    tendance    = calcul_tendance(ticker)
    tendance_ok = tendance in ["HAUSSE", "NEUTRE"]

    sig, score = None, 0

    if prix <= bbl * 1.015 and k < 30 and dv < 35 and tendance_ok:
        sig, score = "BELKHAYAT_REBOND", 3
        if vol_ok: score+=1
        if k > kp: score+=1
        if rsi < 40: score+=1

    elif macd > msig and mp <= sp and rsi > 35 and rsi < 65 and tendance_ok:
        sig, score = "MACD_HAUSSIER", 3
        if vol_ok: score+=1
        if prix < bbm: score+=1
        if k < 60: score+=1

    elif rsi < 35 and k > kp and k < 45 and tendance_ok and vol_ok:
        sig, score = "RSI_SURVENDU", 3
        if prix <= bbl*1.02: score+=1
        if macd > mp: score+=1
        if k > dv: score+=1

    elif e9 > e21 and e9p <= e21p and tendance_ok and rsi < 65:
        sig, score = "EMA_GOLDEN_CROSS", 3
        if vol_ok: score+=1
        if rsi > 40: score+=1
        if prix > bbm: score+=1

    if sig is None or score < 4: return None

    return {
        "ticker": ticker, "type": "SWING_LONG", "signal": sig,
        "score": score, "prix": prix,
        "sl": round(prix - (atr * ATR_MULT_SWING), 2),
        "quantite": calculer_quantite(prix),
        "capital": round(calculer_quantite(prix) * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 2 — SCALP LONG 5MIN (mèches ×2 + RSI/MACD réactifs)
# ═══════════════════════════════════════════════════════════════

def signal_scalp_long(ticker):
    df = telecharger_5min(ticker)
    if df is None or len(df) < 20: return None
    df = indicateurs_5min(df)

    pattern = detecter_meche(df)
    if pattern not in ["MARTEAU", "ETOILE_MATIN"]: return None

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix  = float(d["Close"])
    rsi   = float(d["RSI"])
    macd  = float(d["MACD"])
    mp    = float(d1["MACD"])
    k     = float(d["%K"])
    bbl   = float(d["BB_LO"])

    score = 2
    if rsi < 60:       score += 1
    if macd >= mp:     score += 1
    if k < 65:         score += 1
    if prix <= bbl*1.02: score += 1

    if score < 3: return None

    q  = calculer_quantite(prix)
    return {
        "ticker": ticker, "type": "SCALP_LONG", "signal": pattern,
        "direction": "LONG", "score": score,
        "prix": prix,
        "sl": round(prix * (1 - SL_SCALP_PCT), 2),
        "tp": round(prix * (1 + TP_SCALP_PCT), 2),
        "quantite": q, "capital": round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 3 — 7 SIGNAUX SHORT 5MIN 🔴
# ═══════════════════════════════════════════════════════════════

def signal_short(ticker):
    df = telecharger_5min(ticker)
    if df is None or len(df) < 20: return None
    df = indicateurs_5min(df)

    d, d1, d2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    prix  = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    dv    = float(d["%D"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    bbu        = float(d["BB_UP"])
    bbm        = float(d["BB_MID"])
    e9, e21    = float(d["EMA9"]), float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])
    e9pp,e21pp = float(d2["EMA9"]), float(d2["EMA21"])
    rsi_prev   = float(d2["RSI"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    sig, score = None, 0

    # SHORT 1 : Mèche haute ×2 (Marteau inverse / Étoile du soir)
    pattern = detecter_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        sig, score = f"MECHE_{pattern}", 3
        if rsi > 55:         score += 1
        if macd <= mp:       score += 1
        if k > 60:           score += 1
        if vol_ok:           score += 1

    # SHORT 2 : Zone haute Belkhayat + suracheté
    elif prix >= bbu * 0.985 and k > 70 and rsi > 65:
        sig, score = "ZONE_HAUTE_BELKHAYAT", 3
        if k < kp:           score += 1
        if macd < msig:      score += 1
        if vol_ok:           score += 1
        if rsi > 70:         score += 1

    # SHORT 3 : Croisement MACD baissier 5min
    elif macd < msig and mp >= sp and rsi > 50:
        sig, score = "MACD_BAISSIER_5MIN", 3
        if prix > bbm:       score += 1
        if k > 60:           score += 1
        if vol_ok:           score += 1
        if rsi > 55:         score += 1

    # SHORT 4 : RSI suracheté + stoch redescend
    elif rsi > 70 and k < kp and k > 55:
        sig, score = "RSI_SURACHETÉ_5MIN", 3
        if prix >= bbu*0.97: score += 1
        if macd < mp:        score += 1
        if vol_ok:           score += 1
        if dv > 70:          score += 1

    # SHORT 5 : EMA Death Cross 5min (EMA9 croise sous EMA21)
    elif e9 < e21 and e9p >= e21p and rsi > 45:
        sig, score = "EMA_DEATH_CROSS_5MIN", 3
        if prix < bbm:       score += 1
        if macd < msig:      score += 1
        if vol_ok:           score += 1
        if rsi > 50:         score += 1

    # SHORT 6 : Cassure sous EMA21 avec volume
    elif prix < e21 and float(d1["Close"]) >= e21p and k < 50 and vol_ok:
        sig, score = "CASSURE_EMA21_5MIN", 3
        if macd < msig:      score += 1
        if k < kp:           score += 1
        if rsi < 50:         score += 1
        if prix < bbm:       score += 1

    # SHORT 7 : Divergence baissière (prix monte mais RSI baisse)
    elif prix > float(d2["Close"]) and rsi < rsi_prev and rsi > 55 and k > 60:
        sig, score = "DIVERGENCE_BAISSIERE_5MIN", 3
        if macd < mp:        score += 1
        if k < kp:           score += 1
        if prix > bbm:       score += 1
        if vol_ok:           score += 1

    if sig is None or score < 3: return None

    q = calculer_quantite(prix)
    return {
        "ticker": ticker, "type": "SHORT", "signal": sig,
        "score": score, "direction": "SHORT",
        "prix": prix,
        "sl": round(prix * (1 + SL_SHORT_PCT), 2),
        "tp": round(prix * (1 - TP_SHORT_PCT), 2),
        "quantite": q, "capital": round(q * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL SORTIE SWING 15MIN
# ═══════════════════════════════════════════════════════════════

def signal_sortie_swing(ticker, prix_entree):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30: return False, None
    df = indicateurs_15min(df)

    d, d1 = df.iloc[-1], df.iloc[-2]
    prix  = float(d["Close"])
    k, kp = float(d["%K"]), float(d1["%K"])
    rsi   = float(d["RSI"])
    macd, msig = float(d["MACD"]), float(d["MACD_SIG"])
    mp, sp     = float(d1["MACD"]), float(d1["MACD_SIG"])
    bbu        = float(d["BB_UP"])
    e9, e21    = float(d["EMA9"]), float(d["EMA21"])
    e9p, e21p  = float(d1["EMA9"]), float(d1["EMA21"])

    if prix < prix_entree: return False, None

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
        tp_str = f" | TP: {signal['tp']:.2f}$" if "tp" in signal else ""
        print(f"  ✅ LONG {signal['ticker']} | {signal['signal']} | {signal['quantite']}x{signal['prix']:.2f}$")
        print(f"     SL: {signal['sl']:.2f}${tp_str} | Capital: {signal['capital']:.2f}$")
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
            print(f"  ❌ Échec total : {e2}")
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
        print(f"  🔴 SHORT {signal['ticker']} | {signal['signal']} | Score {signal['score']}/7")
        print(f"     {signal['quantite']}x{signal['prix']:.2f}$ | SL: {signal['sl']:.2f}$ | TP: {signal['tp']:.2f}$")
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
    print("\n🔔 21h00 — FERMETURE FORCÉE TOUTES POSITIONS")
    try:
        positions = client.get_all_positions()
        if not positions:
            print("  ℹ️ Aucune position")
            return
        for pos in positions:
            try:
                side = OrderSide.SELL if "long" in str(pos.side) else OrderSide.BUY
                client.submit_order(MarketOrderRequest(
                    symbol=pos.symbol, qty=abs(int(float(pos.qty))),
                    side=side, time_in_force=TimeInForce.DAY
                ))
                print(f"  ✅ {pos.symbol} fermée")
            except Exception as e:
                print(f"  ❌ {pos.symbol} : {e}")
    except Exception as e:
        print(f"  ❌ Fermeture : {e}")

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
                print(f"  🛑 {t} swing — fermée Alpaca")
                del swing_positions[t]
        for k in list(scalp_positions.keys()):
            if scalp_positions[k]["ticker"] not in reelles:
                print(f"  ✅ {scalp_positions[k]['ticker']} scalp/short — fermée Alpaca")
                del scalp_positions[k]
    except Exception as e:
        print(f"  ⚠️ Sync : {e}")

def gerer_swing():
    if not swing_positions: return
    print("\n📊 Swing positions :")
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
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

def gerer_scalp():
    if not scalp_positions: return
    print("\n⚡ Scalp/Short positions :")
    try:
        pa = {p.symbol: p for p in client.get_all_positions()}
    except: return
    for key, pos in list(scalp_positions.items()):
        ticker    = pos["ticker"]
        direction = pos["direction"]
        if ticker not in pa:
            print(f"  ✅ {ticker} {direction} fermé TP/SL Alpaca")
            del scalp_positions[key]; continue
        prix_actuel = float(pa[ticker].current_price)
        entree      = pos["prix_entree"]
        gain        = ((prix_actuel - entree) / entree) * 100
        if direction == "SHORT": gain = -gain
        q = int(float(pa[ticker].qty))
        print(f"  ⚡ {ticker} {direction} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain:+.2f}%")
        try:
            df = telecharger_5min(ticker)
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
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def get_heure():
    now = datetime.now(TZ_PARIS)
    return now.hour + now.minute/60, now.strftime("%H:%M")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE — DOUBLE VITESSE
#  Swing  : scan toutes les 15min
#  Scalp  : scan toutes les 5min
# ═══════════════════════════════════════════════════════════════

def lancer_robot():
    print("\n🤖 ROBOT HALAL V7 — SWING 15MIN + SCALP 5MIN + 7 SHORTS")
    print("=" * 65)
    print(f"💰 Capital          : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs           : GLD | SGOL | USO")
    print(f"📈 Swing LONG       : max {MAX_LONG_SWING} | scan 15min | Belkhayat+MACD+RSI+EMA")
    print(f"⚡ Scalp LONG       : mèches ×2 | scan 5min | TP +0.8% SL -0.4%")
    print(f"🔴 SHORT            : 7 signaux | scan 5min | TP +1.0% SL -0.5%")
    print(f"📦 Max lots/trade   : {MAX_LOTS} actions maximum")
    print(f"🕘 Fermeture forcée : 21h00")
    print(f"📊 Trades/jour est. : 15 à 25")
    print("=" * 65)

    fermeture_faite  = False
    last_swing_scan  = 0
    cycle_scalp      = 0

    while True:
        h, heure_str = get_heure()

        # ── FERMETURE FORCÉE 21h00 ─────────────────────────────
        if h >= HEURE_FERMETURE:
            if not fermeture_faite:
                fermeture_forcee_tout()
                swing_positions.clear()
                scalp_positions.clear()
                fermeture_faite = True
            print(f"🌙 {heure_str} — Marché fermé")
            time.sleep(PAUSE_SCALP_MIN * 60)
            continue

        if h >= HEURE_OUVERTURE:
            fermeture_faite = False

        if h < HEURE_OUVERTURE:
            print(f"⏳ {heure_str} — Marché fermé")
            time.sleep(PAUSE_SCALP_MIN * 60)
            continue

        cycle_scalp += 1
        now_ts = time.time()
        print(f"\n{'='*65}")
        print(f"⚡ Cycle scalp {cycle_scalp} | 🕐 {heure_str} Paris")

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
                print(f"\n⚡🔴 Scan SCALP+SHORT 5min ({places} place(s))...")
                try:
                    pos_r = {p.symbol for p in client.get_all_positions()}
                except:
                    pos_r = set()

                for ticker in actifs:
                    if len(scalp_positions) >= MAX_SCALP: break
                    short_key = f"{ticker}_short"
                    scalp_key = f"{ticker}_scalp"

                    # SHORT en priorité
                    if short_key not in scalp_positions:
                        try:
                            sig = signal_short(ticker)
                            if sig:
                                print(f"  🔴 SHORT {ticker} — {sig['signal']} score {sig['score']}/7")
                                oid = passer_short(sig)
                                if oid:
                                    scalp_positions[short_key] = {
                                        "ticker": ticker, "prix_entree": sig["prix"],
                                        "direction": "SHORT", "sl": sig["sl"],
                                        "tp": sig["tp"], "order_id": oid,
                                    }
                            time.sleep(DELAI_REQUETE)
                        except Exception as e:
                            time.sleep(DELAI_REQUETE)

                    # SCALP LONG
                    if scalp_key not in scalp_positions and len(scalp_positions) < MAX_SCALP:
                        try:
                            sig = signal_scalp_long(ticker)
                            if sig:
                                print(f"  🟢 SCALP {ticker} — {sig['signal']} score {sig['score']}")
                                oid = passer_achat(sig)
                                if oid:
                                    scalp_positions[scalp_key] = {
                                        "ticker": ticker, "prix_entree": sig["prix"],
                                        "direction": "LONG", "sl": sig["sl"],
                                        "tp": sig["tp"], "order_id": oid,
                                    }
                            time.sleep(DELAI_REQUETE)
                        except Exception as e:
                            time.sleep(DELAI_REQUETE)
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
                try:
                    pos_r = {p.symbol for p in client.get_all_positions()}
                except:
                    pos_r = set()
                signaux = []
                for ticker in actifs:
                    if ticker in swing_positions or ticker in pos_r: continue
                    try:
                        sig = signal_swing_long(ticker)
                        if sig:
                            signaux.append(sig)
                            print(f"  🚨 SWING {ticker} — {sig['signal']} score {sig['score']}/6")
                        time.sleep(DELAI_REQUETE)
                    except Exception as e:
                        time.sleep(DELAI_REQUETE)
                signaux.sort(key=lambda x: x["score"], reverse=True)
                for sig in signaux:
                    if len(swing_positions) >= MAX_LONG_SWING: break
                    oid = passer_achat(sig)
                    if oid:
                        swing_positions[sig["ticker"]] = {
                            "prix_entree": sig["prix"],
                            "sl": sig["sl"], "order_id": oid,
                        }

        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Swing: {len(swing_positions)}/{MAX_LONG_SWING} | Scalp/Short: {len(scalp_positions)}/{MAX_SCALP}")
        for t, p in swing_positions.items():
            print(f"   📈 {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$")
        for k, p in scalp_positions.items():
            e = "🔴" if p["direction"] == "SHORT" else "🟢"
            print(f"   {e} {p['direction']} {p['ticker']} | {p['prix_entree']:.2f}$ | TP: {p['tp']:.2f}$ SL: {p['sl']:.2f}$")

        print(f"\n⏳ Prochain scan scalp dans {PAUSE_SCALP_MIN}min...")
        time.sleep(PAUSE_SCALP_MIN * 60)

# LANCEMENT
lancer_robot()
