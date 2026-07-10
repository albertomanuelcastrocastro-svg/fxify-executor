"""
DXTrade — Cliente mínimo para FXIFY (via AlchemyMarkets).
Login, orden con SL/TP adjuntos, y refresh de sesión.
"""

import uuid
import logging
import threading
import requests

log = logging.getLogger("dxtrade")


class DXTrade:

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.csrf = None
        self.account_id = None
        self._authenticated = False
        self._stop = threading.Event()

    # ─── AUTH ────────────────────────────────────────────

    def login(self) -> bool:
        try:
            resp = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password, "vendor": "default"},
                timeout=15
            )
            if resp.status_code != 200:
                log.error(f"Login failed: {resp.status_code}")
                return False

            self.csrf = (
                resp.headers.get("X-CSRF-Token") or
                self.session.cookies.get("XSRF-TOKEN") or
                ""
            )
            self._authenticated = True
            self._find_account()
            self._start_keepalive()
            return True

        except Exception as e:
            log.error(f"Login error: {e}")
            return False

    def is_alive(self) -> bool:
        """¿La sesión sigue activa?"""
        if not self._authenticated:
            return False
        try:
            resp = self.session.get(
                f"{self.base_url}/api/accounts",
                headers=self._h(),
                timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _find_account(self):
        try:
            resp = self.session.get(f"{self.base_url}/api/accounts", headers=self._h(), timeout=10)
            data = resp.json()
            if isinstance(data, list) and data:
                self.account_id = data[0].get("accountId", data[0].get("id", ""))
            elif isinstance(data, dict):
                self.account_id = data.get("accountId", data.get("id", ""))
            log.info(f"Account: {self.account_id}")
        except Exception as e:
            log.warning(f"Account discovery failed: {e}")

    def _start_keepalive(self):
        """Ping cada 20 min para que la sesión no expire (30 min idle)."""
        def loop():
            while not self._stop.is_set():
                self._stop.wait(20 * 60)
                if self._stop.is_set():
                    break
                try:
                    self.session.get(f"{self.base_url}/api/accounts", headers=self._h(), timeout=10)
                    log.debug("🔄 Keepalive OK")
                except Exception:
                    log.warning("⚠️ Keepalive failed, re-login")
                    self.login()

        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def _h(self) -> dict:
        h = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return h

    # ─── ORDEN CON SL/TP ────────────────────────────────

    def abrir_con_sl_tp(self, symbol: str, side: str, quantity: float,
                         sl_price: float, tp_price: float) -> dict:
        """
        Abre posición market y coloca SL + TP como órdenes de cierre.
        Todo en una secuencia: open → SL → TP.
        """
        # 1. Orden market de apertura
        order = self._order(symbol, side, quantity, "MARKET", effect="OPENING")
        log.info(f"📤 Abierto: {side} {symbol} × {quantity}")

        # 2. SL (STOP order de cierre)
        close_side = "SELL" if side == "BUY" else "BUY"
        try:
            self._order(symbol, close_side, quantity, "STOP",
                       stop_price=sl_price, effect="CLOSING")
            log.info(f"🛡️ SL: {sl_price}")
        except Exception as e:
            log.error(f"⚠️ SL failed: {e}")

        # 3. TP (LIMIT order de cierre)
        try:
            self._order(symbol, close_side, quantity, "LIMIT",
                       limit_price=tp_price, effect="CLOSING")
            log.info(f"🎯 TP: {tp_price}")
        except Exception as e:
            log.error(f"⚠️ TP failed: {e}")

        return order

    def _order(self, symbol: str, side: str, quantity: float, order_type: str,
               limit_price: float = None, stop_price: float = None,
               effect: str = "OPENING") -> dict:
        """Envía una orden a DXtrade."""
        payload = {
            "directExchange": False,
            "legs": [{
                "instrumentId": symbol,
                "positionEffect": effect,
                "ratioQuantity": 1,
                "symbol": symbol
            }],
            "orderSide": side,
            "orderType": order_type,
            "quantity": quantity,
            "requestId": f"palmero-{uuid.uuid4().hex[:8]}",
            "timeInForce": "GTC"
        }

        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price

        resp = self.session.post(
            f"{self.base_url}/api/orders/single",
            headers=self._h(),
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    # ─── CONSULTAS ──────────────────────────────────────

    def cuenta(self) -> dict:
        resp = self.session.get(f"{self.base_url}/api/accounts", headers=self._h(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def posiciones(self) -> list:
        try:
            resp = self.session.get(f"{self.base_url}/api/positions", headers=self._h(), timeout=10)
            data = resp.json()
            return data if isinstance(data, list) else data.get("positions", [])
        except Exception:
            return []
