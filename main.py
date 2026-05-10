
Copier

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
#  CONFIGURATION — UT BOT + GESTION CAPITAL
# ═══════════════════════════════════════════════════════════════
 
CAPITAL_TOTAL    = float(account.cash)
MAX_LOTS         = 5          # Max 5 actions par trade
MAX_TRADES_JOUR  = 10         # Max 10 trades par jour
PERTE_MAX_JOUR   = 0.03       # Stop si -3% du capital
RISQUE_PCT       = 0.01       # Risque 1% du capital par trade
 
# UT Bot paramètres
UT_KEY_VALUE     = 1.0        # Sensibilité UT Bot (1.0 = standard)
UT_ATR_PERIOD    = 10         # Période ATR pour UT Bot
 
# EMA
EMA_PERIODE      = 200        # EMA200 filtre tendance
 
# Sorties
PARTIAL_MULT     = 1.5        # Sortie partielle à 1.5R
TP_MULT          = 3.0        # TP final à 3R
BE_PCT           = 0.5        # Breakeven à 50% du SL
 
# Horaires Paris
HEURE_DEBUT      = 9          # 9h00 Paris
HEURE_FIN_TRADE  = 17         # Plus de nouveaux trades après 17h
HEURE_FERMETURE  = 21         # Fermeture forcée 21h
 
PAUSE_MIN        = 5          # Scan toutes les 5 minutes
TZ_PARIS         = pytz.timezone("Europe/Paris")
TZ_NY            = pytz.timezone("America/New_York")
 
# Timeframes
TF_5MIN  = TimeFrame(5,  TimeFrameUnit.Minute)
TF_1H    = TimeFrame(1,  TimeFrameUnit.Hour)
TF_DAY   = TimeFrame(1,  TimeFrameUnit.Day)
 
# ═══════════════════════════════════════════════════════════════
#  3 ACTIFS HALAL
# ═══════════════════════════════════════════════════════════════
 
actifs = {
    "GLD":  "Or 🥇",
    "SGOL": "Or physique 🥇",
    "USO":  "Pétrole 🛢️",
}
 
# ═══════════════════════════════════════════════════════════════
#  COMPTEURS JOURNALIERS
# ═══════════════════════════════════════════════════════════════
 
trades_jour   = 0
date_courante = None
 
def reset_compteurs():
    global trades_jour, date_courante
    today = datetime.now(TZ_PARIS).date()
    if date_courante != today:
        date_courante = today
        trades_jour   = 0
        print(f"\n🔄 Nouveau jour — compteurs réinitialisés")
 
# ═══════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT DONNÉES ALPACA
# ═══════════════════════════════════════════════════════════════
 
def get_bars(ticker, timeframe, nb=150):
    try:
        now_ny = datetime.now(TZ_NY)
        debut  = now_ny - timedelta(days=10)
        req    = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=timeframe,
            start=debut,
            end=now_ny,
            limit=nb
        )
        bars = data_client.get_stock_bars(req)
        df   = bars.df
        if df is None or len(df) < 20:
            return None
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume"
        })
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception as e:
        print(f"  ⚠️ Data {ticker} : {e}")
        return None
 
# ═══════════════════════════════════════════════════════════════
#  UT BOT — Trailing Stop ATR
#  Traduit fidèlement du Pine Script original
# ═══════════════════════════════════════════════════════════════
 
