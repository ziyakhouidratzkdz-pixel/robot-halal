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
RISQUE_PAR_TRADE   = 0.02
MAX_POSITIONS      = 3
ATR_MULTIPLICATEUR = 1.5
TP1_PCT = 0.08
TP2_PCT = 0.15
TP3_PCT = 0.25
SL_SECURISE = 0.005
TZ_PARIS = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  MATIÈRES PREMIÈRES HALAL
# ═══════════════════════════════════════════════════════════════

matieres_premieres = {
    "GLD":  "Or 🥇",
    "SLV":  "Argent 🥈",
    "CPER": "Cuivre 🔶",
    "USO":  "Pétrole brut 🛢️",
    "BNO":  "Brent Oil 🛢️",
    "PPLT": "Platine",
    "SGOL": "Or physique",
    "NEM":  "Newmont (Or minier)",
    "FCX":  "Freeport McMoRan",
    "BHP":  "BHP Group",
    "RIO":  "Rio Tinto",
    "WEAT": "Blé 🌾",
    "CORN": "Maïs 🌽",
    "SOYB": "Soja",
    "DBA":  "Agriculture",
    "PHO":  "Eau 💧",
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
    df["%K"]  = 100 * (df["Close"] - low_min) / (high_max - low_min)
    df["%D"]  = df["%K"].rolling(d).mean()
    return df

def calcul_zones(df, periode=20):
    df["Moyenne"]    = df["Close"].rolling(periode).mean()
    df["Ecart"]      = df["Close"].rolling(periode).std()
    df["Zone_Haute"] = df["Moyenne"] + 2 * df["Ecart"]
    df["Zone_Basse"] = df["Moyenne"] - 2 * df["Ecart"]
    return df

def calcul_tendance(ticker):
    for tentative in range(3):
        try:
            df_w = yf.download(ticker, period="6mo", interval="1wk",
                               auto_adjust=True, progress=False)
            if df_w is None or len(df_w) < 10:
                return "INCONNUE"
            df_w.columns = df_w.columns.get_level_values(0)
            df_w["MA10"] = df_w["Close"].rolling(10).mean()
            last = df_w.iloc[-1]
            return "HAUSSE" if float(last["Close"]) > float(last["MA10"]) else "BAISSE"
        except Exception as e:
            print(f"  ⚠️ Tendance {ticker} tentative {tentative+1}/3 : {e}")
            time.sleep(5 * (tentative + 1))
    return "INCONNUE"

# ═══════════════════════════════════════════════════════════════
#  SIGNAL 15MIN — avec retry anti-rate-limit
# ═══════════════════════════════════════════════════════════════

def telecharger_donnees(ticker, period="5d", interval="15m", max_tentatives=3):
    for tentative in range(max_tentatives):
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 50:
                return df
            time.sleep(3)
        except Exception as e:
            attente = 10 * (tentative + 1)
            print(f"  ⚠️ Yahoo Finance {ticker} (tentative {tentative+1}/{max_tentatives}) : {e}")
            print(f"  ⏳ Attente {attente}s...")
            time.sleep(attente)
    return None

def analyser_signal(ticker):
    df = telecharger_donnees(ticker)
    if df is None:
        return None

    df.columns = df.columns.get_level_values(0)
    df = calcul_stochastique(df)
    df = calcul_zones(df)
    df = calcul_atr(df)

    dernier   = df.iloc[-1]
    prix      = float(dernier["Close"])
    k         = float(dernier["%K"])
    d_val     = float(dernier["%D"])
    atr       = float(dernier["ATR"])
    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_fort  = float(dernier["Volume"]) > vol_moyen * 1.5
    tendance  = calcul_tendance(ticker)

    if (prix <= float(dernier["Zone_Basse"]) and
        k < 25 and d_val < 25 and
        vol_fort and tendance == "HAUSSE"):

        capital_trade = CAPITAL_TOTAL * RISQUE_PAR_TRADE
        quantite      = max(1, int(capital_trade / prix))

        return {
            "ticker":   ticker,
            "prix":     prix,
            "sl":       round(prix - (atr * ATR_MULTIPLICATEUR), 4),
            "tp1":      round(prix * (1 + TP1_PCT), 4),
            "tp2":      round(prix * (1 + TP2_PCT), 4),
            "tp3":      round(prix * (1 + TP3_PCT), 4),
            "quantite": quantite,
            "capital":  round(quantite * prix, 2),
        }
    return None

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
        print(f"  ✅ ORDRE ACHAT : {signal['quantite']} x {signal['ticker']} à ~{signal['prix']:.2f}$")
        print(f"     Order ID : {result.id}")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur ordre achat : {e}")
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
        print(f"  ✅ VENTE : {quantite} x {ticker}")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur vente : {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS TP/SL PROGRESSIF
# ═══════════════════════════════════════════════════════════════

portefeuille = {}

def gerer_positions():
    if not portefeuille:
        return

    print("\n📂 Gestion positions ouvertes :")
    try:
        positions_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except Exception as e:
        print(f"  ❌ Erreur récupération positions : {e}")
        return

    for ticker, pos in list(portefeuille.items()):
        if ticker not in positions_alpaca:
            print(f"  ℹ️ {ticker} déjà fermée")
            del portefeuille[ticker]
            continue

        prix_actuel = float(positions_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(positions_alpaca[ticker].qty)

        print(f"\n  📌 {ticker} | Entrée: {entree:.2f}$ | Actuel: {prix_actuel:.2f}$ | {gain_pct:+.2f}%")

        fermer = False
        raison = ""

        if prix_actuel <= pos["sl"]:
            fermer = True
            raison = f"🛑 Stop Loss touché {prix_actuel:.2f}$"
        elif prix_actuel >= pos["tp3"]:
            fermer = True
            raison = f"🎉 TP3 +25% atteint !"
        elif prix_actuel >= pos["tp2"] and not pos.get("tp2_atteint"):
            pos["tp2_atteint"] = True
            pos["sl"] = pos["tp1"]
            print(f"     ✅ TP2 atteint ! SL → {pos['tp1']:.2f}$")
        elif prix_actuel >= pos["tp1"] and not pos.get("tp1_atteint"):
            pos["tp1_atteint"] = True
            pos["sl"] = round(entree * (1 + SL_SECURISE), 4)
            print(f"     ✅ TP1 atteint ! SL sécurisé → {pos['sl']:.2f}$")

        if fermer:
            print(f"     {raison}")
            pnl = (prix_actuel - entree) * quantite
            print(f"     💰 PnL : {pnl:+.2f}$")
            passer_ordre_vente(ticker, quantite)
            del portefeuille[ticker]

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def est_heure_tradeable():
    now   = datetime.now(TZ_PARIS)
    heure = now.hour + now.minute / 60
    # Marchés US : 15h30 → 22h00 Paris | Pré-marché : 9h00 → 15h30
    tradeable = (9.0 <= heure <= 17.5) or (15.5 <= heure <= 22.0)
    return tradeable, now.strftime("%H:%M")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot(nb_cycles=999, pause_minutes=15):
    print("\n🤖 ROBOT HALAL MATIÈRES PREMIÈRES - BELKHAYAT + ALPACA")
    print("=" * 60)
    print(f"💰 Capital    : {CAPITAL_TOTAL:.2f}$")
    print(f"🎯 Par trade  : {CAPITAL_TOTAL * RISQUE_PAR_TRADE:.2f}$ (2%)")
    print(f"📦 Max trades : {MAX_POSITIONS}")
    print(f"🎯 TP1/2/3    : +8% / +15% / +25%")
    print(f"⏱️  Scan       : toutes les {pause_minutes} minutes")
    print("=" * 60)

    for cycle in range(nb_cycles):
        print(f"\n{'='*60}")
        print(f"🔄 Cycle {cycle+1}")

        tradeable, heure = est_heure_tradeable()
        print(f"🕐 {heure} (Paris) | Marché : {'✅ Ouvert' if tradeable else '❌ Fermé'}")

        if not tradeable:
            print(f"⏳ Marché fermé — pause {pause_minutes}min...")
            time.sleep(pause_minutes * 60)
            continue

        # Mise à jour capital
        try:
            account = client.get_account()
            capital_dispo = float(account.cash)
            print(f"💰 Cash disponible : {capital_dispo:.2f}$")
        except Exception as e:
            print(f"  ⚠️ Erreur compte : {e}")

        # Gérer positions ouvertes
        gerer_positions()

        # Chercher nouveaux signaux
        places = MAX_POSITIONS - len(portefeuille)
        if places > 0:
            print(f"\n🔍 Recherche signaux ({places} place(s) disponible(s))...")
            for ticker, nom in matieres_premieres.items():
                if ticker in portefeuille or len(portefeuille) >= MAX_POSITIONS:
                    continue
                try:
                    print(f"  📊 Analyse {ticker} ({nom})...", end=" ")
                    signal = analyser_signal(ticker)
                    if signal:
                        print(f"🚨 SIGNAL !")
                        order_id = passer_ordre_achat(signal)
                        if order_id:
                            portefeuille[ticker] = {
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
                            print(f"     SL: {signal['sl']:.2f}$ | TP1: {signal['tp1']:.2f}$ | TP2: {signal['tp2']:.2f}$ | TP3: {signal['tp3']:.2f}$")
                    else:
                        print("pas de signal")
                    # ✅ DÉLAI ANTI-RATE-LIMIT Yahoo Finance
                    time.sleep(3)
                except Exception as e:
                    print(f"erreur : {e}")
                    time.sleep(5)
                    continue
        else:
            print("📦 Portefeuille plein, pas de nouveau signal")

        # Résumé cycle
        print(f"\n📊 Positions actives : {len(portefeuille)}/{MAX_POSITIONS}")
        for t, p in portefeuille.items():
            print(f"   {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$ | TP1: {p['tp1']:.2f}$")

        print(f"\n⏳ Prochain scan dans {pause_minutes}min...")
        time.sleep(pause_minutes * 60)

    print("\n✅ Robot terminé.")

# LANCEMENT
lancer_robot(nb_cycles=999, pause_minutes=15)
