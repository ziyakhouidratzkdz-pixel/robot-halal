import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import datetime
import pytz
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# ═══════════════════════════════════════════════════════════════
#  CONNEXION ALPACA
# ═══════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("ALPACA_API_KEY", "PK4XAYAVTANIMZK6YT5DDNXZXT")
API_SECRET = os.environ.get("ALPACA_SECRET", "9iYFsPF1iv3mvVKDqA4dvF3w42RzGinyryixB8SxopsR")

client  = TradingClient(API_KEY, API_SECRET, paper=True)
account = client.get_account()

print("✅ Connexion Alpaca réussie !")
print(f"💰 Capital disponible : {float(account.cash):.2f}$")

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

RISQUE_PAR_TRADE   = 0.02    # 2% du capital par trade
MAX_POSITIONS      = 3       # max 3 trades en même temps
ATR_MULTIPLICATEUR = 1.5     # Stop Loss = entrée - (ATR x 1.5)
TP1_PCT     = 0.08           # +8%
TP2_PCT     = 0.15           # +15%
TP3_PCT     = 0.25           # +25%
SL_SECURISE = 0.005          # SL remonte à +0.5% au dessus entrée après TP1
TZ_PARIS    = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  MATIÈRES PREMIÈRES HALAL UNIQUEMENT
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
#  PORTEFEUILLE (positions ouvertes en mémoire)
# ═══════════════════════════════════════════════════════════════

portefeuille = {}

# ═══════════════════════════════════════════════════════════════
#  INDICATEURS BELKHAYAT
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
    try:
        df_w = yf.download(ticker, period="6mo", interval="1wk",
                           auto_adjust=True, progress=False)
        if df_w is None or len(df_w) < 10:
            return "INCONNUE"
        df_w.columns = df_w.columns.get_level_values(0)
        df_w["MA10"] = df_w["Close"].rolling(10).mean()
        last = df_w.iloc[-1]
        return "HAUSSE" if float(last["Close"]) > float(last["MA10"]) else "BAISSE"
    except:
        return "INCONNUE"

# ═══════════════════════════════════════════════════════════════
#  DÉTECTION SIGNAL 15MIN
# ═══════════════════════════════════════════════════════════════