def calcul_ut_bot(df, key_value=1.0, atr_period=10):
    """
    UT Bot : calcule le trailing stop ATR et les signaux BUY/SELL
    Retourne df avec colonnes : ATR, nLoss, xATRTS, utPos, utBuy, utSell
    """
    # Heikin Ashi close
    df["HA_Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
 
    # ATR
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"]  - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR"]   = tr.rolling(atr_period).mean()
    df["nLoss"] = key_value * df["ATR"]
 
    # Trailing Stop ATR (xATRTS)
    src    = df["HA_Close"].values
    nLoss  = df["nLoss"].values
    xATRTS = np.zeros(len(df))
 
    for i in range(1, len(df)):
        prev = xATRTS[i-1]
        s    = src[i]
        sp   = src[i-1]
        nl   = nLoss[i]
        if s > prev and sp > prev:
            xATRTS[i] = max(prev, s - nl)
        elif s < prev and sp < prev:
            xATRTS[i] = min(prev, s + nl)
        elif s > prev:
            xATRTS[i] = s - nl
        else:
            xATRTS[i] = s + nl
 
    df["xATRTS"] = xATRTS
 
    # Direction UT Bot
    utPos  = np.zeros(len(df))
    utBuy  = np.zeros(len(df), dtype=bool)
    utSell = np.zeros(len(df), dtype=bool)
 
    for i in range(1, len(df)):
        s  = src[i]
        sp = src[i-1]
        t  = xATRTS[i]
        tp = xATRTS[i-1]
 
        # Crossover haussier
        if sp < tp and s > t:
            utPos[i] = 1
            utBuy[i] = True
        # Crossover baissier
        elif sp > tp and s < t:
            utPos[i] = -1
            utSell[i] = True
        else:
            utPos[i] = utPos[i-1]
 
    df["utPos"]  = utPos
    df["utBuy"]  = utBuy
    df["utSell"] = utSell
    return df
 
# ═══════════════════════════════════════════════════════════════
#  EMA200 — FILTRE TENDANCE
# ═══════════════════════════════════════════════════════════════
 
def calcul_ema200(df, periode=200):
    df["EMA200"] = df["Close"].ewm(span=periode, adjust=False).mean()
    return df
 
# ═══════════════════════════════════════════════════════════════
#  CONFIRMATION EN 2 TEMPS (comme UT Bot original)
# ═══════════════════════════════════════════════════════════════
 
def verifier_confirmation(df):
    """
    Signal UT Bot confirmé en 2 bougies :
    1. Bougie de signal (utBuy/utSell)
    2. Bougie suivante qui confirme en cassant le high/low de la bougie signal
    """
    if len(df) < 3:
        return None, 0
 
    d   = df.iloc[-1]
    d_1 = df.iloc[-2]
    d_2 = df.iloc[-3]
 
    prix    = float(d["Close"])
    ema200  = float(d["EMA200"])
    atr     = float(d["ATR"])
 
    # ── LONG : signal sur d_2, confirmation sur d_1 ou d ──────
    # Signal UT Buy sur avant-dernière bougie
    if d_2["utBuy"]:
        sig_high = float(d_2["High"])
        # Confirmation : close au-dessus du high de la bougie signal
        if float(d_1["Close"]) > sig_high or float(d["Close"]) > sig_high:
            # Filtre EMA200 : prix doit être au-dessus
            if prix > ema200:
                return "LONG", atr
 
    # ── SHORT : signal sur d_2, confirmation sur d_1 ou d ─────
    if d_2["utSell"]:
        sig_low = float(d_2["Low"])
        # Confirmation : close en-dessous du low de la bougie signal
        if float(d_1["Close"]) < sig_low or float(d["Close"]) < sig_low:
            # Filtre EMA200 : prix doit être en-dessous
            if prix < ema200:
                return "SHORT", atr
 
    # Signal frais sur la dernière bougie (attente confirmation)
    if d_1["utBuy"]:
        sig_high = float(d_1["High"])
        if prix > sig_high and prix > ema200:
            return "LONG", atr
 
    if d_1["utSell"]:
        sig_low = float(d_1["Low"])
        if prix < sig_low and prix < ema200:
            return "SHORT", atr
 
    return None, 0
 
# ═══════════════════════════════════════════════════════════════
#  CALCUL SL, TP PARTIEL, TP FINAL, BREAKEVEN
# ═══════════════════════════════════════════════════════════════
 
def calcul_niveaux(direction, prix, atr):
    """
    SL basé sur ATR
    P1 (partielle) = 1.5R
    P2 (final)     = 3R
    BE             = prix d'entrée + petit buffer
    """
    sl_distance = atr * UT_KEY_VALUE  # 1R = distance ATR
 
    if direction == "LONG":
        sl   = round(prix - sl_distance, 2)
        p1   = round(prix + sl_distance * PARTIAL_MULT, 2)  # +1.5R
        p2   = round(prix + sl_distance * TP_MULT, 2)       # +3R
        be   = round(prix + sl_distance * BE_PCT, 2)        # breakeven
    else:
        sl   = round(prix + sl_distance, 2)
        p1   = round(prix - sl_distance * PARTIAL_MULT, 2)  # -1.5R
        p2   = round(prix - sl_distance * TP_MULT, 2)       # -3R
        be   = round(prix - sl_distance * BE_PCT, 2)        # breakeven
 
    return sl, p1, p2, be, sl_distance
 
