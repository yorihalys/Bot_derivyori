import websocket
import json
import threading
import time
import requests
from datetime import datetime, timedelta
import pytz
import numpy as np
from flask import Flask
import logging

# ====== CONFIGURACIÓN =======
DERIV_TOKEN = "UbQVaW5F4f7DWyM"
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"
CUENTA_ID = "CR8793618"

META_DIARIA = 20.00
VOLUMEN_BASE = 0.20
DURACION_OPERACION = 3  # minutos

ACTIVOS = [
    "boom1000", "boom500", "boom300", "boom100",
    "crash1000", "crash500", "crash300", "crash100",
    "volatility100", "volatility75", "volatility50",
    "volatility25", "volatility10"
]

HORARIO_DERIV = pytz.UTC
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

capital_actual = 22.00
ganancia_diaria = 0.0
operaciones_dia = []
bot_activo = True
datos_candles = {activo: [] for activo in ACTIVOS}
ws = None

# ===== FUNCIONES UTILIDAD =====

def enviar_mensaje_telegram(texto: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHANNEL, "text": texto, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data)
        if r.status_code != 200:
            logging.error(f"Error Telegram: {r.text}")
    except Exception as e:
        logging.error(f"Exception Telegram: {e}")

def obtener_hora_deriv():
    return datetime.now(HORARIO_DERIV)

def formatear_hora(dt):
    return dt.strftime("%H:%M")

def calcular_ema(precios, periodo):
    precios = np.array(precios)
    k = 2 / (periodo + 1)
    ema = precios[0]
    for p in precios[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calcular_rsi(precios, periodo=14):
    precios = np.array(precios)
    deltas = np.diff(precios)
    ganancias = np.where(deltas > 0, deltas, 0)
    perdidas = np.where(deltas < 0, -deltas, 0)
    avg_ganancia = np.mean(ganancias[-periodo:]) if len(ganancias) >= periodo else 0
    avg_perdida = np.mean(perdidas[-periodo:]) if len(perdidas) >= periodo else 0
    if avg_perdida == 0:
        return 100.0
    rs = avg_ganancia / avg_perdida
    return 100 - (100 / (1 + rs))

def ajustar_volumen(volumen):
    opciones = [0.20, 0.15, 0.10, 0.05]
    for v in opciones:
        if volumen >= v:
            return v
    return 0.05

def esta_dentro_horario_operacion():
    ahora_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
    hora_utc = ahora_utc.hour
    inicio_utc = 11  # 7:00 AM VZ = 11:00 UTC
    fin_utc = 1      # 9:00 PM VZ = 1:00 AM UTC siguiente

    if inicio_utc > fin_utc:
        return hora_utc >= inicio_utc or hora_utc < fin_utc
    else:
        return inicio_utc <= hora_utc < fin_utc

# ===== OPERACIONES =====

def abrir_operacion(simbolo, direccion, volumen, duracion):
    global capital_actual
    if not esta_dentro_horario_operacion():
        return

    volumen = ajustar_volumen(volumen)
    if volumen > capital_actual:
        volumen = capital_actual

    tipo = "CALL" if direccion == "COMPRA" else "PUT"
    contract_id = f"contrato_{int(time.time()*1000)}"

    msg_compra = {
        "buy": 1,
        "parameters": {
            "amount": volumen,
            "basis": "stake",
            "contract_type": tipo.lower(),
            "currency": "USD",
            "duration": duracion,
            "duration_unit": "m",
            "symbol": simbolo,
            "barrier": None,
            "contract_id": contract_id,
            "id": contract_id
        },
        "req_id": int(time.time())
    }

    ws.send(json.dumps(msg_compra))
    logging.info(f"Abrir operación: {simbolo} {direccion} volumen {volumen}")
    capital_actual -= volumen

    hora_op = obtener_hora_deriv()
    texto = (
        f"✅ OPERACIÓN ABIERTA\n"
        f"🧭 Activo: {simbolo.upper()}\n"
        f"🕐 Hora: {formatear_hora(hora_op)} (Hora Deriv)\n"
        f"📉 Dirección: {direccion}\n"
        f"🔢 Volumen: {volumen:.2f}\n"
        f"🎯 Take Profit: Automático\n"
        f"🛡️ Stop Loss: Automático"
    )
    enviar_mensaje_telegram(texto)

    operaciones_dia.append({
        "contract_id": contract_id,
        "simbolo": simbolo,
        "direccion": direccion,
        "volumen": volumen,
        "hora": hora_op,
        "estado": "ABIERTA",
        "precio_entrada": None,
        "precio_salida": None,
        "ganancia": None,
    })

def cerrar_operacion_por_contrato(contract_id, resultado, precio_salida):
    global capital_actual, ganancia_diaria
    operacion = next((op for op in operaciones_dia if op["contract_id"] == contract_id), None)
    if not operacion or operacion["estado"] == "CERRADA":
        return

    operacion["estado"] = "CERRADA"
    operacion["precio_salida"] = precio_salida
    ganancia = operacion["volumen"] * 0.8 if resultado == "ganancia" else -operacion["volumen"]
    operacion["ganancia"] = ganancia

    capital_actual += operacion["volumen"] + ganancia if ganancia > 0 else 0
    ganancia_diaria += ganancia if ganancia > 0 else 0

    texto = (
        f"📤 OPERACIÓN CERRADA\n"
        f"🧭 Activo: {operacion['simbolo'].upper()}\n"
        f"📈 Resultado: {'GANANCIA ✅' if ganancia > 0 else 'PÉRDIDA ❌'}\n"
        f"💸 Salida: {precio_salida}\n"
        f"📊 Ganancia: ${ganancia:.2f}"
    )
    enviar_mensaje_telegram(texto)
    enviar_mensaje_telegram(f"📈 Progreso diario: ${ganancia_diaria:.2f} / ${META_DIARIA:.2f}")

    if ganancia_diaria >= META_DIARIA:
        enviar_mensaje_telegram(f"🎯 META DIARIA ALCANZADA\n✅ Total: ${ganancia_diaria:.2f}\n🔒 Bot detenido.")
        detener_bot()

def detener_bot():
    global bot_activo
    bot_activo = False

def reiniciar_dia():
    global ganancia_diaria, operaciones_dia, bot_activo
    ganancia_diaria = 0.0
    operaciones_dia.clear()
    bot_activo = True
    enviar_mensaje_telegram("♻️ Reinicio diario - Nuevo ciclo iniciado.")
    logging.info("Reinicio diario completado.")

# ===== WEBSOCKET =====

def on_message(wsapp, message):
    data = json.loads(message)
    if "buy" in data:
        buy_data = data["buy"]
        if buy_data.get("is_sold", False):
            contract_id = buy_data.get("contract_id")
            resultado = "ganancia" if buy_data.get("profit", 0) > 0 else "perdida"
            precio_salida = buy_data.get("sell_price", 0)
            cerrar_operacion_por_contrato(contract_id, resultado, precio_salida)
    elif "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]
        if simbolo in datos_candles:
            datos_candles[simbolo].append(precio)
            if len(datos_candles[simbolo]) > 50:
                datos_candles[simbolo].pop(0)
    elif "error" in data:
        logging.error(f"Error API Deriv: {data['error']['message']}")

def on_open(wsapp):
    logging.info("WebSocket conectado")
    for activo in ACTIVOS:
        wsapp.send(json.dumps({"ticks": activo, "req_id": int(time.time())}))
    iniciar_bot()

def on_error(wsapp, error):
    logging.error(f"WebSocket error: {error}")

def on_close(wsapp, code, msg):
    logging.warning(f"WebSocket cerrado: {code} - {msg}")

def conectar_websocket():
    global ws
    url = "wss://ws.binaryws.com/websockets/v3?app_id=1089&l=EN"
    headers = {"Authorization": f"Bearer {DERIV_TOKEN}"}
    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        header=headers
    )
    ws.run_forever()

