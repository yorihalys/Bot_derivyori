import websocket
import json
import threading
import time
import pytz
import requests
from datetime import datetime
from flask import Flask

# ====== CONFIGURACIÓN USUARIO ======

DERIV_TOKEN = "UbQVaW5F4f7DWyM"
TELEGRAM_BOT_TOKEN = "7996503475:AAG6mEPhRF5TlK_syTzmhKYWV_2ETpGkRXU"
TELEGRAM_CHANNEL = "@yorihaly18"

CAPITAL_INICIAL = 22.0
META_DIARIA = 20.0
VOLUMEN_FIJO = 0.20  # volumen fijo por operación

# Horarios de operación (hora Venezuela UTC-4)
HORARIOS_OPERACION = [
    (6, 0, 11, 0),   # 6:00 - 11:00
    (14, 0, 18, 0),  # 14:00 - 18:00
    (20, 0, 23, 0),  # 20:00 - 23:00
]

tz_venezuela = pytz.timezone("America/Caracas")

app = Flask(__name__)

# Lista de activos para suscribirse y operar
ACTIVOS = [
    "BOOM100", "BOOM300", "BOOM500", "BOOM600",
    "CRASH100", "CRASH300", "CRASH500", "CRASH600",
    "VOLATILITY10", "VOLATILITY25", "VOLATILITY50", "VOLATILITY75"
]

# Diccionario para almacenar precios por activo
precios_activos = {activo: [] for activo in ACTIVOS}

# Variables globales
ganancias_del_dia = 0.0
operaciones_abiertas = {}
lock = threading.Lock()

# ----- Funciones Auxiliares -----

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHANNEL, "text": mensaje, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data)
        return r.status_code == 200
    except Exception as e:
        print(f"Error enviando Telegram: {e}")
        return False

def ahora_venezuela():
    return datetime.now(tz_venezuela)

def esta_en_horario():
    ahora = ahora_venezuela()
    for inicio_h, inicio_m, fin_h, fin_m in HORARIOS_OPERACION:
        inicio = ahora.replace(hour=inicio_h, minute=inicio_m, second=0, microsecond=0)
        fin = ahora.replace(hour=fin_h, minute=fin_m, second=0, microsecond=0)
        if inicio <= ahora <= fin:
            return True
    return False

def reiniciar_ganancias_si_medio_dia():
    global ganancias_del_dia
    ahora = ahora_venezuela()
    if ahora.hour == 0 and ahora.minute == 0:
        with lock:
            ganancias_del_dia = 0.0
        enviar_telegram("🔄 Reinicio diario de ganancias y operaciones.")

# ----- Funciones de Indicadores Técnicos -----

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
    ganancias = []
    perdidas = []
    rsi = []

    for i in range(1, len(datos)):
        cambio = datos[i] - datos[i - 1]
        ganancias.append(max(cambio, 0))
        perdidas.append(abs(min(cambio, 0)))

    promedio_ganancia = sum(ganancias[:periodo]) / periodo
    promedio_perdida = sum(perdidas[:periodo]) / periodo

    if promedio_perdida == 0:
        rsi.append(100)
    else:
        rs = promedio_ganancia / promedio_perdida
        rsi.append(100 - (100 / (1 + rs)))

    for i in range(periodo, len(ganancias)):
        promedio_ganancia = (promedio_ganancia * (periodo - 1) + ganancias[i]) / periodo
        promedio_perdida = (promedio_perdida * (periodo - 1) + perdidas[i]) / periodo

        if promedio_perdida == 0:
            rsi.append(100)
        else:
            rs = promedio_ganancia / promedio_perdida
            rsi.append(100 - (100 / (1 + rs)))

    return [None] * periodo + rsi

# ----- Análisis de señales y operaciones -----

