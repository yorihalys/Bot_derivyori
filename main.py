import websocket
import json
import threading
import time
import requests
from datetime import datetime, timezone
from flask import Flask

# ======= CONFIGURACIÓN ========
DERIV_TOKEN = "UbQVaW5F4f7DWyM"
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"
CAPITAL_INICIAL = 22.0
META_DIARIA = 20.0
VOLUMEN_FIJO = 0.20

HORARIOS_OPERACION_UTC = [
    (10, 0, 15, 0),
    (18, 0, 22, 0),
    (0, 0, 3, 0),
]

ACTIVOS = [
    "BOOM100", "BOOM300", "BOOM500", "BOOM600",
    "CRASH100", "CRASH300", "CRASH500", "CRASH600",
    "R_10", "R_25", "R_50", "R_75"
]

precios_activos = {activo: [] for activo in ACTIVOS}
ganancias_del_dia = 0.0
operaciones_abiertas = {}
lock = threading.Lock()
app = Flask(__name__)

ws_global = None

# ======= FUNCIONES UTILES ========

def ahora_utc():
    return datetime.now(timezone.utc)

def esta_en_horario():
    ahora = ahora_utc()
    for h_inicio, m_inicio, h_fin, m_fin in HORARIOS_OPERACION_UTC:
        inicio = ahora.replace(hour=h_inicio, minute=m_inicio, second=0, microsecond=0)
        fin = ahora.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
        if fin < inicio:
            if ahora >= inicio or ahora <= fin:
                return True
        else:
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
    ahora = ahora_utc()
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

# ======= FUNCIONES PARA OPERAR ========

def comprar_contrato(simbolo, direccion, volumen, duracion=5):
    global ws_global
    if not ws_global:
        print("WebSocket no está listo para enviar órdenes")
        return None
    contrato = {
        "buy": 1,
        "parameters": {
            "amount": volumen,
            "contract_type": "CALL" if direccion == "CALL" else "PUT",
            "currency": "USD",
            "duration": duracion,
            "duration_unit": "m",
            "symbol": simbolo,
            "basis": "stake",
        }
    }
    print(f"Enviando orden: {direccion} {simbolo} volumen: {volumen}")
    ws_global.send(json.dumps(contrato))

def abrir_operacion(simbolo, direccion, volumen, duracion=5):
    global ganancias_del_dia, operaciones_abiertas
    if ganancias_del_dia >= META_DIARIA:
        print("Meta diaria alcanzada.")
        return
    with lock:
        if simbolo in operaciones_abiertas:
            print(f"Ya hay operación abierta en {simbolo}")
            return
        operaciones_abiertas[simbolo] = {
            "direccion": direccion,
            "volumen": volumen,
            "inicio": ahora_utc(),
            "duracion": duracion,
            "estado": "abierta",
            "contrato_id": None,
            "resultado": None,
        }
    enviar_telegram(
        f"🚀 Abriendo operación\nActivo: {simbolo}\nDirección: {'COMPRA' if direccion=='CALL' else 'VENTA'}\nVolumen: ${volumen}\nDuración: {duracion} minutos\nHora UTC: {ahora_utc().strftime('%H:%M')}"
    )
    comprar_contrato(simbolo, direccion, volumen, duracion)
# ====== FORZAR UNA OPERACIÓN DE PRUEBA =======
time.sleep(3)  # Espera unos segundos a que se conecte el WebSocket
abrir_operacion("CRASH600", "PUT", 0.20)
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

# ======= WEBSOCKET ========

def procesar_respuesta(data):
    if "error" in data:
        print("Error:", data["error"]["message"])
        return
    if "buy" in data:
        contrato = data["buy"]
        simbolo = contrato.get("symbol")
        contrato_id = contrato.get("contract_id")
        is_sold = contrato.get("is_sold", False)
        profit = contrato.get("profit", 0)
        if simbolo and contrato_id:
            with lock:
                if simbolo in operaciones_abiertas:
                    operaciones_abiertas[simbolo]["contrato_id"] = contrato_id
                    if is_sold:
                        cerrar_operacion(simbolo, profit)
                else:
                    print(f"Contrato recibido pero {simbolo} no tiene operación abierta.")

def on_message(ws, message):
    global precios_activos
    data = json.loads(message)
    if "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]
        if simbolo in precios_activos:
            precios_activos[simbolo].append(precio)
            if len(precios_activos[simbolo]) > 100:
                precios_activos[simbolo].pop(0)
            print(f"{simbolo}: {precio}")
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

# ======= FUNCIONES HORARIAS ========

def notificar_estado():
    while True:
        ahora = ahora_utc()
        if ahora.minute == 0:
            estado = "✅ ACTIVO" if esta_en_horario() else "🛌 En descanso"
            enviar_telegram(f"🕐 {ahora.strftime('%H:%M')} UTC\nEstado: {estado}")
            time.sleep(60)
        else:
            time.sleep(30)

# ======= ANÁLISIS Y OPERACIÓN ========

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

# ======= CICLOS ========

def reiniciar_ganancias_diarias_periodico():
    while True:
        reiniciar_ganancias_diarias()
        time.sleep(60)

def ciclo_operativo():
    while True:
        reiniciar_ganancias_diarias()
        analizar_y_operar()
        time.sleep(300)

# ======= EJECUCIÓN ========

@app.route('/')
def home():
    return "Bot activo."

if __name__ == "__main__":
    enviar_telegram("✅ Bot de Deriv encendido.")
    threading.Thread(target=iniciar_websocket, daemon=True).start()
    threading.Thread(target=notificar_estado, daemon=True).start()
    threading.Thread(target=ciclo_operativo, daemon=True).start()
    threading.Thread(target=reiniciar_ganancias_diarias_periodico, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
