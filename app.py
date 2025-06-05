# =============================================================================
# app.py
# =============================================================================

from flask import Flask, render_template, request, redirect, Response
import psycopg2
import os
import csv
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
import logging

app = Flask(__name__)

# -------------------------------------------------------------------------
# Configuración mínima de logging para ver eventos en consola
# -------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# -------------------------------------------------------------------------
# LEER y VALIDAR la variable de entorno DATABASE_URL (postgres en Render)
# -------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ La variable de entorno DATABASE_URL no está definida.")


# -------------------------------------------------------------------------
# Función que crea la tabla "votos" si aún no existe
# -------------------------------------------------------------------------
def crear_tabla_votos():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id          SERIAL PRIMARY KEY,
            timestamp   TIMESTAMPTZ NOT NULL,
            sucursal    TEXT  NOT NULL,
            envio       TEXT  NOT NULL,
            respuesta   TEXT  NOT NULL,
            ip          TEXT  NOT NULL,
            comentario  TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# Llamamos a la creación de la tabla al iniciar la app
crear_tabla_votos()



# =============================================================================
# RUTA: GET /
#   Simple “ping” para verificar que el servidor esté vivo
# =============================================================================
@app.route("/")
def home():
    return "Servidor activo"



# =============================================================================
# RUTA: GET /voto
#
#   Parámetros en query string:
#     - sucursal   (p.ej. "PALERMO")
#     - respuesta  ("si" o "no")
#     - envio      (número de envío, p.ej. "360002580979850")
#
#   Lo que hace:
#     1) Inserta un nuevo registro en la tabla votos con esos datos + IP del cliente.
#     2) Si respuesta == "si", muestra el template para pedir un comentario 
#         (form_comentario.html)   ← pasándole envio e ip como hidden.
#        Si respuesta == "no", muestra directamente “gracias_no.html”.
#
#   Ejemplo de URL:
#     /voto?sucursal=PALERMO&respuesta=si&envio=12345ABC
# =============================================================================
@app.route("/voto")
def voto():
    # — Recuperar parámetros desde la query string — 
    raw_query = request.query_string.decode()
    if ";" in raw_query and "&" not in raw_query:
        # Caso en que vengan separados por punto y coma: "sucursal=SUC;respuesta=si;envio=XXX"
        params = parse_qs(raw_query.replace(";", "&"))
        sucursal = params.get("sucursal", [None])[0]
        respuesta = params.get("respuesta", [None])[0]
        envio = params.get("envio", [None])[0]
    else:
        sucursal = request.args.get("sucursal")
        respuesta = request.args.get("respuesta")
        envio = request.args.get("envio")

    # — Obtener IP real (X-Forwarded-For o request.remote_addr) —
    ip_full = request.headers.get("X-Forwarded-For", request.remote_addr)
    ip = ip_full.split(",")[0].strip() if ip_full else None

    # Validar que exista al menos sucursal, respuesta, envio e IP
    if not (sucursal and respuesta and envio and ip):
        return "Datos incompletos", 400

    # Obtener timestamp en zona Argentina
    timestamp = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    # Insertar en la base de datos (comentario vacío por defecto)
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO votos (timestamp, sucursal, envio, respuesta, ip, comentario)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (timestamp, sucursal, envio, respuesta, ip, "" ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error al insertar voto: {e}")
        return f"Error al guardar el voto: {e}", 500

    # Si la respuesta fue “si”, mostramos el formulario de comentario
    if respuesta.strip().lower() == "si":
        return render_template("form_comentario.html", envio=envio, ip=ip)

    # Si fue “no”, simplemente mostramos la página de gracias_no.html
    return render_template("gracias_no.html")



# =============================================================================
# RUTA: POST /comentario
# 
#   Parámetros recibidos en el formulario (form_comentario.html):
#     - comentario
#     - envio
#     - ip
#
#   Lo que hace:
#     1) Busca en la tabla votos el registro más reciente para ese (envio + ip).
#     2) Actualiza solo la columna `comentario` de ese registro. 
#     3) Muestra la página “gracias_tiempo.html”.
# =============================================================================
@app.route("/comentario", methods=["POST"])
def comentario():
    comentario_text = request.form.get("comentario", "").strip()
    envio = request.form.get("envio")
    ip = request.form.get("ip")

    if not (envio and ip):
        return "Datos incompletos en comentario", 400

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1) Obtener el ID del voto más reciente para ese par (envio + ip)
        cur.execute("""
            SELECT id
            FROM votos
            WHERE envio = %s
              AND ip = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """, (envio, ip))
        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()
            return "No se encontró el voto para actualizar.", 404

        voto_id = row[0]

        # 2) Actualizar únicamente la columna comentario
        cur.execute("""
            UPDATE votos
               SET comentario = %s
             WHERE id = %s
        """, (comentario_text, voto_id))
        conn.commit()
        cur.close()
        conn.close()

        return render_template("gracias_tiempo.html")
    except Exception as e:
        logging.error(f"Error al guardar comentario: {e}")
        return f"Error al guardar el comentario: {e}", 500



