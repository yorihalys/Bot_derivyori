import websocket
import json
import threading
import time
import pytz
import requests
from datetime import datetime
from flask import Flask

# ======= CONFIGURACIÓN ========
DERIV_TOKEN = "UbQVaW5F4f7DWyM"
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"
CAPITAL_INICIAL = 22.0
META_DIARIA = 20.0
VOLUMEN_FIJO = 0.20
tz_venezuela = pytz.timezone("America/Caracas")

HORARIOS_OPERACION = [
    (6, 0, 11, 0),
    (14, 0, 18, 0),
    (20, 0, 23, 0),
]
ACTIVOS = [
    "boom_100_index", "boom_500_index", "boom_600_index",
    "crash_100_index", "crash_500_index", "crash_600_index",
    "volatility_10_index", "volatility_25_index",
    "volatility_50_index", "volatility_75_index"
]


precios_activos = {activo: [] for activo in ACTIVOS}
ganancias_del_dia = 0.0
operaciones_abiertas = {}
lock = threading.Lock()
app = Flask(__name__)

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

# ======= TRADING ========

def abrir_operacion(simbolo, direccion, volumen, duracion=5):
    global ganancias_del_dia
    if ganancias_del_dia >= META_DIARIA:
        return
    mensaje = (
        f"🚀 *Operación abierta*\n"
        f"Activo: {simbolo}\n"
        f"Dirección: {'COMPRA' if direccion == 'CALL' else 'VENTA'}\n"
        f"Volumen: ${volumen}\n"
        f"Duración: {duracion} minutos\n"
        f"Hora: {ahora_venezuela().strftime('%I:%M %p')}"
    )
    enviar_telegram(mensaje)
    with lock:
        operaciones_abiertas[simbolo] = {
            "direccion": direccion,
            "volumen": volumen,
            "inicio": ahora_venezuela(),
            "duracion": duracion,
        }
    threading.Timer(duracion * 60, cerrar_operacion, args=(simbolo,)).start()

def cerrar_operacion(simbolo):
    global ganancias_del_dia
    with lock:
        if simbolo not in operaciones_abiertas:
            return
        operaciones_abiertas.pop(simbolo)
    resultado = VOLUMEN_FIJO * 0.8  # Simulación ganancia 80%
    ganancias_del_dia += resultado
    mensaje = (
        f"✅ *Operación cerrada*\n"
        f"Activo: {simbolo}\n"
        f"Ganancia: +${resultado:.2f}\n"
        f"Total hoy: ${ganancias_del_dia:.2f}"
    )
    enviar_telegram(mensaje)
    if ganancias_del_dia >= META_DIARIA:
        enviar_telegram(f"🎯 *Meta diaria alcanzada:* ${META_DIARIA}.\nBot descansa.")

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

# ======= WEBSOCKET ========

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]
        if simbolo in precios_activos:
            precios_activos[simbolo].append(precio)
            if len(precios_activos[simbolo]) > 100:
                precios_activos[simbolo].pop(0)
            # Debug print para confirmar recepción de datos
            print(f"{simbolo}: precio recibido {precio}")

def on_error(ws, error):
    print("WebSocket error:", error)

def on_close(ws, *args):
    print("WebSocket desconectado. Reintentando en 5s...")
    time.sleep(5)
    iniciar_websocket()

def on_open(ws):
    ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    time.sleep(1)
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
            enviar_telegram(f"🕐 {ahora.strftime('%I:%M %p')} (Hora Venezuela)\n🔔 Estado del Bot: *{estado}*")
            time.sleep(60)
        else:
            time.sleep(30)

# ======= CICLO PRINCIPAL OPERATIVO ========

def ciclo_operativo():
    while True:
        reiniciar_ganancias_diarias()
        analizar_y_operar()
        time.sleep(300)  # Cada 5 minutos

# ======= EJECUCIÓN ========

@app.route('/')
def home():
    return "Bot activo en Render."

if __name__ == "__main__":
    enviar_telegram("✅ El bot de Deriv está activo y listo para enviar señales.")
    threading.Thread(target=iniciar_websocket, daemon=True).start()
    threading.Thread(target=notificar_estado, daemon=True).start()
    threading.Thread(target=ciclo_operativo, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
