import websocket
import json
import threading
import time
import pytz
import requests
from datetime import datetime
from flask import Flask

# ======= CONFIGURACIÓN ========
DERIV_TOKEN = "UbQVaW5F4f7DWyM"  # Tu token API de Deriv aquí
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"
CAPITAL_INICIAL = 22.0
META_DIARIA = 20.0
VOLUMEN_FIJO = 0.20  # apuesta fija en USD
tz_venezuela = pytz.timezone("America/Caracas")

HORARIOS_OPERACION = [
    (6, 0, 11, 0),
    (14, 0, 18, 0),
    (20, 0, 23, 0),
]

ACTIVOS = [
    "BOOM100", "BOOM300", "BOOM500", "BOOM600",
    "CRASH100", "CRASH300", "CRASH500", "CRASH600",
    "R_10", "R_25", "R_50", "R_75"
]

precios_activos = {activo: [] for activo in ACTIVOS}
ganancias_del_dia = 0.0
operaciones_abiertas = {}  # Guardará datos de contratos abiertos
lock = threading.Lock()
app = Flask(__name__)

ws_global = None  # Guardaremos el WebSocket para enviar mensajes de compra


# ======= FUNCIONES UTILES ========

def ahora_venezuela():
    return datetime.now(tz_venezuela)

def esta_en_horario():
    ahora = ahora_venezuela()
    for h_inicio, m_inicio, h_fin, m_fin in HORARIOS_OPERACION:
        inicio = ahora.replace(hour=h_inicio, minute=m_inicio, second=0, microsecond=0)
        fin = ahora.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
        if inicio <= ahora <= fin:
            return True
    return False

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHANNEL, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

def reiniciar_ganancias_diarias():
    global ganancias_del_dia
    ahora = ahora_venezuela()
    if ahora.hour == 0 and ahora.minute == 0:
        with lock:
            ganancias_del_dia = 0.0
            enviar_telegram("🔄 Reinicio diario de ganancias y operaciones.")

def calcular_ema(datos, periodo):
    k = 2 / (periodo + 1)
    ema = []
    for i, precio in enumerate(datos):
        if i == 0:
            ema.append(precio)
        else:
            ema.append(precio * k + ema[i-1] * (1 - k))
    return ema

def calcular_rsi(datos, periodo=14):
    ganancias, perdidas, rsi = [], [], []
    for i in range(1, len(datos)):
        cambio = datos[i] - datos[i-1]
        ganancias.append(max(cambio, 0))
        perdidas.append(abs(min(cambio, 0)))
    if len(ganancias) < periodo:
        return [None]*len(datos)
    pg = sum(ganancias[:periodo]) / periodo
    pp = sum(perdidas[:periodo]) / periodo
    rs = pg / pp if pp else 0
    rsi.append(100 - (100 / (1 + rs)) if pp else 100)
    for i in range(periodo, len(ganancias)):
        pg = (pg * (periodo - 1) + ganancias[i]) / periodo
        pp = (pp * (periodo - 1) + perdidas[i]) / periodo
        rs = pg / pp if pp else 0
        rsi.append(100 - (100 / (1 + rs)) if pp else 100)
    return [None]*periodo + rsi


# ======= FUNCIONES PARA OPERAR REALMENTE ========

def comprar_contrato(simbolo, direccion, volumen, duracion=5):
    global ws_global
    if not ws_global:
        print("WebSocket no está listo para enviar órdenes")
        return None
    contrato = {
        "buy": 1,
        "subscribe": 1,
        "parameters": {
            "amount": volumen,
            "contract_type": "CALL" if direccion == "CALL" else "PUT",
            "currency": "USD",
            "duration": duracion,
            "duration_unit": "m",
            "symbol": simbolo,
            "basis": "stake",
            "barrier": None,
            "date_expiry": None,
            "date_start": None,
            "limit_order": None,
            "trailing_stop": None,
            "stop_loss": None,
            "take_profit": None,
        }
    }
    ws_global.send(json.dumps(contrato))


def abrir_operacion(simbolo, direccion, volumen, duracion=5):
    global ganancias_del_dia, operaciones_abiertas
    if ganancias_del_dia >= META_DIARIA:
        print("Meta diaria alcanzada, no se abren más operaciones.")
        return
    with lock:
        if simbolo in operaciones_abiertas:
            print(f"Ya hay operación abierta en {simbolo}")
            return
        operaciones_abiertas[simbolo] = {
            "direccion": direccion,
            "volumen": volumen,
            "inicio": ahora_venezuela(),
            "duracion": duracion,
            "estado": "abierta",
            "contrato_id": None,
            "resultado": None,
        }
    enviar_telegram(
        f"🚀 Abriendo operación\nActivo: {simbolo}\nDirección: {'COMPRA' if direccion=='CALL' else 'VENTA'}\nVolumen: ${volumen}\nDuración: {duracion} minutos\nHora: {ahora_venezuela().strftime('%I:%M %p')}"
    )
    comprar_contrato(simbolo, direccion, volumen, duracion)