# =============================================================================
# RUTA: GET /descargar
#
#   Genera un CSV con todos los registros de la tabla `votos`.
#   Devuelve el archivo como adjunto descargable.
# =============================================================================
@app.route("/descargar")
def descargar():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, timestamp, sucursal, envio, respuesta, ip, comentario FROM votos")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        import io
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        # Escribir encabezado
        writer.writerow(["id", "timestamp", "sucursal", "envio", "respuesta", "ip", "comentario"])
        for _id, ts, suc, env, resp, ip_, comm in rows:
            # Formatear timestamp si es datetime
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%d/%m/%Y %H:%M:%S")
            else:
                ts_str = str(ts)
            writer.writerow([_id, ts_str, suc, env, resp, ip_, comm or ""])
        csv_buffer.seek(0)

        return Response(
            csv_buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=resultados_votos.csv"}
        )
    except Exception as e:
        logging.error(f"Error al generar CSV: {e}")
        return f"Error al acceder a los datos: {e}", 500



# =============================================================================
# RUTA: GET /dashboard
#
#   1) Lee todos los votos de la tabla.
#   2) Agrupa por “envio” para quedarse con un único voto por envío (el primero que aparezca).
#   3) Calcula:
#      • Top 10 de sucursales con más votos “si”. 
#      • Total de votos por día (solo votos únicos).
#      • Lista de los últimos 100 votos únicos (para tabla).
#   4) Renderiza “dashboard.html” pasándole:
#      - top_positivos   (lista de tuplas: [(sucursal, cantidad), ...])
#      - votos_dia_ordenados (lista: [(fecha, total), ...])
#      - ultimos_votos   (lista: [(envio, timestamp, sucursal, respuesta, comentario), ...])
#      - labels          (lista de fechas como strings, para Chart.js)
#      - data_total      (lista de totales por día, para Chart.js)
# =============================================================================
@app.route("/dashboard")
def dashboard():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT sucursal, respuesta, envio, timestamp, comentario FROM votos")
        votos = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error al leer votos desde BD: {e}")
        return f"No se pudo leer votos: {e}", 500

    # --------------------------------------------------
    # 1) Construir diccionario { envio: (sucursal, respuesta, ts, comentario) }
    # --------------------------------------------------
    votos_unicos = {}
    for sucursal, respuesta, envio, ts, comentario in votos:
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta, ts, comentario)

    # --------------------------------------------------
    # 2) Calcular métricas
    #    - Cuenta “si” por sucursal
    #    - Votos únicos por día
    #    - Últimos 100 votos únicos
    # --------------------------------------------------
    positivos_por_sucursal = Counter()
    votos_por_dia = defaultdict(int)
    ultimos_votos = []

    for envio, (sucursal, respuesta, ts, comentario) in votos_unicos.items():
        if respuesta.strip().lower() == "si":
            positivos_por_sucursal[sucursal] += 1

        fecha = ts.date()
        votos_por_dia[fecha] += 1
        ultimos_votos.append((envio, ts, sucursal, respuesta, comentario))

    # --------------------------------------------------
    # 3) Ordenar los resultados
    # --------------------------------------------------
    votos_dia_ordenados = sorted(votos_por_dia.items())  # [ (fecha, total), ... ]
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]
    top_positivos = sorted(positivos_por_sucursal.items(), key=lambda x: x[1], reverse=True)[:10]

    # --------------------------------------------------
    # 4) Preparar datos para Chart.js: 
    #    labels = ["2025-06-01", "2025-06-02", …]
    #    data_total = [ 5, 12, 8, ... ]  (totales por cada fecha)
    # --------------------------------------------------
    labels = [fecha.strftime("%Y-%m-%d") for fecha, _ in votos_dia_ordenados]
    data_total = [cantidad for _, cantidad in votos_dia_ordenados]

    # --------------------------------------------------
    # 5) Render de la plantilla “dashboard.html”
    # --------------------------------------------------
    return render_template(
        "dashboard.html",
        top_positivos=top_positivos,
        votos_dia=votos_dia_ordenados,
        ultimos_votos=ultimos_votos,
        labels=labels,
        data_total=data_total
    )



# =============================================================================
# Finalmente: levantamos la app en el puerto que Render provea (o 10000 por defecto)
# =============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    # debug=True para mostrar rastreo de error local, 
    # pero en producción en Render se recomienda quitar debug
    app.run(host="0.0.0.0", port=port, debug=True)
