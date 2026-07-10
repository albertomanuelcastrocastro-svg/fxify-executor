"""
FXIFY EXECUTOR — Un activo por servicio.
Mismo código, distintas variables de entorno por cada deploy en Railway.

Flujo: TradingView → webhook → market order + SL/TP adjuntos → fin.
Sin parciales, sin trailing, sin gestión posterior. Todo o nada.
"""

import os
import json
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dxtrade import DXTrade
from telegram import enviar_telegram

# ─── Config desde variables de entorno ───
SYMBOL        = os.getenv("SYMBOL", "XRPUSDT")          # Mi activo (FXIFY usa sufijo USDT)
TP_PCT        = float(os.getenv("TP_PCT", "1.0"))        # Take profit %
SL_PCT        = float(os.getenv("SL_PCT", "1.0"))        # Stop loss %
POSITION_SIZE = float(os.getenv("POSITION_SIZE", "100")) # Cantidad del activo
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "palmero2026")
DXTRADE_URL   = os.getenv("DXTRADE_URL", "https://dxtrade.alchemymarkets.eu")
DXTRADE_USER  = os.getenv("DXTRADE_USER", "")
DXTRADE_PASS  = os.getenv("DXTRADE_PASS", "")
DXTRADE_ACCOUNT = os.getenv("DXTRADE_ACCOUNT", "")
TG_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT       = os.getenv("TELEGRAM_CHAT_ID", "")
PORT          = int(os.getenv("PORT", "8080"))

# ─── Mapeo de símbolos TradingView → DXtrade (FXIFY) ───
# FXIFY (via AlchemyMarkets) usa el mismo formato USDT que Binance
SYMBOL_MAP = {
    "XRPUSDT": "XRPUSDT", "XRPUSD": "XRPUSDT",
    "SOLUSDT": "SOLUSDT", "SOLUSD": "SOLUSDT",
    "ETHUSDT": "ETHUSDT", "ETHUSD": "ETHUSDT",
    "XLMUSDT": "XLMUSDT", "XLMUSD": "XLMUSDT",
}

# ─── Estado ───
trade_hoy = None  # Fecha del último trade (str "YYYY-MM-DD")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("executor")

dx = None

def conectar():
    """Conecta a DXtrade si hay credenciales."""
    global dx
    if not DXTRADE_URL or not DXTRADE_USER:
        log.info(f"🔸 Modo DRY-RUN para {SYMBOL} (sin credenciales DXtrade)")
        return
    dx = DXTrade(DXTRADE_URL, DXTRADE_USER, DXTRADE_PASS)
    if dx.login():
        log.info(f"✅ Conectado a DXtrade — Activo: {SYMBOL}")
    else:
        log.error("❌ Fallo login DXtrade")
        dx = None


def ya_opero_hoy() -> bool:
    """1 operación por día. Devuelve True si ya operó hoy."""
    global trade_hoy
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return trade_hoy == hoy


def marcar_operado():
    """Marca que ya operó hoy."""
    global trade_hoy
    trade_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "JSON inválido"}), 400

    # Verificar secreto
    if data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    action = data.get("action", "").upper()
    symbol_tv = data.get("symbol", "")
    price = float(data.get("price", 0))
    comment = data.get("comment", "")

    # Mapear símbolo
    symbol_dx = SYMBOL_MAP.get(symbol_tv, symbol_tv)

    # ¿Es para mí?
    if symbol_dx != SYMBOL:
        return jsonify({"status": "ignored", "reason": f"Soy {SYMBOL}, esto es {symbol_dx}"}), 200

    log.info(f"📩 Señal: {action} {SYMBOL} @ {price} | {comment}")

    # ¿Ya operé hoy?
    if ya_opero_hoy():
        msg = f"⏸️ {SYMBOL}: Ya operé hoy. Señal ignorada: {action} @ {price}"
        log.info(msg)
        enviar_telegram(msg, TG_TOKEN, TG_CHAT)
        return jsonify({"status": "skipped", "reason": "already_traded_today"}), 200

    # Calcular SL y TP
    if action == "BUY":
        sl_price = round(price * (1 - SL_PCT / 100), 6)
        tp_price = round(price * (1 + TP_PCT / 100), 6)
    elif action == "SELL":
        sl_price = round(price * (1 + SL_PCT / 100), 6)
        tp_price = round(price * (1 - TP_PCT / 100), 6)
    else:
        return jsonify({"error": f"Acción desconocida: {action}"}), 400

    # ─── DRY-RUN ───
    if dx is None:
        msg = (f"🔸 DRY-RUN {SYMBOL}\n"
               f"{action} × {POSITION_SIZE} @ {price}\n"
               f"SL: {sl_price} ({SL_PCT}%) | TP: {tp_price} ({TP_PCT}%)\n"
               f"{comment}")
        log.info(msg)
        enviar_telegram(msg, TG_TOKEN, TG_CHAT)
        marcar_operado()
        return jsonify({"status": "dry-run", "sl": sl_price, "tp": tp_price}), 200

    # ─── EJECUCIÓN REAL ───
    try:
        # Asegurar sesión activa
        if not dx.is_alive():
            dx.login()

        result = dx.abrir_con_sl_tp(
            symbol=SYMBOL,
            side=action,
            quantity=POSITION_SIZE,
            sl_price=sl_price,
            tp_price=tp_price
        )

        marcar_operado()

        msg = (f"✅ {SYMBOL} EJECUTADO\n"
               f"{action} × {POSITION_SIZE} @ {price}\n"
               f"SL: {sl_price} | TP: {tp_price}\n"
               f"{comment}")
        log.info(msg)
        enviar_telegram(msg, TG_TOKEN, TG_CHAT)
        return jsonify({"status": "executed", "result": result}), 200

    except Exception as e:
        msg = f"❌ {SYMBOL} ERROR: {action} @ {price} — {str(e)}"
        log.error(msg)
        enviar_telegram(msg, TG_TOKEN, TG_CHAT)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════
# HEALTH + INFO (UptimeRobot pinga aquí)
# ═══════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "symbol": SYMBOL,
        "config": {"tp": TP_PCT, "sl": SL_PCT, "size": POSITION_SIZE},
        "traded_today": ya_opero_hoy(),
        "connected": dx is not None and dx.is_alive() if dx else False,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": f"FXIFY Executor — {SYMBOL}",
        "endpoints": ["/webhook", "/health"]
    })


# ═══════════════════════════════════════════════════════
# ARRANQUE — conectar al importar (funciona con gunicorn)
# ═══════════════════════════════════════════════════════

conectar()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
