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
MAX_LONG_SWING       = 4
MAX_SCALP            = 3
ATR_MULT_SWING       = 1.5
SL_SCALP_PCT         = 0.004    # SL scalp -0.4%
TP_SCALP_PCT         = 0.008    # TP scalp +0.8%
SL_SHORT_PCT         = 0.005    # SL short -0.5%
TP_SHORT_PCT         = 0.010    # TP short +1.0%
MECHE_MULTIPLICATEUR = 2        # Mèche ≥ 2x corps
HEURE_OUVERTURE      = 9
HEURE_FIN_SCALP      = 17
HEURE_FERMETURE      = 21
DELAI_REQUETE        = 8
TZ_PARIS             = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  3 ACTIFS HALAL UNIQUEMENT
# ═══════════════════════════════════════════════════════════════

actifs = {
    "GLD":  "Or 🥇 (équivalent XAUUSD)",
    "SGOL": "Or physique 🥇 (équivalent GC)",
    "USO":  "Pétrole 🛢️ (équivalent CL)",
}

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT AVEC RETRY
# ═══════════════════════════════════════════════════════════════

def telecharger_donnees(ticker, period="5d", interval="15m"):
    for tentative in range(4):
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 30:
                df.columns = df.columns.get_level_values(0)
                return df
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            attente = DELAI_REQUETE * (tentative + 2)
            print(f"  ⚠️ {ticker} tentative {tentative+1}/4 — attente {attente}s")
            time.sleep(attente)
    print(f"  ❌ {ticker} inaccessible")
    return None

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS
# ═══════════════════════════════════════════════════════════════

def calcul_atr(df, periode=14):
    df["H-L"]  = df["High"] - df["Low"]
    df["H-CP"] = abs(df["High"] - df["Close"].shift(1))
    df["L-CP"] = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]   = df[["H-L","H-CP","L-CP"]].max(axis=1)
    df["ATR"]  = df["TR"].rolling(periode).mean()
    return df

def calcul_stochastique(df, k=14, d=3):
    low_min  = df["Low"].rolling(k).min()
    high_max = df["High"].rolling(k).max()
    df["%K"]  = 100 * (df["Close"] - low_min) / (high_max - low_min + 1e-9)
    df["%D"]  = df["%K"].rolling(d).mean()
    return df

def calcul_rsi(df, periode=14):
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(periode).mean()
    perte = (-delta.clip(upper=0)).rolling(periode).mean()
    rs    = gain / (perte + 1e-9)
    df["RSI"] = 100 - (100 / (1 + rs))
    return df

def calcul_macd(df):
    ema12             = df["Close"].ewm(span=12).mean()
    ema26             = df["Close"].ewm(span=26).mean()
    df["MACD"]        = ema12 - ema26
    df["Signal_MACD"] = df["MACD"].ewm(span=9).mean()
    return df

def calcul_ema(df):
    df["EMA9"]  = df["Close"].ewm(span=9).mean()
    df["EMA21"] = df["Close"].ewm(span=21).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    return df

def calcul_zones_belkhayat(df, periode=20):
    df["Moyenne"]    = df["Close"].rolling(periode).mean()
    df["Ecart"]      = df["Close"].rolling(periode).std()
    df["Zone_Haute"] = df["Moyenne"] + 2 * df["Ecart"]
    df["Zone_Basse"] = df["Moyenne"] - 2 * df["Ecart"]
    df["Zone_Mid"]   = df["Moyenne"]
    return df

def calcul_tendance(ticker):
    for tentative in range(3):
        try:
            df_d = yf.download(ticker, period="3mo", interval="1d",
                               auto_adjust=True, progress=False)
            if df_d is None or len(df_d) < 20:
                return "NEUTRE"
            df_d.columns = df_d.columns.get_level_values(0)
            df_d["MA20"] = df_d["Close"].rolling(20).mean()
            last  = df_d.iloc[-1]
            close = float(last["Close"])
            ma20  = float(last["MA20"])
            if close > ma20 * 1.01:   return "HAUSSE"
            elif close < ma20 * 0.99: return "BAISSE"
            else:                     return "NEUTRE"
        except Exception:
            time.sleep(DELAI_REQUETE * (tentative + 1))
    return "NEUTRE"

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION GRANDES MÈCHES (×2) — PATTERNS JAPONAIS
# ═══════════════════════════════════════════════════════════════