def calcul_quantite(prix, sl_distance):
    """Risque 1% du capital, max 5 lots"""
    risque_dollar = CAPITAL_TOTAL * RISQUE_PCT
    quantite      = int(risque_dollar / sl_distance)
    return max(1, min(quantite, MAX_LOTS))
 
# ═══════════════════════════════════════════════════════════════
#  ORDRES ALPACA
# ═══════════════════════════════════════════════════════════════
 
def passer_ordre(ticker, direction, prix, sl, tp_final, quantite):
    """Ordre avec SL + TP final via bracket"""
    try:
        if direction == "LONG":
            ordre = MarketOrderRequest(
                symbol=ticker,
                qty=quantite,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=tp_final),
                stop_loss=StopLossRequest(stop_price=sl)
            )
        else:
            ordre = MarketOrderRequest(
                symbol=ticker,
                qty=quantite,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=tp_final),
                stop_loss=StopLossRequest(stop_price=sl)
            )
        result = client.submit_order(ordre)
        return result.id
    except Exception as e:
        print(f"  ❌ Ordre {ticker} : {e}")
        return None
 
def fermer_position(ticker, quantite, direction, raison):
    """Ferme une position"""
    try:
        side = OrderSide.SELL if direction == "LONG" else OrderSide.BUY
        client.submit_order(MarketOrderRequest(
            symbol=ticker,
            qty=quantite,
            side=side,
            time_in_force=TimeInForce.DAY
        ))
        print(f"  ✅ Fermé {quantite}x{ticker} | {raison}")
    except Exception as e:
        print(f"  ❌ Fermeture {ticker} : {e}")
 
def fermeture_forcee():
    """Fermeture forcée à 21h"""
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
#  GESTION POSITIONS — PARTIELLE + BREAKEVEN
# ═══════════════════════════════════════════════════════════════
 
positions_robot = {}
# Structure : ticker → {
#   direction, prix_entree, sl, p1, p2, be,
#   quantite_totale, quantite_restante,
#   p1_atteint, be_actif, order_id
# }
 
def sync_positions():
    """Synchronise avec Alpaca"""
    try:
        reelles = {p.symbol for p in client.get_all_positions()}
        for t in list(positions_robot.keys()):
            if t not in reelles:
                pos = positions_robot[t]
                gain = "TP ✅" if pos.get("p1_atteint") else "SL/fermé"
                print(f"  ✅ {t} fermée Alpaca ({gain})")
                del positions_robot[t]
    except Exception as e:
        print(f"  ⚠️ Sync : {e}")
 