def cerrar_operacion(simbolo, ganancia):
    global ganancias_del_dia
    with lock:
        if simbolo not in operaciones_abiertas:
            return
        operaciones_abiertas[simbolo]["estado"] = "cerrada"
        operaciones_abiertas[simbolo]["resultado"] = ganancia
        ganancias_del_dia += ganancia
        enviar_telegram(
            f"✅ Operación cerrada\nActivo: {simbolo}\nGanancia: ${ganancia:.2f}\nTotal hoy: ${ganancias_del_dia:.2f}"
        )
        operaciones_abiertas.pop(simbolo)
        if ganancias_del_dia >= META_DIARIA:
            enviar_telegram(f"🎯 Meta diaria alcanzada: ${META_DIARIA}. Bot descansará.")


# ======= PROCESAMIENTO DE RESPUESTAS DEL WEBSOCKET ========

def procesar_respuesta(data):
    global operaciones_abiertas
    if "error" in data:
        print("Error en la respuesta:", data["error"]["message"])
        return
    if "buy" in data:
        # Confirmación de compra
        contrato = data["buy"]
        simbolo = contrato.get("symbol")
        contrato_id = contrato.get("contract_id")
        is_sold = contrato.get("is_sold", False)
        profit = contrato.get("profit", 0)
        estado = contrato.get("status", "")
        if simbolo and contrato_id:
            with lock:
                if simbolo in operaciones_abiertas:
                    operaciones_abiertas[simbolo]["contrato_id"] = contrato_id
                    if is_sold:
                        # Operación cerrada con resultado
                        ganancias = profit
                        cerrar_operacion(simbolo, ganancias)
                    else:
                        print(f"Operación {simbolo} abierta con contrato ID {contrato_id}")
                else:
                    print(f"Contrato recibido para {simbolo} pero sin operación abierta.")
    elif "contract" in data:
        # Otra estructura posible, revisar si se usa
        pass


# ======= WEBSOCKET ========

def on_message(ws, message):
    global precios_activos
    data = json.loads(message)

    # Procesar ticks para actualizar precios
    if "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]
        if simbolo in precios_activos:
            precios_activos[simbolo].append(precio)
            if len(precios_activos[simbolo]) > 100:
                precios_activos[simbolo].pop(0)
            print(f"{simbolo}: precio recibido {precio}")

    # Procesar respuestas a compras y actualizaciones de contratos
    if "buy" in data or "error" in data:
        procesar_respuesta(data)


def on_error(ws, error):
    print("WebSocket error:", error)


def on_close(ws, *args):
    print("WebSocket desconectado. Reintentando en 5s...")
    time.sleep(5)
    iniciar_websocket()


def on_open(ws):
    global ws_global
    ws_global = ws
    # Autorizar con token
    ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    time.sleep(1)
    # Suscribir ticks de todos los activos
    for activo in ACTIVOS:
        ws.send(json.dumps({"ticks": activo}))


def iniciar_websocket():
    ws = websocket.WebSocketApp(
        "wss://ws.binaryws.com/websockets/v3?app_id=1089",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws.run_forever()


# ======= SMS DE ESTADO CADA HORA ========

def notificar_estado():
    while True:
        ahora = ahora_venezuela()
        if ahora.minute == 0:
            estado = "ACTIVO y operando." if esta_en_horario() else "En descanso (fuera de horario operativo)."
            enviar_telegram(f"🕐 {ahora.strftime('%I:%M %p')} (Hora Venezuela)\n🔔 Estado del Bot: {estado}")
            time.sleep(60)
        else:
            time.sleep(30)


# ======= LÓGICA DE ANÁLISIS Y OPERACIÓN ========

def analizar_y_operar():
    if not esta_en_horario():
        return
    if ganancias_del_dia >= META_DIARIA:
        return
    for simbolo in ACTIVOS:
        precios = precios_activos[simbolo]
        if len(precios) < 20:
            continue
        ema = calcular_ema(precios, 14)
        rsi = calcular_rsi(precios, 14)
        if ema[-1] is None or rsi[-1] is None:
            continue
        if precios[-1] > ema[-1] and 30 < rsi[-1] < 70:
            direccion = "CALL"
        elif precios[-1] < ema[-1] and 30 < rsi[-1] < 70:
            direccion = "PUT"
        else:
            continue
        with lock:
            if simbolo not in operaciones_abiertas:
                abrir_operacion(simbolo, direccion, VOLUMEN_FIJO)


# ======= CICLO PRINCIPAL ========

def reiniciar_ganancias_diarias_periodico():
    while True:
        reiniciar_ganancias_diarias()
        time.sleep(60)


def ciclo_operativo():
    while True:
        analizar_y_operar()
        time.sleep(300)


# ======= EJECUCIÓN ========

@app.route('/')
def home():
    return "Bot activo en Render."

if __name__ == "__main__":
    enviar_telegram("✅ El bot de Deriv está activo y listo para enviar señales y abrir operaciones.")
    threading.Thread(target=iniciar_websocket, daemon=True).start()
    threading.Thread(target=notificar_estado, daemon=True).start()
    threading.Thread(target=ciclo_operativo, daemon=True).start()
    threading.Thread(target=reiniciar_ganancias_diarias_periodico, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
