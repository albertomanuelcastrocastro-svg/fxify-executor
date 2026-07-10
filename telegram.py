"""Telegram — Envía notificaciones. Silencioso si no hay credenciales."""

import requests
import logging

log = logging.getLogger("telegram")


def enviar_telegram(mensaje: str, token: str, chat_id: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "HTML"},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False