def abrir_operacion(simbolo, direccion, volumen, duracion=5):
    global ganancias_del_dia, operaciones_abiertas

    if ganancias_del_dia >= META_DIARIA:
        return

    entrada = ahora_venezuela().strftime("%Y-%m-%d %H:%M:%S")
    mensaje = (
        f"🚀 *Operación abierta*\n"
        f"Activo: {simbolo}\n"
        f"Dirección: {'COMPRA' if direccion == 'CALL' else 'VENTA'}\n"
        f"Volumen: ${volumen}\n"
        f"Duración: {duracion} minutos\n"
        f"Hora: {entrada}"
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
    global ganancias_del_dia, operaciones_abiertas

    with lock:
        if simbolo not in operaciones_abiertas:
            return

        operacion = operaciones_abiertas.pop(simbolo)

    # Simulación de resultado: 80% ganancia (para demo)
    resultado = VOLUMEN_FIJO * 0.8
    ganancias_del_dia += resultado

    mensaje = (
        f"✅ *Operación cerrada*\n"
        f"Activo: {simbolo}\n"
        f"Resultado: +${resultado:.2f}\n"
        f"Ganancias totales hoy: ${ganancias_del_dia:.2f}"
    )
    enviar_telegram(mensaje)

    if ganancias_del_dia >= META_DIARIA:
        enviar_telegram(f"🎯 *Meta diaria alcanzada:* ${META_DIARIA}.\nBot detiene operaciones por hoy.")

def analizar_y_operar():
    if not esta_en_horario():
        print("No está en horario operativo, bot en modo descanso.")
        return

    if ganancias_del_dia >= META_DIARIA:
        print("Meta diaria alcanzada, no se abrirán más operaciones hoy.")
        return

    for simbolo in ACTIVOS:
        precios = precios_activos[simbolo]

        if len(precios) < 20:
            print(f"Esperando datos suficientes para análisis de {simbolo}...")
            continue

        ema = calcular_ema(precios, 14)
        rsi = calcular_rsi(precios, 14)

        ultimo_precio = precios[-1]
        ultima_ema = ema[-1]
        ultimo_rsi = rsi[-1]

        if ultima_ema is None or ultimo_rsi is None:
            print(f"Indicadores no calculados aún para {simbolo}.")
            continue

        if ultimo_precio > ultima_ema and 30 < ultimo_rsi < 70:
            direccion = "CALL"
        elif ultimo_precio < ultima_ema and 30 < ultimo_rsi < 70:
            direccion = "PUT"
        else:
            print(f"Condiciones de entrada no cumplidas para {simbolo}.")
            continue

        with lock:
            if simbolo not in operaciones_abiertas:
                abrir_operacion(simbolo, direccion, VOLUMEN_FIJO)

# ----- WebSocket para datos en vivo -----

def on_message(ws, message):
    data = json.loads(message)

    if "tick" in data:
        simbolo = data["tick"]["symbol"]
        precio = data["tick"]["quote"]

        if simbolo in precios_activos:
            precios_activos[simbolo].append(precio)
            if len(precios_activos[simbolo]) > 100:
                precios_activos[simbolo].pop(0)

def on_error(ws, error):
    print(f"Error WebSocket: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket cerrado, reconectando...")
    time.sleep(5)
    iniciar_websocket()

def on_open(ws):
    print("WebSocket conectado, suscribiendo ticks de activos...")
    auth_msg = {
        "authorize": DERIV_TOKEN
    }
    ws.send(json.dumps(auth_msg))

    for activo in ACTIVOS:
        subscribe_msg = {
            "ticks": activo
        }
        ws.send(json.dumps(subscribe_msg))

def iniciar_websocket():
    url = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    ws = websocket.WebSocketApp(url,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close,
                                on_open=on_open)
    ws.run_forever()

# ----- Flask para mantener vivo el servicio -----

@app.route('/')
def home():
    return "Bot de trading Deriv activo."

# ----- Función para enviar estado cada hora -----

def enviar_estado_hora():
    while True:
        ahora = ahora_venezuela()
        minuto = ahora.minute
        segundo = ahora.second

        # Esperar hasta la próxima hora en punto
        if minuto == 0 and segundo == 0:
            hora_actual = ahora.hour
            texto_hora = ahora.strftime('%I:%M %p')

            if esta_en_horario():
                mensaje = (f"🔔 *Estado del Bot:* ACTIVO y operando.\n"
                           f"🕐 Hora Venezuela: {texto_hora}")
            else:
                mensaje = (f"💤 *Estado del Bot:* En descanso (fuera de horario operativo).\n"
                           f"🕐 Hora Venezuela: {texto_hora}")

            enviar_telegram(mensaje)
            # Espera un minuto para no enviar múltiples mensajes en la misma hora
            time.sleep(60)
        else:
            # Dormir 1 segundo y chequear de nuevo para sincronizar bien en la hora
            time.sleep(1)

def iniciar_monitor_estado():
    hilo_estado = threading.Thread(target=enviar_estado_hora)
    hilo_estado.daemon = True
    hilo_estado.start()

# ----- Tarea periódica principal -----

def tarea_periodica():
    while True:
        reiniciar_ganancias_si_medio_dia()
        analizar_y_operar()
        time.sleep(300)  # Cada 5 minutos

# ----- MAIN -----

if __name__ == "__main__":
    threading.Thread(target=tarea_periodica, daemon=True).start()
    threading.Thread(target=iniciar_websocket, daemon=True).start()
    iniciar_monitor_estado()
    app.run(host="0.0.0.0", port=8080)

# ----- ENVÍO DE MENSAJE DE PRUEBA AL INICIAR EL BOT -----

mensaje = "✅ El bot de Deriv está activo y listo para enviar señales."
url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
data = {"chat_id": TELEGRAM_CHANNEL, "text": mensaje, "parse_mode": "Markdown"}
try:
    requests.post(url, data=data)
except Exception as e:
    print(f"❌ Error enviando mensaje de prueba: {e}")
