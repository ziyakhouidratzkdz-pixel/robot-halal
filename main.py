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
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, PositionSide

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
RISQUE_PAR_TRADE   = 0.02        # 2% par trade
MAX_LONG_SWING     = 4           # Max 4 trades TYPE 1 (swing)
MAX_SCALP          = 3           # Max 3 trades TYPE 2 (scalp rapide)
ATR_MULT_SWING     = 1.5         # SL swing = 1.5x ATR
ATR_MULT_SCALP     = 0.5         # SL scalp = 0.5x ATR (très court)
TP_SCALP_PCT       = 0.008       # TP scalp = +0.8% (sortie rapide)
SL_SCALP_PCT       = 0.004       # SL scalp = -0.4% (serré)
MECHE_MULTIPLICATEUR = 3         # Mèche ≥ 3x le corps
HEURE_FERMETURE    = 21          # Fermeture forcée à 21h00
HEURE_OUVERTURE    = 9           # Ouverture à 9h00
HEURE_FIN_SCALP    = 17          # Scalp uniquement jusqu'à 17h
TZ_PARIS           = pytz.timezone("Europe/Paris")

# ═══════════════════════════════════════════════════════════════
#  3 ACTIFS HALAL UNIQUEMENT
# ═══════════════════════════════════════════════════════════════

actifs = {
    "GLD":  "Or 🥇 (équivalent XAUUSD)",
    "SGOL": "Or physique 🥇 (équivalent GC)",
    "USO":  "Pétrole 🛢️ (équivalent CL)",
}

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
#  DÉTECTION GRANDES MÈCHES (×3) — PATTERNS JAPONAIS
# ═══════════════════════════════════════════════════════════════

def detecter_pattern_meche(df):
    """
    Détecte les patterns de grandes mèches sur la dernière bougie.
    Retourne : 'MARTEAU', 'MARTEAU_INVERSE', 'ETOILE_MATIN', 
               'ETOILE_SOIR', ou None
    Règle : mèche ≥ 3× le corps de la bougie
    """
    if len(df) < 3:
        return None

    # Dernière bougie
    d   = df.iloc[-1]
    d_1 = df.iloc[-2]
    d_2 = df.iloc[-3]

    def analyse_bougie(row):
        ouv   = float(row["Open"])
        clo   = float(row["Close"])
        haut  = float(row["High"])
        bas   = float(row["Low"])
        corps = abs(clo - ouv)
        if corps < 1e-9:
            corps = 1e-9
        meche_haute = haut - max(ouv, clo)
        meche_basse = min(ouv, clo) - bas
        return corps, meche_haute, meche_basse, ouv, clo

    corps, meche_h, meche_b, ouv, clo = analyse_bougie(d)
    corps_1, _, _, ouv_1, clo_1       = analyse_bougie(d_1)
    corps_2, _, _, ouv_2, clo_2       = analyse_bougie(d_2)

    # ── MARTEAU : grande mèche BASSE ≥ 3× corps → signal ACHAT
    if meche_b >= MECHE_MULTIPLICATEUR * corps and meche_h < corps:
        return "MARTEAU"

    # ── MARTEAU INVERSE : grande mèche HAUTE ≥ 3× corps → signal ACHAT
    if meche_h >= MECHE_MULTIPLICATEUR * corps and meche_b < corps:
        return "MARTEAU_INVERSE"

    # ── ÉTOILE DU MATIN : 3 bougies (rouge + petite + verte) → ACHAT
    bougie_1_rouge  = clo_2 < ouv_2                        # 1ère bougie rouge
    bougie_2_petite = corps_1 < corps_2 * 0.3              # 2ème petite
    bougie_3_verte  = clo > ouv and clo > (ouv_2 + clo_2) / 2  # 3ème verte
    if bougie_1_rouge and bougie_2_petite and bougie_3_verte:
        return "ETOILE_MATIN"

    # ── ÉTOILE DU SOIR : 3 bougies (verte + petite + rouge) → VENTE/SHORT
    bougie_1_verte  = clo_2 > ouv_2                        # 1ère bougie verte
    bougie_2_petite2= corps_1 < corps_2 * 0.3              # 2ème petite
    bougie_3_rouge  = clo < ouv and clo < (ouv_2 + clo_2) / 2  # 3ème rouge
    if bougie_1_verte and bougie_2_petite2 and bougie_3_rouge:
        return "ETOILE_SOIR"

    return None

# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT AVEC RETRY
# ═══════════════════════════════════════════════════════════════

def telecharger_donnees(ticker, period="5d", interval="15m"):
    for tentative in range(3):
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 30:
                df.columns = df.columns.get_level_values(0)
                return df
            time.sleep(3)
        except Exception as e:
            print(f"  ⚠️ {ticker} tentative {tentative+1} : {e}")
            time.sleep(10 * (tentative + 1))
    return None

# ═══════════════════════════════════════════════════════════════
#  TYPE 1 — SIGNAL SWING LONG (Belkhayat + RSI + MACD)
#  Max 4 trades, lancés à partir de 9h, fermés à 21h
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

    vol_moyen = df["Volume"].rolling(20).mean().iloc[-1]
    vol_ok    = float(d["Volume"]) > vol_moyen

    tendance    = calcul_tendance(ticker)
    tendance_ok = tendance in ["HAUSSE", "NEUTRE"]

    signal_type = None
    score = 0

    # Rebond zone basse Belkhayat
    if prix <= zone_b * 1.015 and k < 30 and d_val < 35 and tendance_ok:
        signal_type = "BELKHAYAT_REBOND"
        score = 3
        if vol_ok:      score += 1
        if k > k_prev:  score += 1
        if rsi < 40:    score += 1

    # Croisement MACD haussier
    elif (macd > macd_sig and macd_prev <= sig_prev
          and rsi > 35 and rsi < 65 and tendance_ok):
        signal_type = "MACD_HAUSSIER"
        score = 3
        if vol_ok:          score += 1
        if prix < zone_mid: score += 1
        if k < 60:          score += 1

    # RSI survendu + retournement
    elif rsi < 35 and k > k_prev and k < 45 and tendance_ok and vol_ok:
        signal_type = "RSI_SURVENDU"
        score = 3
        if prix <= zone_b * 1.02: score += 1
        if macd > macd_prev:      score += 1
        if k > d_val:             score += 1

    if signal_type is None or score < 4:
        return None

    quantite = max(1, int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix))
    sl       = round(prix - (atr * ATR_MULT_SWING), 2)

    return {
        "ticker":      ticker,
        "type":        "SWING_LONG",
        "signal":      signal_type,
        "score":       score,
        "prix":        prix,
        "sl":          sl,
        "quantite":    quantite,
        "capital":     round(quantite * prix, 2),
        "direction":   "LONG",
    }

# ═══════════════════════════════════════════════════════════════
#  TYPE 2 — SIGNAL SCALP RAPIDE (Mèches + confirmation)
#  2-3 trades, toute la journée 9h→17h, TP/SL très courts
#  LONG sur Marteau / SHORT sur Marteau inverse + Étoile soir
# ═══════════════════════════════════════════════════════════════