def gerer_positions():
    """Gère les positions ouvertes : partielle + breakeven"""
    if not positions_robot:
        return
 
    print("\n📂 Positions ouvertes :")
    try:
        pa = {p.symbol: p for p in client.get_all_positions()}
    except:
        return
 
    for ticker, pos in list(positions_robot.items()):
        if ticker not in pa:
            del positions_robot[ticker]
            continue
 
        prix_actuel = float(pa[ticker].current_price)
        entree      = pos["prix_entree"]
        direction   = pos["direction"]
        quantite    = int(float(pa[ticker].qty))
        gain_pct    = ((prix_actuel - entree) / entree) * 100
        if direction == "SHORT":
            gain_pct = -gain_pct
 
        print(f"  {'🟢' if direction=='LONG' else '🔴'} {ticker} {direction}")
        print(f"     {entree:.2f}$→{prix_actuel:.2f}$ | {gain_pct:+.2f}%")
        print(f"     SL:{pos['sl']:.2f}$ | P1:{pos['p1']:.2f}$ | P2:{pos['p2']:.2f}$")
 
        # ── SORTIE PARTIELLE P1 (1.5R) ────────────────────────
        if not pos["p1_atteint"]:
            p1_touche = (
                (direction == "LONG"  and prix_actuel >= pos["p1"]) or
                (direction == "SHORT" and prix_actuel <= pos["p1"])
            )
            if p1_touche and quantite >= 2:
                moitie = quantite // 2
                fermer_position(ticker, moitie, direction, f"✨ P1 1.5R atteint!")
                pos["p1_atteint"]      = True
                pos["quantite_restante"] = quantite - moitie
                # Déplacer SL au breakeven
                pos["sl"] = pos["be"]
                pos["be_actif"] = True
                print(f"     🎯 P1 atteint! SL déplacé au BE: {pos['be']:.2f}$")
 
        # ── BREAKEVEN (si P1 atteint) ──────────────────────────
        if pos.get("be_actif") and not pos.get("be_touche"):
            be_touche = (
                (direction == "LONG"  and prix_actuel <= pos["be"]) or
                (direction == "SHORT" and prix_actuel >= pos["be"])
            )
            if be_touche:
                q_restante = pos.get("quantite_restante", quantite)
                fermer_position(ticker, q_restante, direction, "📍 Breakeven")
                pos["be_touche"] = True
                del positions_robot[ticker]
 
        # ── TP FINAL P2 (3R) ───────────────────────────────────
        if pos.get("p1_atteint"):
            p2_touche = (
                (direction == "LONG"  and prix_actuel >= pos["p2"]) or
                (direction == "SHORT" and prix_actuel <= pos["p2"])
            )
            if p2_touche:
                q_restante = pos.get("quantite_restante", quantite)
                fermer_position(ticker, q_restante, direction, "🏆 TP Final 3R!")
                del positions_robot[ticker]
 
# ═══════════════════════════════════════════════════════════════
#  HORAIRES
# ═══════════════════════════════════════════════════════════════
 
def get_heure():
    now = datetime.now(TZ_PARIS)
    return now.hour + now.minute / 60, now.strftime("%H:%M")
 
# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════
 
