import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime
import pytz
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

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

CAPITAL_TOTAL      = float(account.cash)
RISQUE_PAR_TRADE   = 0.02       # 2% du capital par trade
MAX_POSITIONS      = 6          # ✅ 6 positions max simultanées
ATR_MULTIPLICATEUR = 1.5
TP1_PCT  = 0.04                 # ✅ +4%
TP2_PCT  = 0.08                 # ✅ +8%
TP3_PCT  = 0.15                 # ✅ +15%
SL_SECURISE = 0.005
TZ_PARIS = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  UNIVERS ÉLARGI — 25 ACTIFS HALAL
# ═══════════════════════════════════════════════════════════════

matieres_premieres = {
    "GLD":  "Or 🥇",
    "SLV":  "Argent 🥈",
    "SGOL": "Or physique",
    "PPLT": "Platine",
    "PALL": "Palladium",
    "USO":  "Pétrole brut 🛢️",
    "BNO":  "Brent Oil 🛢️",
    "UNG":  "Gaz naturel",
    "CPER": "Cuivre 🔶",
    "FCX":  "Freeport (Cuivre)",
    "NEM":  "Newmont (Or)",
    "AEM":  "Agnico Eagle (Or)",
    "WPM":  "Wheaton Precious",
    "BHP":  "BHP Group",
    "RIO":  "Rio Tinto",
    "VALE": "Vale (Fer/Nickel)",
    "WEAT": "Blé 🌾",
    "CORN": "Maïs 🌽",
    "SOYB": "Soja",
    "DBA":  "Agriculture",
    "MOO":  "Agribusiness",
    "PHO":  "Eau 💧",
    "CGW":  "Eau mondiale",
    "DJP":  "Commodities large",
    "PDBC": "Commodities actif",
}

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS
# ═══════════════════════════════════════════════════════════════

def calcul_atr(df, periode=14):
    df["H-L"]  = df["High"] - df["Low"]
    df["H-CP"] = abs(df["High"] - df["Close"].shift(1))
    df["L-CP"] = abs(df["Low"]  - df["Close"].shift(1))
    df["TR"]   = df[["H-L", "H-CP", "L-CP"]].max(axis=1)
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
    ema12            = df["Close"].ewm(span=12).mean()
    ema26            = df["Close"].ewm(span=26).mean()
    df["MACD"]       = ema12 - ema26
    df["Signal_MACD"]= df["MACD"].ewm(span=9).mean()
    return df

def calcul_zones(df, periode=20):
    df["Moyenne"]    = df["Close"].rolling(periode).mean()
    df["Ecart"]      = df["Close"].rolling(periode).std()
    df["Zone_Haute"] = df["Moyenne"] + 2 * df["Ecart"]
    df["Zone_Basse"] = df["Moyenne"] - 2 * df["Ecart"]
    df["Zone_Mid"]   = df["Moyenne"]
    return df

def calcul_tendance(ticker):
    for tentative in range(3):
        try:
            df_w = yf.download(ticker, period="3mo", interval="1d",
                               auto_adjust=True, progress=False)
            if df_w is None or len(df_w) < 20:
                return "NEUTRE"
            df_w.columns = df_w.columns.get_level_values(0)
            df_w["MA20"] = df_w["Close"].rolling(20).mean()
            last  = df_w.iloc[-1]
            close = float(last["Close"])
            ma20  = float(last["MA20"])
            if close > ma20 * 1.01:
                return "HAUSSE"
            elif close < ma20 * 0.99:
                return "BAISSE"
            else:
                return "NEUTRE"
        except Exception:
            time.sleep(5 * (tentative + 1))
    return "NEUTRE"

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT AVEC RETRY
# ═══════════════════════════════════════════════════════════════

def telecharger_donnees(ticker, period="5d", interval="15m"):
    for tentative in range(3):
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 30:
                return df
            time.sleep(3)
        except Exception as e:
            print(f"  ⚠️ {ticker} tentative {tentative+1} : {e}")
            time.sleep(10 * (tentative + 1))
    return None

# ═══════════════════════════════════════════════════════════════
#  3 TYPES DE SIGNAUX — SCORE MINIMUM 4/6
# ═══════════════════════════════════════════════════════════════