def detecter_pattern_meche(df):
    if df is None or len(df) < 3:
        return None

    def analyse_bougie(row):
        ouv  = float(row["Open"])
        clo  = float(row["Close"])
        haut = float(row["High"])
        bas  = float(row["Low"])
        corps   = abs(clo - ouv)
        if corps < 1e-9: corps = 1e-9
        meche_h = haut - max(ouv, clo)
        meche_b = min(ouv, clo) - bas
        return corps, meche_h, meche_b, ouv, clo

    d   = df.iloc[-1]
    d_1 = df.iloc[-2]
    d_2 = df.iloc[-3]

    corps,   meche_h,  meche_b,  ouv,   clo   = analyse_bougie(d)
    corps_1, meche_h1, meche_b1, ouv_1, clo_1 = analyse_bougie(d_1)
    corps_2, meche_h2, meche_b2, ouv_2, clo_2 = analyse_bougie(d_2)

    # MARTEAU : grande mèche BASSE ×2 → ACHAT 🟢
    if meche_b >= MECHE_MULTIPLICATEUR * corps and meche_h < corps:
        return "MARTEAU"

    # MARTEAU INVERSE : grande mèche HAUTE ×2 → SHORT 🔴
    if meche_h >= MECHE_MULTIPLICATEUR * corps and meche_b < corps:
        return "MARTEAU_INVERSE"

    # ÉTOILE DU MATIN : rouge + petite + verte → ACHAT 🟢
    if (clo_2 < ouv_2 and
        corps_1 < corps_2 * 0.3 and
        clo > ouv and clo > (ouv_2 + clo_2) / 2):
        return "ETOILE_MATIN"

    # ÉTOILE DU SOIR : verte + petite + rouge → SHORT 🔴
    if (clo_2 > ouv_2 and
        corps_1 < corps_2 * 0.3 and
        clo < ouv and clo < (ouv_2 + clo_2) / 2):
        return "ETOILE_SOIR"

    return None

# ═══════════════════════════════════════════════════════════════
#  TYPE 1 — SWING LONG (Belkhayat + RSI + MACD)
# ═══════════════════════════════════════════════════════════════

