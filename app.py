from flask import Flask, render_template, request, redirect, Response
import psycopg2
import os
import csv
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict  # <<--- Asegúrate de importar Counter y defaultdict
import logging

app = Flask(__name__)

# Configuración del logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# URL a la base de datos en Render (automáticamente inyectada)
DATABASE_URL = os.environ.get("DATABASE_URL")

# Crear tablas si no existen
def crear_tablas():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal TEXT NOT NULL,
            respuesta TEXT NOT NULL,
            envio TEXT NOT NULL,
            ip TEXT NOT NULL,
            comentario TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tablas()

@app.route("/")
def home():
    return "Servidor activo"

# (Aquí irían los demás endpoints (/voto, /gracias, /comentario, /descargar), asumiendo que ya los tienes funcionando)

@app.route("/dashboard")
def dashboard():
    # 1) Recupero todos los registros de la tabla 'votos'
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT sucursal, respuesta, envio, timestamp, comentario FROM votos")
    votos = cur.fetchall()
    cur.close()
    conn.close()

    # 2) Agrupo por 'envio' para quedarme siempre con el último voto (único por envío)
    votos_unicos = {}
    for sucursal, respuesta, envio, timestamp, comentario in votos:
        # Si el envío no está en el dict, lo guardo; de lo contrario me quedo siempre con el primero
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta, timestamp, comentario)

    # 3) Calcular métricas:
    #    - cuántos "positivo" por sucursal (solo votos únicos)
    #    - cuántos votos por día (solo votos únicos)
    positivos_por_sucursal = Counter()
    votos_por_dia = defaultdict(int)
    ultimos_votos = []

    for envio, (sucursal, respuesta, timestamp, comentario) in votos_unicos.items():
        # Si la respuesta es "positivo", incrementar contador por sucursal
        if respuesta.strip().lower() == "positivo":
            positivos_por_sucursal[sucursal] += 1

        # Cuento votos por fecha (timestamp.date())
        fecha = timestamp.date()
        votos_por_dia[fecha] += 1

        # Para mostrar tabla de "últimos 100 votos únicos"
        ultimos_votos.append((envio, timestamp, sucursal, respuesta, comentario))

    # 4) Ordeno los resultados de votos_por_dia y tomo los 100 más recientes
    votos_dia = sorted(votos_por_dia.items())  # [(fecha, cantidad), ...]
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]

    # 5) Preparar series de etiquetas y datos para Chart.js
    labels = [fecha.strftime("%Y-%m-%d") for fecha, _ in votos_dia]
    data = [cantidad for _, cantidad in votos_dia]

    # 6) Ordenar sucursales por cantidad de votos positivos (descendente)
    top_positivos = sorted(positivos_por_sucursal.items(), key=lambda x: x[1], reverse=True)

    # 7) Render de la plantilla 'dashboard.html'
    return render_template("dashboard.html",
                           top_positivos=top_positivos,
                           votos_dia=votos_dia,
                           ultimos_votos=ultimos_votos,
                           labels=labels,
                           data=data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --- Creamos la tabla 'votos' si no existe aún
def crear_tabla_votos():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal TEXT NOT NULL,
            envio TEXT NOT NULL,
            respuesta TEXT NOT NULL,
            ip TEXT NOT NULL,
            comentario TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tabla_votos()

# ----------------------------------------------------------------------
# Ruta que recibe el voto (GET). Ejemplo:
#   /voto?sucursal=...&respuesta=...&envio=...
# ----------------------------------------------------------------------
@app.route("/voto")
def voto():
    # (Aquí suponemos que ya tenés lógica similar a la que venías usando.
    #  Por brevedad, incluyo solo un esqueleto muy simple.)
    sucursal = request.args.get("sucursal")
    respuesta = request.args.get("respuesta")
    envio = request.args.get("envio")
    ip_full = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip = ip_full.split(",")[0].strip() if ip_full else None

    if not (sucursal and respuesta and envio):
        return "Datos incompletos", 400

    timestamp = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Insertamos el voto (sin comentario por ahora)
        cur.execute("""
            INSERT INTO votos (timestamp, sucursal, envio, respuesta, ip, comentario)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (timestamp, sucursal, envio, respuesta, ip, ""))
        conn.commit()
        cur.close()
        conn.close()

        # Si la respuesta fue "si", redirigimos a formulario de comentario;
        # de lo contrario, a la página de "gracias_no.html"
        if respuesta.lower() == "si":
            return render_template("form_comentario.html", envio=envio, ip=ip)
        else:
            return render_template("gracias_no.html")
    except Exception as e:
        return f"Error al guardar el voto: {e}", 500


# ----------------------------------------------------------------------
# Ruta que procesa el formulario de comentario (POST)
# ----------------------------------------------------------------------
@app.route("/comentario", methods=["POST"])
def comentario():
    comentario_text = request.form.get("comentario")
    envio = request.form.get("envio")
    ip = request.form.get("ip")

    if not (envio and ip):
        return "Datos incompletos", 400

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1) Buscamos el ID del voto más reciente para este envío+IP
        cur.execute("""
            SELECT id
            FROM votos
            WHERE envio = %s
              AND ip = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """, (envio, ip))
        fila = cur.fetchone()

        if fila:
            voto_id = fila[0]
            # 2) Actualizamos sólo ese registro con el comentario
            cur.execute("""
                UPDATE votos
                   SET comentario = %s
                 WHERE id = %s
            """, (comentario_text, voto_id))
            conn.commit()
        else:
            cur.close()
            conn.close()
            return "No se encontró el voto para actualizar.", 404

        cur.close()
        conn.close()
        return render_template("gracias_tiempo.html")
    except Exception as e:
        return f"Error al guardar el comentario: {e}", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