def analyser_signal(ticker):
    df = telecharger_donnees(ticker)
    if df is None:
        return None

    df.columns = df.columns.get_level_values(0)
    df = calcul_stochastique(df)
    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_zones(df)
    df = calcul_atr(df)

    if len(df) < 30:
        return None

    d    = df.iloc[-1]
    d_1  = df.iloc[-2]

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

    vol_moyen  = df["Volume"].rolling(20).mean().iloc[-1]
    vol_actuel = float(d["Volume"])
    vol_ok     = vol_actuel > vol_moyen * 1.0

    tendance    = calcul_tendance(ticker)
    tendance_ok = tendance in ["HAUSSE", "NEUTRE"]

    signal_type = None
    score = 0

    # ─── SIGNAL A : Rebond zone basse Belkhayat ───────────────
    if prix <= zone_b * 1.015 and k < 30 and d_val < 35 and tendance_ok:
        signal_type = "REBOND_ZONE_BASSE"
        score = 3
        if vol_ok:          score += 1
        if k > k_prev:      score += 1  # stoch remonte
        if rsi < 40:        score += 1

    # ─── SIGNAL B : Croisement MACD haussier ──────────────────
    elif (macd > macd_sig and macd_prev <= sig_prev
          and rsi > 35 and rsi < 65 and tendance_ok):
        signal_type = "CROISEMENT_MACD"
        score = 3
        if vol_ok:              score += 1
        if prix < zone_mid:     score += 1
        if k < 60:              score += 1

    # ─── SIGNAL C : RSI survendu + retournement stoch ─────────
    elif rsi < 35 and k > k_prev and k < 45 and tendance_ok and vol_ok:
        signal_type = "RSI_SURVENDU"
        score = 3
        if prix <= zone_b * 1.02:   score += 1
        if macd > macd_prev:        score += 1
        if k > d_val:               score += 1

    if signal_type is None or score < 4:
        return None

    capital_trade = CAPITAL_TOTAL * RISQUE_PAR_TRADE
    quantite      = max(1, int(capital_trade / prix))

    return {
        "ticker":      ticker,
        "prix":        prix,
        "signal_type": signal_type,
        "score":       score,
        "sl":          round(prix - (atr * ATR_MULTIPLICATEUR), 4),
        "tp1":         round(prix * (1 + TP1_PCT), 4),
        "tp2":         round(prix * (1 + TP2_PCT), 4),
        "tp3":         round(prix * (1 + TP3_PCT), 4),
        "quantite":    quantite,
        "capital":     round(quantite * prix, 2),
    }

# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════