def signal_scalp(ticker):
    df = telecharger_donnees(ticker)
    if df is None or len(df) < 30:
        return None

    df = calcul_rsi(df)
    df = calcul_macd(df)
    df = calcul_zones_belkhayat(df)
    df = calcul_atr(df)

    pattern = detecter_pattern_meche(df)
    if pattern is None:
        return None

    d    = df.iloc[-1]
    d_1  = df.iloc[-2]
    prix = float(d["Close"])
    atr  = float(d["ATR"])
    rsi  = float(d["RSI"])
    macd = float(d["MACD"])
    macd_sig  = float(d["Signal_MACD"])
    macd_prev = float(d_1["MACD"])
    sig_prev  = float(d_1["Signal_MACD"])

    direction   = None
    signal_type = pattern

    # ── LONG : Marteau ou Étoile du matin
    if pattern in ["MARTEAU", "ETOILE_MATIN"]:
        # Confirmation supplémentaire
        if rsi < 65 and macd >= macd_prev:
            direction = "LONG"

    # ── SHORT : Marteau inverse ou Étoile du soir
    elif pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        # Confirmation supplémentaire
        if rsi > 35 and macd <= macd_prev:
            direction = "SHORT"

    if direction is None:
        return None

    quantite = max(1, int((CAPITAL_TOTAL * RISQUE_PAR_TRADE) / prix))

    if direction == "LONG":
        sl = round(prix * (1 - SL_SCALP_PCT), 2)
        tp = round(prix * (1 + TP_SCALP_PCT), 2)
    else:  # SHORT
        sl = round(prix * (1 + SL_SCALP_PCT), 2)
        tp = round(prix * (1 - TP_SCALP_PCT), 2)

    return {
        "ticker":    ticker,
        "type":      "SCALP",
        "signal":    signal_type,
        "direction": direction,
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
    zone_mid = float(d["Zone_Mid"])

    if prix < prix_entree:
        return False, None

    # Zone haute Belkhayat + stoch suracheté
    if prix >= zone_h * 0.985 and k > 70 and k < k_prev and rsi > 65:
        return True, "ZONE_HAUTE_BELKHAYAT"

    # MACD croise vers le bas
    if macd < macd_sig and macd_prev >= sig_prev and rsi > 50:
        return True, "MACD_BAISSIER"

    # RSI suracheté + retournement
    if rsi > 72 and k < k_prev:
        return True, "RSI_SURACHETÉ"

    # Pattern bougie de retournement
    pattern = detecter_pattern_meche(df)
    if pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
        return True, f"PATTERN_{pattern}"

    return False, None

# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════

def passer_achat(signal):
    """Ordre LONG avec SL automatique Alpaca"""
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
    """Ordre SHORT avec SL et TP automatiques"""
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
        print(f"  🔴 SHORT {signal['ticker']} | {signal['signal']} | {signal['quantite']}x{signal['prix']:.2f}$")
        print(f"     SL: {signal['sl']:.2f}$ | TP: {signal['tp']:.2f}$")
        return result.id
    except Exception as e:
        print(f"  ❌ Erreur short {signal['ticker']} : {e}")
        return None

def passer_vente(ticker, quantite, raison):
    try:
        ordre = MarketOrderRequest(
            symbol=ticker,
            qty=quantite,
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
    """Ferme TOUTES les positions à 21h00 sans exception"""
    print("\n🔔 21h00 — FERMETURE FORCÉE DE TOUTES LES POSITIONS")
    try:
        positions = client.get_all_positions()
        if not positions:
            print("  ℹ️ Aucune position ouverte")
            return
        for pos in positions:
            ticker   = pos.symbol
            quantite = abs(int(float(pos.qty)))
            side_pos = pos.side
            try:
                if str(side_pos) == "long":
                    ordre = MarketOrderRequest(
                        symbol=ticker,
                        qty=quantite,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY
                    )
                else:
                    ordre = MarketOrderRequest(
                        symbol=ticker,
                        qty=quantite,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY
                    )
                client.submit_order(ordre)
                print(f"  ✅ {ticker} fermée ({quantite} actions)")
            except Exception as e:
                print(f"  ❌ Erreur fermeture {ticker} : {e}")
    except Exception as e:
        print(f"  ❌ Erreur récupération positions : {e}")

# ═══════════════════════════════════════════════════════════════
#  PORTEFEUILLES
# ═══════════════════════════════════════════════════════════════

swing_positions  = {}   # TYPE 1 — max 4 swings long
scalp_positions  = {}   # TYPE 2 — max 3 scalps rapides

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS SWING
# ═══════════════════════════════════════════════════════════════

def gerer_swing():
    if not swing_positions:
        return
    print("\n📊 Swing positions :")
    try:
        positions_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except:
        return

    for ticker, pos in list(swing_positions.items()):
        if ticker not in positions_alpaca:
            print(f"  🛑 {ticker} — SL touché (Alpaca)")
            del swing_positions[ticker]
            continue

        prix_actuel = float(positions_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(float(positions_alpaca[ticker].qty))

        print(f"  📌 {ticker} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain_pct:+.2f}%")

        try:
            sortir, raison = signal_sortie_swing(ticker, entree)
            if sortir:
                passer_vente(ticker, quantite, raison)
                del swing_positions[ticker]
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️ {ticker} : {e}")

# ═══════════════════════════════════════════════════════════════
#  GESTION POSITIONS SCALP
# ═══════════════════════════════════════════════════════════════

def gerer_scalp():
    if not scalp_positions:
        return
    print("\n⚡ Scalp positions :")
    try:
        positions_alpaca = {p.symbol: p for p in client.get_all_positions()}
    except:
        return

    for ticker, pos in list(scalp_positions.items()):
        # Scalp fermé par Alpaca via TP/SL automatique
        if ticker not in positions_alpaca:
            print(f"  ✅ {ticker} scalp fermé (TP/SL Alpaca)")
            del scalp_positions[ticker]
            continue

        prix_actuel = float(positions_alpaca[ticker].current_price)
        entree      = pos["prix_entree"]
        direction   = pos["direction"]
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        quantite    = int(float(positions_alpaca[ticker].qty))

        if direction == "SHORT":
            gain_pct = -gain_pct

        print(f"  ⚡ {ticker} {direction} | {entree:.2f}$→{prix_actuel:.2f}$ | {gain_pct:+.2f}%")

        # Sortie scalp sur pattern opposé
        pattern = detecter_pattern_meche(telecharger_donnees(ticker) or pd.DataFrame())
        if direction == "LONG" and pattern in ["MARTEAU_INVERSE", "ETOILE_SOIR"]:
            passer_vente(ticker, quantite, f"PATTERN_OPPOSE_{pattern}")
            del scalp_positions[ticker]
        elif direction == "SHORT" and pattern in ["MARTEAU", "ETOILE_MATIN"]:
            # Rachat pour clore le short
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
            del scalp_positions[ticker]

# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════

def get_heure():
    now   = datetime.now(TZ_PARIS)
    heure = now.hour + now.minute / 60
    return heure, now.strftime("%H:%M")

def est_ouvert():
    h, _ = get_heure()
    return HEURE_OUVERTURE <= h < HEURE_FERMETURE

def scalp_autorise():
    h, _ = get_heure()
    return HEURE_OUVERTURE <= h < HEURE_FIN_SCALP

def fermeture_imminente():
    h, _ = get_heure()
    return h >= HEURE_FERMETURE

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def lancer_robot(pause_minutes=15):
    print("\n🤖 ROBOT HALAL V5 — OR & PÉTROLE — SWING + SCALP")
    print("=" * 60)
    print(f"💰 Capital         : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs          : GLD | SGOL | USO")
    print(f"📈 TYPE 1 Swing    : max {MAX_LONG_SWING} LONG | 9h→21h | Belkhayat+RSI+MACD")
    print(f"⚡ TYPE 2 Scalp    : max {MAX_SCALP} trades | 9h→17h | LONG+SHORT sur mèches ×3")
    print(f"🕘 Fermeture forcée: 21h00 tous les jours")
    print(f"🛑 SL Swing        : ATR ×{ATR_MULT_SWING}")
    print(f"🛑 SL Scalp        : {SL_SCALP_PCT*100:.1f}% | TP Scalp : {TP_SCALP_PCT*100:.1f}%")
    print("=" * 60)

    fermeture_faite = False
    cycle = 0

    while True:
        cycle += 1
        h, heure_str = get_heure()
        print(f"\n{'='*60}")
        print(f"🔄 Cycle {cycle} | 🕐 {heure_str} Paris")

        # ── FERMETURE FORCÉE 21h00 ─────────────────────────────
        if fermeture_imminente():
            if not fermeture_faite:
                fermeture_forcee_tout()
                swing_positions.clear()
                scalp_positions.clear()
                fermeture_faite = True
            print("🌙 Marché fermé jusqu'à 9h00")
            time.sleep(pause_minutes * 60)
            continue

        # Reset flag fermeture chaque matin
        if h >= HEURE_OUVERTURE:
            fermeture_faite = False

        # ── MARCHÉ FERMÉ ───────────────────────────────────────
        if not est_ouvert():
            print(f"⏳ Marché fermé — pause {pause_minutes}min")
            time.sleep(pause_minutes * 60)
            continue

        # ── INFOS COMPTE ───────────────────────────────────────
        try:
            acc = client.get_account()
            print(f"💰 Cash: {float(acc.cash):.2f}$ | Portef: {float(acc.portfolio_value):.2f}$")
        except Exception as e:
            print(f"  ⚠️ {e}")

        # ── SYNC POSITIONS ALPACA ──────────────────────────────
        try:
            pos_reelles = {p.symbol for p in client.get_all_positions()}
            for t in list(swing_positions.keys()):
                if t not in pos_reelles:
                    print(f"  🛑 {t} swing — SL touché Alpaca")
                    del swing_positions[t]
            for t in list(scalp_positions.keys()):
                if t not in pos_reelles:
                    print(f"  ✅ {t} scalp — TP/SL Alpaca")
                    del scalp_positions[t]
        except Exception as e:
            print(f"  ⚠️ Sync : {e}")

        # ── GÉRER POSITIONS OUVERTES ───────────────────────────
        gerer_swing()
        if scalp_autorise():
            gerer_scalp()

        # ── CHERCHER NOUVEAUX SIGNAUX SWING ───────────────────
        places_swing = MAX_LONG_SWING - len(swing_positions)
        if places_swing > 0:
            print(f"\n📈 Recherche SWING ({places_swing} place(s))...")
            try:
                pos_reelles = {p.symbol for p in client.get_all_positions()}
            except:
                pos_reelles = set()

            signaux = []
            for ticker in actifs:
                if ticker in swing_positions or ticker in pos_reelles:
                    continue
                try:
                    sig = signal_swing_long(ticker)
                    if sig:
                        signaux.append(sig)
                        print(f"  🚨 SWING {ticker} — {sig['signal']} score {sig['score']}/6")
                    time.sleep(3)
                except Exception as e:
                    time.sleep(3)

            signaux.sort(key=lambda x: x["score"], reverse=True)
            for sig in signaux:
                if len(swing_positions) >= MAX_LONG_SWING:
                    break
                order_id = passer_achat(sig)
                if order_id:
                    swing_positions[sig["ticker"]] = {
                        "prix_entree": sig["prix"],
                        "sl":          sig["sl"],
                        "order_id":    order_id,
                    }

        # ── CHERCHER NOUVEAUX SIGNAUX SCALP ───────────────────
        if scalp_autorise():
            places_scalp = MAX_SCALP - len(scalp_positions)
            if places_scalp > 0:
                print(f"\n⚡ Recherche SCALP ({places_scalp} place(s)) — 9h→17h...")
                try:
                    pos_reelles = {p.symbol for p in client.get_all_positions()}
                except:
                    pos_reelles = set()

                for ticker in actifs:
                    if len(scalp_positions) >= MAX_SCALP:
                        break
                    # Un actif peut avoir 1 swing ET 1 scalp en même temps
                    scalp_key = f"{ticker}_scalp"
                    if scalp_key in scalp_positions:
                        continue
                    try:
                        sig = signal_scalp(ticker)
                        if sig:
                            print(f"  🕯️ SCALP {ticker} {sig['direction']} — {sig['signal']}")
                            if sig["direction"] == "LONG":
                                order_id = passer_achat(sig)
                            else:
                                order_id = passer_short(sig)
                            if order_id:
                                scalp_positions[scalp_key] = {
                                    "ticker":      ticker,
                                    "prix_entree": sig["prix"],
                                    "direction":   sig["direction"],
                                    "sl":          sig["sl"],
                                    "tp":          sig["tp"],
                                    "order_id":    order_id,
                                }
                        time.sleep(3)
                    except Exception as e:
                        time.sleep(3)
            else:
                print("⚡ Scalp plein (3/3)")
        else:
            print("⏰ Scalp terminé pour aujourd'hui (après 17h)")

        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Swing: {len(swing_positions)}/{MAX_LONG_SWING} | Scalp: {len(scalp_positions)}/{MAX_SCALP}")
        for t, p in swing_positions.items():
            print(f"   📈 SWING {t} | Entrée: {p['prix_entree']:.2f}$ | SL: {p['sl']:.2f}$")
        for k, p in scalp_positions.items():
            print(f"   ⚡ SCALP {p['ticker']} {p['direction']} | Entrée: {p['prix_entree']:.2f}$ | TP: {p['tp']:.2f}$ | SL: {p['sl']:.2f}$")

        print(f"\n⏳ Prochain scan dans {pause_minutes}min...")
        time.sleep(pause_minutes * 60)

# LANCEMENT
lancer_robot(pause_minutes=15)