# ===== ANÁLISIS Y CICLOS =====

def analizar_y_operar():
    global bot_activo
    if not bot_activo or not esta_dentro_horario_operacion():
        return

    for activo in ACTIVOS:
        precios = datos_candles.get(activo, [])
        if len(precios) < 20:
            continue
        ema10 = calcular_ema(precios[-20:], 10)
        ema20 = calcular_ema(precios[-20:], 20)
        rsi14 = calcular_rsi(precios[-20:], 14)
        direccion = None
        if ema10 > ema20 and rsi14 < 70:
            direccion = "COMPRA"
        elif ema10 < ema20 and rsi14 > 30:
            direccion = "VENTA"
        if direccion:
            abrir_operacion(activo, direccion, VOLUMEN_BASE, DURACION_OPERACION)
            time.sleep(1)

def ciclo_analisis_continuo():
    while True:
        analizar_y_operar()
        time.sleep(300)

def ciclo_reinicio_diario():
    while True:
        ahora = obtener_hora_deriv()
        if ahora.hour == 0 and ahora.minute == 0:
            reiniciar_dia()
            time.sleep(61)
        time.sleep(20)

def iniciar_bot():
    enviar_mensaje_telegram(
        f"✅ BOT ENCENDIDO\n"
        f"🆔 Cuenta conectada: {CUENTA_ID} (Real)\n"
        f"💰 Capital disponible: ${capital_actual:.2f}\n"
        f"📡 Estado: Conectado y operativo"
    )

@app.route('/')
def home():
    return "Bot de trading Deriv activo."

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()
    threading.Thread(target=ciclo_analisis_continuo, daemon=True).start()
    threading.Thread(target=ciclo_reinicio_diario, daemon=True).start()
    threading.Thread(target=conectar_websocket, daemon=True).start()
    while True:
        time.sleep(1)