def lancer_robot():
    print("\n🤖 ROBOT HALAL V9 — UT BOT + EMA200 + PARTIELLE 1.5R")
    print("=" * 65)
    print(f"💰 Capital          : {CAPITAL_TOTAL:.2f}$")
    print(f"🥇 Actifs           : GLD | SGOL | USO")
    print(f"📡 Signal           : UT Bot ATR Trailing Stop")
    print(f"📈 Filtre           : EMA200 (LONG au-dessus, SHORT en-dessous)")
    print(f"✂️  Sortie partielle : 1.5R → 50% position fermée")
    print(f"📍 Breakeven        : SL déplacé au prix d'entrée après P1")
    print(f"🏆 TP Final         : 3R → 50% restant")
    print(f"📦 Max lots/trade   : {MAX_LOTS}")
    print(f"📊 Max trades/jour  : {MAX_TRADES_JOUR}")
    print(f"🛡️  Perte max/jour   : {PERTE_MAX_JOUR*100:.0f}%")
    print(f"⏰ Trading          : 9h00 → 17h00 Paris")
    print(f"🕘 Fermeture forcée : 21h00")
    print(f"⏱️  Scan             : toutes les {PAUSE_MIN} minutes")
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
            print(f"🌙 {heure_str} — Repos | Reprise 9h00")
            time.sleep(PAUSE_MIN * 60)
            continue
 
        if h >= HEURE_DEBUT:
            fermeture_faite = False
 
        if h < HEURE_DEBUT:
            print(f"⏳ {heure_str} — Marché fermé")
            time.sleep(PAUSE_MIN * 60)
            continue
 
        cycle += 1
        reset_compteurs()
 
        print(f"\n{'='*65}")
        print(f"🔄 Cycle {cycle} | 🕐 {heure_str} | Trades: {trades_jour}/{MAX_TRADES_JOUR}")
 
        # ── INFO COMPTE ────────────────────────────────────────
        try:
            acc    = client.get_account()
            cash   = float(acc.cash)
            portef = float(acc.portfolio_value)
            pnl    = portef - CAPITAL_TOTAL
            print(f"💰 Cash:{cash:.2f}$ | Portef:{portef:.2f}$ | PnL:{pnl:+.2f}$")
 
            # Stop si perte max
            if pnl < -(CAPITAL_TOTAL * PERTE_MAX_JOUR):
                print(f"🛑 PERTE MAX ATTEINTE — Pause jusqu'à 21h")
                fermeture_forcee()
                positions_robot.clear()
                time.sleep(PAUSE_MIN * 60)
                continue
        except Exception as e:
            print(f"  ⚠️ Compte : {e}")
 
        # ── SYNC + GÉRER POSITIONS ─────────────────────────────
        sync_positions()
        gerer_positions()
 
        # ── CHERCHER NOUVEAUX SIGNAUX (9h → 17h) ──────────────
        if h < HEURE_FIN_TRADE and trades_jour < MAX_TRADES_JOUR:
            print(f"\n🔍 Scan UT Bot + EMA200...")
 
            try:
                pos_reelles = {p.symbol for p in client.get_all_positions()}
            except:
                pos_reelles = set()
 
            for ticker, nom in actifs.items():
                if ticker in pos_reelles:
                    continue
                if trades_jour >= MAX_TRADES_JOUR:
                    break
 
                try:
                    # Télécharger données 5min
                    df = get_bars(ticker, TF_5MIN, nb=250)
                    if df is None or len(df) < 220:
                        print(f"  ⚠️ {ticker} données insuffisantes")
                        continue
 
                    # Calculer UT Bot
                    df = calcul_ut_bot(df, UT_KEY_VALUE, UT_ATR_PERIOD)
 
                    # Calculer EMA200
                    df = calcul_ema200(df, EMA_PERIODE)
 
                    # Vérifier signal
                    direction, atr = verifier_confirmation(df)
 
                    if direction is None:
                        print(f"  ⏸️  {ticker} — pas de signal")
                        continue
 
                    prix = float(df.iloc[-1]["Close"])
                    ema  = float(df.iloc[-1]["EMA200"])
                    ut   = int(df.iloc[-1]["utPos"])
 
                    print(f"\n  🚨 SIGNAL {direction} sur {ticker} ({nom})")
                    print(f"     Prix:{prix:.2f}$ | EMA200:{ema:.2f}$ | UT:{'+1' if ut==1 else '-1'}")
 
                    # Calculer niveaux
                    sl, p1, p2, be, sl_dist = calcul_niveaux(direction, prix, atr)
                    quantite = calcul_quantite(prix, sl_dist)
 
                    print(f"     SL:{sl:.2f}$ | P1(1.5R):{p1:.2f}$ | P2(3R):{p2:.2f}$")
                    print(f"     Quantité:{quantite} | Risque:{quantite*sl_dist:.2f}$")
 
                    # Passer ordre
                    order_id = passer_ordre(ticker, direction, prix, sl, p2, quantite)
 
                    if order_id:
                        trades_jour += 1
                        positions_robot[ticker] = {
                            "direction":        direction,
                            "prix_entree":      prix,
                            "sl":               sl,
                            "p1":               p1,
                            "p2":               p2,
                            "be":               be,
                            "quantite_totale":  quantite,
                            "quantite_restante":quantite,
                            "p1_atteint":       False,
                            "be_actif":         False,
                            "be_touche":        False,
                            "order_id":         order_id,
                        }
                        emoji = "🟢" if direction == "LONG" else "🔴"
                        print(f"     {emoji} Ordre passé ! Trade {trades_jour}/{MAX_TRADES_JOUR}")
 
                    time.sleep(1)
 
                except Exception as e:
                    print(f"  ⚠️ {ticker} erreur : {e}")
                    time.sleep(1)
 
        elif h >= HEURE_FIN_TRADE:
            print(f"\n⏰ Plus de nouveaux trades après 17h — gestion positions uniquement")
        else:
            print(f"\n📦 Max trades atteint ({MAX_TRADES_JOUR})")
 
        # ── RÉSUMÉ ─────────────────────────────────────────────
        print(f"\n📊 Positions: {len(positions_robot)} | Trades: {trades_jour}/{MAX_TRADES_JOUR}")
        for t, p in positions_robot.items():
            e = "🟢" if p["direction"] == "LONG" else "🔴"
            p1s = "✅" if p["p1_atteint"] else "⏳"
            print(f"   {e} {t} | {p['direction']} | {p['prix_entree']:.2f}$ | P1:{p1s}")
 
        print(f"\n⏳ Prochain scan dans {PAUSE_MIN}min...")
        time.sleep(PAUSE_MIN * 60)
 
# LANCEMENT
lancer_robot()
