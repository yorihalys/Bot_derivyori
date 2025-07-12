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
DERIV_TOKEN = "UbQVaW5F4f7DWyM"  # Tu token real
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"
CUENTA_ID = "CR8793618"

META_DIARIA = 20.00
VOLUMEN_BASE = 0.20
DURACION_OPERACION = 3  # en minutos

ACTIVOS = [
    "boom1000", "boom500", "boom300", "boom100",
    "crash1000", "crash500", "crash300", "crash100",
    "volatility100", "volatility75", "volatility50", "volatility25", "volatility10"
]

# Zona horaria oficial de Deriv (UTC)
HORARIO_DERIV = pytz.UTC

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Estado global
capital_actual = 22.00
ganancia_diaria = 0.0
operaciones_dia = []  # Lista con dicts: operación abierta/cerrada
bot_activo = True

# Datos de ticks y candles para cálculos EMA y RSI
datos_candles = {activo: [] for activo in ACTIVOS}
ws = None

# ===== FUNCIONES DE UTILIDAD =====

def enviar_mensaje_telegram(texto: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": texto,
        "parse_mode": "Markdown"
    }
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

# ====== FUNCIONES DE APERTURA Y CIERRE DE OPERACIONES ======

def abrir_operacion(simbolo, direccion, volumen, duracion):
    global capital_actual
    volumen = ajustar_volumen(volumen)
    if volumen > capital_actual:
        volumen = capital_actual

    compra_put_call = "CALL" if direccion == "COMPRA" else "PUT"

    # Generar ID único para contrato
    contract_id = f"contrato_{int(time.time()*1000)}"

    # Solicitud para comprar contrato
    msg_compra = {
        "buy": 1,
        "parameters": {
            "amount": volumen,
            "basis": "stake",
            "contract_type": compra_put_call.lower(),
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
    logging.info(f"Enviado pedido abrir operación {simbolo} {direccion} volumen {volumen}")

    capital_actual -= volumen

    hora_op = obtener_hora_deriv()
    texto = (
        f"✅ OPERACIÓN ABIERTA\n"
        f"🧭 Activo: {simbolo.capitalize()}\n"
        f"🕐 Hora: {formatear_hora(hora_op)} (Hora Deriv)\n"
        f"📉 Dirección: {direccion}\n"
        f"💵 Precio entrada: -- (confirmar en mensaje de compra)\n"
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
    if not operacion:
        logging.warning(f"No se encontró operación para contract_id {contract_id}")
        return
    if operacion["estado"] == "CERRADA":
        logging.info(f"Operación {contract_id} ya cerrada")
        return

    operacion["estado"] = "CERRADA"
    operacion["precio_salida"] = precio_salida
    ganancia = 0.0

    if resultado == "ganancia":
        ganancia = operacion["volumen"] * 0.8  # Ganancia aprox. 80%
        capital_actual += operacion["volumen"] + ganancia
        ganancia_diaria += ganancia
    else:
        # pérdida del stake
        ganancia = -operacion["volumen"]

    operacion["ganancia"] = ganancia

    hora_op = obtener_hora_deriv()
    texto = (
        f"📤 OPERACIÓN CERRADA\n"
        f"🧭 Activo: {operacion['simbolo'].capitalize()}\n"
        f"📈 Resultado: {'GANANCIA' if ganancia>0 else 'PÉRDIDA'} {'✅' if ganancia>0 else '❌'}\n"
        f"💵 Entrada: --\n"
        f"💸 Salida: {precio_salida}\n"
        f"📊 Ganancia: ${ganancia:.2f}"
    )
    enviar_mensaje_telegram(texto)

    texto_progreso = f"📈 Progreso diario: ${ganancia_diaria:.2f} / ${META_DIARIA:.2f}"
    enviar_mensaje_telegram(texto_progreso)

    if ganancia_diaria >= META_DIARIA:
        texto_meta = (
            f"🎯 META DIARIA ALCANZADA\n"
            f"✅ Ganancia total: ${ganancia_diaria:.2f}\n"
            f"🔒 Bot detenido por hoy."
        )
        enviar_mensaje_telegram(texto_meta)
        detener_bot()

def detener_bot():
    global bot_activo
    bot_activo = False

def reiniciar_dia():
    global ganancia_diaria, operaciones_dia, bot_activo
    ganancia_diaria = 0.0
    operaciones_dia.clear()
    bot_activo = True
    enviar_mensaje_telegram("♻️ *Reinicio diario* - Nuevo ciclo iniciado, bot activo.")
    logging.info("Reinicio diario completado.")

# ====== PROCESAMIENTO DE MENSAJES DEL WEBSOCKET ======

def on_message(wsapp, message):
    global bot_activo
    data = json.loads(message)

    # Manejar mensajes respuesta compra
    if "buy" in data:
        buy_data = data["buy"]
        if buy_data.get("is_sold", False):
            contract_id = buy_data.get("contract_id")
            resultado = "ganancia" if buy_data.get("profit", 0) > 0 else "perdida"
            precio_salida = buy_data.get("sell_price", 0)
            cerrar_operacion_por_contrato(contract_id, resultado, precio_salida)
    # Manejar ticks para actualizar candles y calcular indicadores
    elif "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]
        ahora = obtener_hora_deriv()
        # Añadir precio a datos de candles simulando velas de 1m
        if simbolo in datos_candles:
            datos_candles[simbolo].append(precio)
            if len(datos_candles[simbolo]) > 50:
                datos_candles[simbolo].pop(0)
    # Procesar mensajes de contrato abierto, error, etc.
    elif "error" in data:
        logging.error(f"Error API Deriv: {data['error']['message']}")
    else:
        pass  # otros mensajes ignorados por ahora

def on_open(wsapp):
    logging.info("WebSocket conectado a Deriv")
    # Suscribirse a ticks de los activos
    for activo in ACTIVOS:
        subs_msg = {
            "ticks": activo,
            "req_id": int(time.time())
        }
        wsapp.send(json.dumps(subs_msg))

    # Enviar mensaje al iniciar bot
    iniciar_bot()

def on_error(wsapp, error):
    logging.error(f"WebSocket error: {error}")

def on_close(wsapp, close_status_code, close_msg):
    logging.warning(f"WebSocket cerrado: {close_status_code} - {close_msg}")

def conectar_websocket():
    global ws
    url = f"wss://ws.binaryws.com/websockets/v3?app_id=1089&l=EN"
    headers = {"Authorization": f"Bearer {DERIV_TOKEN}"}
    ws = websocket.WebSocketApp(url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close,
                                header=headers)
    ws.run_forever()

# ====== LÓGICA DE ANÁLISIS Y OPERACIÓN ======

def analizar_y_operar():
    global bot_activo
    if not bot_activo:
        logging.info("Bot detenido por meta diaria alcanzada.")
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
            logging.info(f"Señal detectada {activo} -> {direccion}")
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

    # Mantener el main thread vivo
    while True:
        time.sleep(1)