def passer_ordre_achat(signal):
    try:
        ordre = MarketOrderRequest(
            symbol=signal["ticker"],
            qty=signal["quantite"],
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        result = client.submit_order(ordre)
        print(f"  ✅ ACHAT {signal['ticker']} | {signal['signal_type']} | Score {signal['score']}/6")
        print(f"     {signal['quantite']} actions x {signal['prix']:.2f}$ = {signal['capital']:.2f}$")
        print(f"     SL: {signal['sl']:.2f}$ | TP1: {signal['tp1']:.2f}$ | TP2: {signal['tp2']:.2f}$ | TP3: {signal['tp3']:.2f}$")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur achat {signal['ticker']} : {e}")
        return None

def passer_ordre_vente(ticker, quantite):
    try:
        ordre = MarketOrderRequest(
            symbol=ticker,
            qty=quantite,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        result = client.submit_order(ordre)
        print(f"  ✅ VENTE {quantite} x {ticker}")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur vente {ticker} : {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS TP/SL PROGRESSIF
# ═══════════════════════════════════════════════════════════════

portefeuille = {}

def gerer_positions():
    if not portefeuille:
        return

    print("\n📂 Positions ouvertes :")
    try:
        positions_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except Exception as e:
        print(f"  ❌ Erreur positions : {e}")
        return

    for ticker, pos in list(portefeuille.items()):
        if ticker not in positions_alpaca:
            print(f"  ℹ️ {ticker} fermée")
            del portefeuille[ticker]
            continue

        prix_actuel = float(positions_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(positions_alpaca[ticker].qty)

        print(f"  📌 {ticker} | {entree:.2f}$ → {prix_actuel:.2f}$ | {gain_pct:+.2f}%")

        fermer = False
        raison = ""

        if prix_actuel <= pos["sl"]:
            fermer = True
            raison = f"🛑 Stop Loss"
        elif prix_actuel >= pos["tp3"]:
            fermer = True
            raison = f"🎉 TP3 +15% !"
        elif prix_actuel >= pos["tp2"] and not pos.get("tp2_atteint"):
            pos["tp2_atteint"] = True
            pos["sl"] = pos["tp1"]
            print(f"     ✅ TP2 +8% ! SL → {pos['tp1']:.2f}$")
        elif prix_actuel >= pos["tp1"] and not pos.get("tp1_atteint"):
            pos["tp1_atteint"] = True
            pos["sl"] = round(entree * (1 + SL_SECURISE), 4)
            print(f"     ✅ TP1 +4% ! SL sécurisé → {pos['sl']:.2f}$")

        if fermer:
            pnl = (prix_actuel - entree) * quantite
            print(f"     {raison} | PnL: {pnl:+.2f}$")
            passer_ordre_vente(ticker, quantite)
            del portefeuille[ticker]

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def est_heure_tradeable():
    now   = datetime.now(TZ_PARIS)
    heure = now.hour + now.minute / 60
    tradeable = (9.0 <= heure <= 17.5) or (15.5 <= heure <= 22.0)
    return tradeable, now.strftime("%H:%M")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot(nb_cycles=9999, pause_minutes=15):
    print("\n🤖 ROBOT HALAL V2 — MULTI-SIGNAUX BELKHAYAT")
    print("=" * 60)
    print(f"💰 Capital       : {CAPITAL_TOTAL:.2f}$")
    print(f"🎯 Par trade     : {CAPITAL_TOTAL * RISQUE_PAR_TRADE:.2f}$ (2%)")
    print(f"📦 Max positions : {MAX_POSITIONS}")
    print(f"📈 3 Signaux     : REBOND_ZONE_BASSE | CROISEMENT_MACD | RSI_SURVENDU")
    print(f"🎯 TP1/2/3       : +4% / +8% / +15%")
    print(f"🌍 Actifs        : {len(matieres_premieres)}")
    print(f"⏱️  Scan          : toutes les {pause_minutes} minutes")
    print("=" * 60)

    for cycle in range(nb_cycles):
        print(f"\n{'='*60}")
        print(f"🔄 Cycle {cycle+1}")

        tradeable, heure = est_heure_tradeable()
        print(f"🕐 {heure} Paris | {'✅ Marché OUVERT' if tradeable else '❌ Marché FERMÉ'}")

        if not tradeable:
            print(f"⏳ Pause {pause_minutes}min...")
            time.sleep(pause_minutes * 60)
            continue

        try:
            account = client.get_account()
            print(f"💰 Cash: {float(account.cash):.2f}$ | Portef: {float(account.portfolio_value):.2f}$")
        except Exception as e:
            print(f"  ⚠️ Erreur compte : {e}")

        gerer_positions()

        places = MAX_POSITIONS - len(portefeuille)
        if places > 0:
            print(f"\n🔍 Scan {len(matieres_premieres)} actifs ({places} place(s) dispo)...")
            signaux_trouves = []

            for ticker, nom in matieres_premieres.items():
                if ticker in portefeuille:
                    continue
                try:
                    signal = analyser_signal(ticker)
                    if signal:
                        signaux_trouves.append(signal)
                        print(f"  🚨 {ticker} ({nom}) — {signal['signal_type']} score {signal['score']}/6")
                    time.sleep(3)
                except Exception as e:
                    time.sleep(5)
                    continue

            # Trier par meilleur score
            signaux_trouves.sort(key=lambda x: x["score"], reverse=True)
            print(f"\n  📊 {len(signaux_trouves)} signal(s) trouvé(s)")

            for signal in signaux_trouves:
                if len(portefeuille) >= MAX_POSITIONS:
                    break
                if signal["ticker"] in portefeuille:
                    continue
                order_id = passer_ordre_achat(signal)
                if order_id:
                    portefeuille[signal["ticker"]] = {
                        "prix_entree": signal["prix"],
                        "quantite":    signal["quantite"],
                        "sl":          signal["sl"],
                        "tp1":         signal["tp1"],
                        "tp2":         signal["tp2"],
                        "tp3":         signal["tp3"],
                        "tp1_atteint": False,
                        "tp2_atteint": False,
                        "order_id":    order_id,
                    }
        else:
            print("📦 Portefeuille plein (6/6)")

        print(f"\n📊 Positions: {len(portefeuille)}/{MAX_POSITIONS}")
        for t, p in portefeuille.items():
            print(f"   {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$ | TP1: {p['tp1']:.2f}$")

        print(f"\n⏳ Prochain scan dans {pause_minutes}min...")
        time.sleep(pause_minutes * 60)

    print("\n✅ Robot terminé.")

# LANCEMENT
lancer_robot(nb_cycles=9999, pause_minutes=15)