def analyser_signal(ticker):
    try:
        df = yf.download(ticker, period="5d", interval="15m",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 50:
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

        # Signal ACHAT Belkhayat
        if (prix <= float(dernier["Zone_Basse"]) and
            k < 25 and d_val < 25 and
            vol_fort and tendance == "HAUSSE"):

            account     = client.get_account()
            capital     = float(account.cash)
            cap_trade   = capital * RISQUE_PAR_TRADE
            quantite    = max(1, int(cap_trade / prix))

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
    except Exception as e:
        print(f"  ⚠️ Erreur analyse {ticker}: {e}")
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
        print(f"  ✅ ACHAT : {signal['quantite']} x {signal['ticker']} à ~{signal['prix']:.2f}$")
        print(f"     SL: {signal['sl']:.2f}$ | TP1: {signal['tp1']:.2f}$ | TP2: {signal['tp2']:.2f}$ | TP3: {signal['tp3']:.2f}$")
        return str(result.id)
    except Exception as e:
        print(f"  ❌ Erreur achat : {e}")
        return None

def passer_ordre_vente(ticker, quantite):
    try:
        ordre = MarketOrderRequest(
            symbol=ticker,
            qty=quantite,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        client.submit_order(ordre)
        print(f"  ✅ VENTE : {quantite} x {ticker}")
    except Exception as e:
        print(f"  ❌ Erreur vente : {e}")

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS (TP/SL PROGRESSIF)
# ═══════════════════════════════════════════════════════════════

def gerer_positions():
    if not portefeuille:
        return

    print("\n📂 Gestion positions ouvertes :")
    try:
        positions_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except:
        return

    for ticker, pos in list(portefeuille.items()):
        if ticker not in positions_alpaca:
            print(f"  ℹ️ {ticker} fermée externement")
            del portefeuille[ticker]
            continue

        prix_actuel = float(positions_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(float(positions_alpaca[ticker].qty))

        print(f"\n  📌 {ticker} | Entrée: {entree:.2f}$ | Actuel: {prix_actuel:.2f}$ | {gain_pct:+.2f}%")
        print(f"     SL: {pos['sl']:.2f}$ | TP1: {pos['tp1']:.2f}$ | TP2: {pos['tp2']:.2f}$ | TP3: {pos['tp3']:.2f}$")

        fermer = False
        raison = ""

        # Stop Loss touché
        if prix_actuel <= pos["sl"]:
            fermer = True
            pnl    = (prix_actuel - entree) * quantite
            raison = f"🛑 Stop Loss | PnL: {pnl:+.2f}$"

        # TP3 → fermeture totale
        elif prix_actuel >= pos["tp3"]:
            fermer = True
            pnl    = (prix_actuel - entree) * quantite
            raison = f"🎉 TP3 +25% | PnL: {pnl:+.2f}$"

        # TP2 → SL remonte au TP1
        elif prix_actuel >= pos["tp2"] and not pos.get("tp2_atteint"):
            pos["tp2_atteint"] = True
            pos["sl"] = pos["tp1"]
            print(f"     ✅ TP2 +15% atteint ! SL sécurisé → {pos['tp1']:.2f}$")

        # TP1 → SL remonte au dessus entrée
        elif prix_actuel >= pos["tp1"] and not pos.get("tp1_atteint"):
            pos["tp1_atteint"] = True
            pos["sl"] = round(entree * (1 + SL_SECURISE), 4)
            print(f"     ✅ TP1 +8% atteint ! SL sécurisé → {pos['sl']:.2f}$")

        # Risque détecté : stoch inversé + volume faible
        else:
            try:
                df_risk = yf.download(ticker, period="1d", interval="5m",
                                      auto_adjust=True, progress=False)
                if df_risk is not None and len(df_risk) > 20:
                    df_risk.columns = df_risk.columns.get_level_values(0)
                    df_risk = calcul_stochastique(df_risk)
                    k_now   = float(df_risk["%K"].iloc[-1])
                    d_now   = float(df_risk["%D"].iloc[-1])
                    vol_now = float(df_risk["Volume"].iloc[-1])
                    vol_moy = df_risk["Volume"].rolling(20).mean().iloc[-1]
                    if (k_now > 75 and d_now > 75 and
                        vol_now < vol_moy * 0.5 and
                        pos.get("tp1_atteint")):
                        fermer = True
                        raison = f"⚠️ Risque détecté (stoch inversé + vol faible)"
            except:
                pass

        if fermer:
            print(f"     {raison}")
            passer_ordre_vente(ticker, quantite)
            del portefeuille[ticker]

# ═══════════════════════════════════════════════════════════════
#  HORAIRES PARIS
# ═══════════════════════════════════════════════════════════════

def est_heure_tradeable():
    now   = datetime.now(TZ_PARIS)
    heure = now.hour + now.minute / 60
    europe   = (9.0  <= heure <= 17.5)
    amerique = (15.5 <= heure <= 22.0)
    return europe or amerique, now.strftime("%H:%M %d/%m/%Y")

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot():
    PAUSE = 15 * 60  # 15 minutes entre chaque scan

    print("\n" + "="*60)
    print("🤖 ROBOT HALAL MATIÈRES PREMIÈRES - BELKHAYAT")
    print("="*60)
    print(f"💰 Capital    : {float(account.cash):.2f}$")
    print(f"🎯 Par trade  : {float(account.cash) * RISQUE_PAR_TRADE:.2f}$ (2%)")
    print(f"📦 Max trades : {MAX_POSITIONS}")
    print(f"🎯 TP1/2/3   : +8% / +15% / +25%")
    print(f"🕌 Halal      : Matières premières uniquement")
    print("="*60)

    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*60}")
        print(f"🔄 Cycle {cycle}")

        tradeable, heure = est_heure_tradeable()
        print(f"🕐 {heure} (Paris) | {'✅ Marché ouvert' if tradeable else '❌ Marché fermé'}")

        if not tradeable:
            print(f"⏳ Robot en pause, prochain scan dans 15min...")
            time.sleep(PAUSE)
            continue

        # 1. Gérer positions existantes
        gerer_positions()

        # 2. Chercher nouveaux signaux
        places = MAX_POSITIONS - len(portefeuille)
        if places > 0:
            print(f"\n🔍 Recherche de signaux ({places} place(s) dispo)...")
            for ticker, nom in matieres_premieres.items():
                if ticker in portefeuille:
                    continue
                if len(portefeuille) >= MAX_POSITIONS:
                    break
                print(f"  📊 Analyse {ticker} - {nom}...")
                signal = analyser_signal(ticker)
                if signal:
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
                time.sleep(0.5)
        else:
            print(f"\n📦 Portefeuille plein ({MAX_POSITIONS}/{MAX_POSITIONS})")

        # 3. Résumé
        try:
            acc = client.get_account()
            print(f"\n📊 RÉSUMÉ")
            print(f"   💰 Cash        : {float(acc.cash):.2f}$")
            print(f"   📈 Portefeuille: {float(acc.portfolio_value):.2f}$")
            print(f"   📦 Positions   : {len(portefeuille)}/{MAX_POSITIONS}")
            for t, p in portefeuille.items():
                print(f"   {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$")
        except:
            pass

        print(f"\n⏳ Prochain scan dans 15min...")
        time.sleep(PAUSE)

# ═══════════════════════════════════════════════════════════════
#  DÉMARRAGE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    lancer_robot()