def signal_swing_long(ticker):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30:
        return None

    df = calcul_stochastique(df)
    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_zones_belkhayat(df)
    df = calcul_atr(df)
    df = calcul_ema(df)

    d   = df.iloc[-1]
    d_1 = df.iloc[-2]

    prix      = float(d["Close"])
    k         = float(d["%K"])
    k_prev    = float(d_1["%K"])
    d_val     = float(d["%D"])
    rsi       = float(d["RSI"])
    macd      = float(d["MACD"])
    macd_sig  = float(d["Signal_MACD"])
    macd_prev = float(d_1["MACD"])
    sig_prev  = float(d_1["Signal_MACD"])
    atr       = float(d["ATR"])
    zone_b    = float(d["Zone_Basse"])
    zone_mid  = float(d["Zone_Mid"])
    ema9      = float(d["EMA9"])
    ema21     = float(d["EMA21"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    time.sleep(DELAI_REQUETE)
    tendance    = calcul_tendance(ticker)
    tendance_ok = tendance in ["HAUSSE", "NEUTRE"]

    signal_type = None
    score = 0

    # LONG A : Rebond zone basse Belkhayat
    if prix <= zone_b * 1.015 and k < 30 and d_val < 35 and tendance_ok:
        signal_type = "BELKHAYAT_REBOND"
        score = 3
        if vol_ok:      score += 1
        if k > k_prev:  score += 1
        if rsi < 40:    score += 1

    # LONG B : Croisement MACD haussier
    elif (macd > macd_sig and macd_prev <= sig_prev
          and rsi > 35 and rsi < 65 and tendance_ok):
        signal_type = "MACD_HAUSSIER"
        score = 3
        if vol_ok:          score += 1
        if prix < zone_mid: score += 1
        if k < 60:          score += 1

    # LONG C : RSI survendu + retournement
    elif rsi < 35 and k > k_prev and k < 45 and tendance_ok and vol_ok:
        signal_type = "RSI_SURVENDU"
        score = 3
        if prix <= zone_b * 1.02: score += 1
        if macd > macd_prev:      score += 1
        if k > d_val:             score += 1

    # LONG D : Croisement EMA9 > EMA21 (Golden cross court terme)
    elif (ema9 > ema21 and float(d_1["EMA9"]) <= float(d_1["EMA21"])
          and tendance_ok and rsi < 65):
        signal_type = "EMA_GOLDEN_CROSS"
        score = 3
        if vol_ok:          score += 1
        if rsi > 40:        score += 1
        if prix > zone_mid: score += 1

    if signal_type is None or score < 4:
        return None

    quantite = max(1, int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix))
    sl       = round(prix - (atr * ATR_MULT_SWING), 2)

    return {
        "ticker":    ticker,
        "type":      "SWING_LONG",
        "signal":    signal_type,
        "score":     score,
        "prix":      prix,
        "sl":        sl,
        "quantite":  quantite,
        "capital":   round(quantite * prix, 2),
        "direction": "LONG",
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 2 — SCALP LONG (mèches + confirmation)
# ═══════════════════════════════════════════════════════════════

def signal_scalp_long(ticker):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30:
        return None

    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_stochastique(df)
    df = calcul_zones_belkhayat(df)
    df = calcul_atr(df)

    pattern = detecter_pattern_meche(df)
    long_patterns = ["MARTEAU", "ETOILE_MATIN"]
    if pattern not in long_patterns:
        return None

    d    = df.iloc[-1]
    d_1  = df.iloc[-2]
    prix      = float(d["Close"])
    rsi       = float(d["RSI"])
    macd      = float(d["MACD"])
    macd_prev = float(d_1["MACD"])
    k         = float(d["%K"])

    # Confirmation achat
    if not (rsi < 65 and macd >= macd_prev):
        return None

    quantite = max(1, int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix))
    sl = round(prix * (1 - SL_SCALP_PCT), 2)
    tp = round(prix * (1 + TP_SCALP_PCT), 2)

    return {
        "ticker":    ticker,
        "type":      "SCALP_LONG",
        "signal":    pattern,
        "direction": "LONG",
        "prix":      prix,
        "sl":        sl,
        "tp":        tp,
        "quantite":  quantite,
        "capital":   round(quantite * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 3 — MULTIPLES SIGNAUX SHORT 🔴
#  7 signaux différents pour shorter au maximum
# ═══════════════════════════════════════════════════════════════

def signal_short(ticker):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30:
        return None

    df = calcul_stochastique(df)
    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_zones_belkhayat(df)
    df = calcul_atr(df)
    df = calcul_ema(df)

    d   = df.iloc[-1]
    d_1 = df.iloc[-2]
    d_2 = df.iloc[-3]

    prix      = float(d["Close"])
    k         = float(d["%K"])
    k_prev    = float(d_1["%K"])
    d_val     = float(d["%D"])
    rsi       = float(d["RSI"])
    macd      = float(d["MACD"])
    macd_sig  = float(d["Signal_MACD"])
    macd_prev = float(d_1["MACD"])
    sig_prev  = float(d_1["Signal_MACD"])
    atr       = float(d["ATR"])
    zone_h    = float(d["Zone_Haute"])
    zone_mid  = float(d["Zone_Mid"])
    zone_b    = float(d["Zone_Basse"])
    ema9      = float(d["EMA9"])
    ema21     = float(d["EMA21"])
    ema50     = float(d["EMA50"])
    ema9_prev = float(d_1["EMA9"])
    ema21_prev= float(d_1["EMA21"])

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    signal_type = None
    score = 0

    # ── SHORT 1 : Mèche haute ×2 (Marteau inverse) ────────────
    pattern = detecter_pattern_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        signal_type = f"MECHE_{pattern}"
        score = 3
        if rsi > 55:        score += 1
        if macd <= macd_prev: score += 1
        if k > 60:          score += 1
        if vol_ok:          score += 1

    # ── SHORT 2 : Zone haute Belkhayat + suracheté ─────────────
    elif prix >= zone_h * 0.985 and k > 70 and rsi > 65:
        signal_type = "ZONE_HAUTE_BELKHAYAT"
        score = 3
        if k < k_prev:      score += 1  # stoch redescend
        if macd < macd_sig: score += 1
        if vol_ok:          score += 1
        if rsi > 70:        score += 1

    # ── SHORT 3 : Croisement MACD baissier ────────────────────
    elif (macd < macd_sig and macd_prev >= sig_prev and rsi > 50):
        signal_type = "MACD_BAISSIER"
        score = 3
        if prix > zone_mid: score += 1
        if k > 60:          score += 1
        if vol_ok:          score += 1
        if rsi > 60:        score += 1

    # ── SHORT 4 : RSI suracheté + stoch redescend ─────────────
    elif rsi > 70 and k < k_prev and k > 55:
        signal_type = "RSI_SURACHETÉ"
        score = 3
        if prix >= zone_h * 0.97: score += 1
        if macd < macd_prev:      score += 1
        if vol_ok:                score += 1
        if d_val > 70:            score += 1

    # ── SHORT 5 : EMA Death Cross (EMA9 croise sous EMA21) ────
    elif (ema9 < ema21 and ema9_prev >= ema21_prev and rsi > 45):
        signal_type = "EMA_DEATH_CROSS"
        score = 3
        if prix < zone_mid: score += 1
        if macd < macd_sig: score += 1
        if vol_ok:          score += 1
        if rsi > 55:        score += 1

    # ── SHORT 6 : Prix sous EMA50 + stoch baissier ────────────
    elif (prix < ema50 and float(d_1["Close"]) >= ema50_prev
          if (ema50_prev := float(d_1["EMA50"])) else False
          and k < 50 and rsi < 55):
        signal_type = "CASSURE_EMA50"
        score = 3
        if vol_ok:          score += 1
        if macd < macd_sig: score += 1
        if k < k_prev:      score += 1
        if rsi < 45:        score += 1

    # ── SHORT 7 : Double divergence (prix monte, RSI baisse) ──
    elif (prix > float(d_2["Close"]) and
          rsi < float(df.iloc[-3]["RSI"]) and
          rsi > 55 and k > 60):
        signal_type = "DIVERGENCE_BAISSIERE"
        score = 3
        if macd < macd_prev:  score += 1
        if k < k_prev:        score += 1
        if prix > zone_mid:   score += 1
        if vol_ok:            score += 1

    # Score minimum 3/7 pour shorter
    if signal_type is None or score < 3:
        return None

    quantite = max(1, int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix))
    sl = round(prix * (1 + SL_SHORT_PCT), 2)
    tp = round(prix * (1 - TP_SHORT_PCT), 2)

    return {
        "ticker":    ticker,
        "type":      "SHORT",
        "signal":    signal_type,
        "score":     score,
        "direction": "SHORT",
        "prix":      prix,
        "sl":        sl,
        "tp":        tp,
        "quantite":  quantite,
        "capital":   round(quantite * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  SIGNAL DE SORTIE SWING
# ═══════════════════════════════════════════════════════════════

def signal_sortie_swing(ticker, prix_entree):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30:
        return False, None

    df = calcul_stochastique(df)
    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_zones_belkhayat(df)
    df = calcul_ema(df)

    d   = df.iloc[-1]
    d_1 = df.iloc[-2]

    prix     = float(d["Close"])
    k        = float(d["%K"])
    k_prev   = float(d_1["%K"])
    rsi      = float(d["RSI"])
    macd     = float(d["MACD"])
    macd_sig = float(d["Signal_MACD"])
    macd_prev= float(d_1["MACD"])
    sig_prev = float(d_1["Signal_MACD"])
    zone_h   = float(d["Zone_Haute"])
    ema9     = float(d["EMA9"])
    ema21    = float(d["EMA21"])

    if prix < prix_entree:
        return False, None

    if prix >= zone_h * 0.985 and k > 70 and k < k_prev and rsi > 65:
        return True, "ZONE_HAUTE_BELKHAYAT"
    if macd < macd_sig and macd_prev >= sig_prev and rsi > 50:
        return True, "MACD_BAISSIER"
    if rsi > 72 and k < k_prev:
        return True, "RSI_SURACHETÉ"
    if ema9 < ema21 and float(d_1["EMA9"]) >= float(d_1["EMA21"]):
        return True, "EMA_DEATH_CROSS"

    pattern = detecter_pattern_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        return True, f"PATTERN_{pattern}"

    return False, None

# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════

def passer_achat(signal):
    try:
        ordre = MarketOrderRequest(
            symbol=signal["ticker"],
            qty=signal["quantite"],
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=signal["sl"])
        )
        result = client.submit_order(ordre)
        print(f"  ✅ LONG {signal['ticker']} | {signal['signal']} | {signal['quantite']}x{signal['prix']:.2f}$")
        print(f"     SL: {signal['sl']:.2f}$ | Capital: {signal['capital']:.2f}$")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur achat {signal['ticker']} : {e}")
        try:
            ordre_simple = MarketOrderRequest(
                symbol=signal["ticker"],
                qty=signal["quantite"],
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            result = client.submit_order(ordre_simple)
            print(f"  ✅ LONG simple {signal['ticker']}")
            return result.id
        except Exception as e2:
            print(f"  ❌ Échec total : {e2}")
            return None

def passer_short(signal):
    try:
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
        print(f"  🔴 SHORT {signal['ticker']} | {signal['signal']} | Score {signal['score']}/7")
        print(f"     {signal['quantite']}x{signal['prix']:.2f}$ | SL: {signal['sl']:.2f}$ | TP: {signal['tp']:.2f}$")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur short {signal['ticker']} : {e}")
        return None

def passer_vente(ticker, quantite, raison):
    try:
        ordre = MarketOrderRequest(
            symbol=ticker, qty=quantite,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        result = client.submit_order(ordre)
        print(f"  ✅ VENTE {quantite}x{ticker} | {raison}")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur vente {ticker} : {e}")
        return None

def fermeture_forcee_tout():
    print("\n🔔 21h00 — FERMETURE FORCÉE TOUTES POSITIONS")
    try:
        positions = client.get_all_positions()
        if not positions:
            print("  ℹ️ Aucune position")
            return
        for pos in positions:
            ticker   = pos.symbol
            quantite = abs(int(float(pos.qty)))
            cote     = str(pos.side)
            try:
                side = OrderSide.SELL if "long" in cote else OrderSide.BUY
                ordre = MarketOrderRequest(
                    symbol=ticker, qty=quantite,
                    side=side, time_in_force=TimeInForce.DAY
                )
                client.submit_order(ordre)
                print(f"  ✅ {ticker} fermée")
            except Exception as e:
                print(f"  ❌ {ticker} : {e}")
    except Exception as e:
        print(f"  ❌ Erreur fermeture : {e}")

# ═══════════════════════════════════════════════════════════════
#  PORTEFEUILLES
# ═══════════════════════════════════════════════════════════════

swing_positions = {}   # max 4 LONG swing
scalp_positions = {}   # max 3 scalp (LONG + SHORT)

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS
# ═══════════════════════════════════════════════════════════════

def gerer_swing():
    if not swing_positions:
        return
    print("\n📊 Swing positions :")
    try:
        pos_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except:
        return

    for ticker, pos in list(swing_positions.items()):
        if ticker not in pos_alpaca:
            print(f"  🛑 {ticker} — SL touché Alpaca")
            del swing_positions[ticker]
            continue
        prix_actuel = float(pos_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(float(pos_alpaca[ticker].qty))
        print(f"  📌 {ticker} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain_pct:+.2f}%")
        try:
            sortir, raison = signal_sortie_swing(ticker, entree)
            if sortir:
                passer_vente(ticker, quantite, raison)
                del swing_positions[ticker]
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

def gerer_scalp():
    if not scalp_positions:
        return
    print("\n⚡ Scalp positions :")
    try:
        pos_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except:
        return

    for key, pos in list(scalp_positions.items()):
        ticker    = pos["ticker"]
        direction = pos["direction"]

        if ticker not in pos_alpaca:
            print(f"  ✅ {ticker} {direction} fermé (TP/SL Alpaca)")
            del scalp_positions[key]
            continue

        prix_actuel = float(pos_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        if direction == "SHORT": gain_pct = -gain_pct
        quantite = int(float(pos_alpaca[ticker].qty))

        print(f"  ⚡ {ticker} {direction} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain_pct:+.2f}%")

        try:
            df = telecharger_donnees(ticker)
            if df is not None:
                pattern = detecter_pattern_meche(df)
                if direction == "LONG" and pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
                    passer_vente(ticker, quantite, "PATTERN_OPPOSE")
                    del scalp_positions[key]
                elif direction == "SHORT" and pattern in ["MARTEAU", "ETOILE_MATIN"]:
                    try:
                        ordre = MarketOrderRequest(
                            symbol=ticker, qty=quantite,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY
                        )
                        client.submit_order(ordre)
                        print(f"  ✅ SHORT {ticker} clôturé")
                    except Exception as e:
                        print(f"  ❌ {e}")
                    del scalp_positions[key]
            time.sleep(DELAI_REQUETE)
        except Exception as e:
            print(f"  ⚠️ {ticker} scalp : {e}")

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def get_heure():
    now   = datetime.now(TZ_PARIS)
    heure = now.hour + now.minute / 60
    return heure, now.strftime("%H:%M")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot(pause_minutes=15):
    print("\n🤖 ROBOT HALAL V6 — OR & PÉTROLE — SWING + SCALP + 7 SHORTS")
    print("=" * 65)
    print(f"💰 Capital         : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs          : GLD | SGOL | USO")
    print(f"📈 Swing LONG      : max {MAX_LONG_SWING} | 9h→21h | Belkhayat+MACD+RSI+EMA")
    print(f"⚡ Scalp LONG      : mèches ×2 | 9h→17h | TP +0.8% SL -0.4%")
    print(f"🔴 SHORT           : 7 signaux | 9h→17h | TP +1.0% SL -0.5%")
    print(f"   → MECHE_×2 | ZONE_HAUTE | MACD_BAISSIER | RSI_SURACHETÉ")
    print(f"   → EMA_DEATH_CROSS | CASSURE_EMA50 | DIVERGENCE_BAISSIERE")
    print(f"🕘 Fermeture forcée: 21h00")
    print("=" * 65)

    fermeture_faite = False
    cycle = 0

    while True:
        cycle += 1
        h, heure_str = get_heure()
        print(f"\n{'='*65}")
        print(f"🔄 Cycle {cycle} | 🕐 {heure_str} Paris")

        # ── FERMETURE FORCÉE 21h00 ─────────────────────────────
        if h >= HEURE_FERMETURE:
            if not fermeture_faite:
                fermeture_forcee_tout()
                swing_positions.clear()
                scalp_positions.clear()
                fermeture_faite = True
            print("🌙 Marché fermé — reprise à 9h00")
            time.sleep(pause_minutes * 60)
            continue

        if h >= HEURE_OUVERTURE:
            fermeture_faite = False

        if h < HEURE_OUVERTURE:
            print(f"⏳ Marché fermé — pause {pause_minutes}min")
            time.sleep(pause_minutes * 60)
            continue

        # ── INFOS COMPTE ───────────────────────────────────────
        try:
            acc = client.get_account()
            print(f"💰 Cash: {float(acc.cash):.2f}$ | Portef: {float(acc.portfolio_value):.2f}$")
        except Exception as e:
            print(f"  ⚠️ Compte : {e}")

        # ── SYNC ALPACA ────────────────────────────────────────
        try:
            pos_reelles = {p.symbol for p in client.get_all_positions()}
            for t in list(swing_positions.keys()):
                if t not in pos_reelles:
                    print(f"  🛑 {t} swing fermée Alpaca")
                    del swing_positions[t]
            for k in list(scalp_positions.keys()):
                if scalp_positions[k]["ticker"] not in pos_reelles:
                    print(f"  ✅ {scalp_positions[k]['ticker']} scalp fermée Alpaca")
                    del scalp_positions[k]
        except Exception as e:
            print(f"  ⚠️ Sync : {e}")

        # ── GÉRER POSITIONS ────────────────────────────────────
        gerer_swing()
        if h < HEURE_FIN_SCALP:
            gerer_scalp()

        # ── CHERCHER SIGNAUX SWING LONG ────────────────────────
        places_swing = MAX_LONG_SWING - len(swing_positions)
        if places_swing > 0:
            print(f"\n📈 Recherche SWING LONG ({places_swing} place(s))...")
            try:
                pos_reelles = {p.symbol for p in client.get_all_positions()}
            except:
                pos_reelles = set()

            signaux_swing = []
            for ticker in actifs:
                if ticker in swing_positions or ticker in pos_reelles:
                    continue
                try:
                    sig = signal_swing_long(ticker)
                    if sig:
                        signaux_swing.append(sig)
                        print(f"  🚨 SWING {ticker} — {sig['signal']} score {sig['score']}/6")
                    time.sleep(DELAI_REQUETE)
                except Exception as e:
                    print(f"  ⚠️ {ticker} : {e}")
                    time.sleep(DELAI_REQUETE)

            signaux_swing.sort(key=lambda x: x["score"], reverse=True)
            for sig in signaux_swing:
                if len(swing_positions) >= MAX_LONG_SWING:
                    break
                order_id = passer_achat(sig)
                if order_id:
                    swing_positions[sig["ticker"]] = {
                        "prix_entree": sig["prix"],
                        "sl":          sig["sl"],
                        "order_id":    order_id,
                    }

        # ── CHERCHER SIGNAUX SCALP + SHORT ─────────────────────
        if h < HEURE_FIN_SCALP:
            places_scalp = MAX_SCALP - len(scalp_positions)
            if places_scalp > 0:
                print(f"\n⚡🔴 Recherche SCALP+SHORT ({places_scalp} place(s)) — 9h→17h...")
                try:
                    pos_reelles = {p.symbol for p in client.get_all_positions()}
                except:
                    pos_reelles = set()

                for ticker in actifs:
                    if len(scalp_positions) >= MAX_SCALP:
                        break

                    # Cherche d'abord un SHORT (priorité)
                    short_key  = f"{ticker}_short"
                    scalp_key  = f"{ticker}_scalp"

                    if short_key not in scalp_positions:
                        try:
                            sig_short = signal_short(ticker)
                            if sig_short:
                                print(f"  🔴 SHORT {ticker} — {sig_short['signal']} score {sig_short['score']}/7")
                                order_id = passer_short(sig_short)
                                if order_id:
                                    scalp_positions[short_key] = {
                                        "ticker":      ticker,
                                        "prix_entree": sig_short["prix"],
                                        "direction":   "SHORT",
                                        "sl":          sig_short["sl"],
                                        "tp":          sig_short["tp"],
                                        "order_id":    order_id,
                                    }
                            time.sleep(DELAI_REQUETE)
                        except Exception as e:
                            print(f"  ⚠️ {ticker} short : {e}")
                            time.sleep(DELAI_REQUETE)

                    # Cherche aussi un SCALP LONG
                    if scalp_key not in scalp_positions and len(scalp_positions) < MAX_SCALP:
                        try:
                            sig_scalp = signal_scalp_long(ticker)
                            if sig_scalp:
                                print(f"  🟢 SCALP LONG {ticker} — {sig_scalp['signal']}")
                                order_id = passer_achat(sig_scalp)
                                if order_id:
                                    scalp_positions[scalp_key] = {
                                        "ticker":      ticker,
                                        "prix_entree": sig_scalp["prix"],
                                        "direction":   "LONG",
                                        "sl":          sig_scalp["sl"],
                                        "tp":          sig_scalp["tp"],
                                        "order_id":    order_id,
                                    }
                            time.sleep(DELAI_REQUETE)
                        except Exception as e:
                            print(f"  ⚠️ {ticker} scalp : {e}")
                            time.sleep(DELAI_REQUETE)
            else:
                print("⚡ Scalp/Short plein (3/3)")
        else:
            print("⏰ Scalp/Short terminé (après 17h)")

        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Swing: {len(swing_positions)}/{MAX_LONG_SWING} | Scalp/Short: {len(scalp_positions)}/{MAX_SCALP}")
        for t, p in swing_positions.items():
            print(f"   📈 SWING {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$")
        for k, p in scalp_positions.items():
            emoji = "🔴" if p["direction"] == "SHORT" else "🟢"
            print(f"   {emoji} {p['direction']} {p['ticker']} | Entrée: {p['prix_entree']:.2f}$ | TP: {p['tp']:.2f}$ | SL: {p['sl']:.2f}$")

        print(f"\n⏳ Prochain scan dans {pause_minutes}min...")
        time.sleep(pause_minutes * 60)

# LANCEMENT
lancer_robot(pause_minutes=15)
