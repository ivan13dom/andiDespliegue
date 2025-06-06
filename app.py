from flask import Flask, request, redirect, render_template, Response
import psycopg2
import os
import csv
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
import logging

app = Flask(__name__)

# ---------------------------------------------------
# Configuración del logger:
# ---------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------
# URL a la base de datos en Render (automáticamente inyectada).
# ---------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")

# ---------------------------------------------------
# Creamos la tabla 'votos' si no existe aún
# ---------------------------------------------------
def crear_tabla_votos():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal TEXT NOT NULL,
            envio TEXT NOT NULL,
            respuesta TEXT NOT NULL,    -- "si" o "no"
            ip TEXT NOT NULL,
            comentario TEXT            -- Comentario libre (opcional)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tabla_votos()


# ---------------------------------------------------
# Ruta principal:
# ---------------------------------------------------
@app.route("/")
def home():
    return "Servidor activo"


# ---------------------------------------------------
# 1) Endpoint que recibe cada click de “voto” vía GET.
#    /voto?sucursal=xxx&respuesta=si|no&envio=YYY
# ---------------------------------------------------
@app.route("/voto")
def voto():
    # 1.1) Extraemos parámetros
    sucursal = request.args.get("sucursal")
    respuesta = request.args.get("respuesta")
    envio = request.args.get("envio")
    ip_full = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip = ip_full.split(",")[0].strip() if ip_full else None

    # Validaciones mínimas
    if not (sucursal and respuesta and envio):
        return "Datos incompletos", 400

    # Logueo de intento
    logging.info(f"[VOTO-INTENTO] sucursal={sucursal} respuesta={respuesta} envio={envio} ip={ip}")

    # Timestamp en zona Argentina
    timestamp = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1.2) Insertamos el voto (comentario vacío)
        cur.execute("""
            INSERT INTO votos (timestamp, sucursal, envio, respuesta, ip, comentario)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (timestamp, sucursal, envio, respuesta, ip, ""))
        conn.commit()
        cur.close()
        conn.close()

        # 1.3) Si respondió “si” → formulario de comentarios; si “no” → gracias_no.html
        if respuesta.strip().lower() == "si":
            return render_template("form_comentario.html", envio=envio, ip=ip)
        else:
            return render_template("gracias_no.html")

    except Exception as e:
        logging.error(f"Error al guardar el voto: {e}")
        return f"Error al guardar el voto: {e}", 500


# ---------------------------------------------------
# 2) Endpoint que procesa el formulario de comentario (POST):
#    /comentario
# ---------------------------------------------------
@app.route("/comentario", methods=["POST"])
def comentario():
    comentario_text = request.form.get("comentario")
    envio = request.form.get("envio")
    ip = request.form.get("ip")

    if not (envio and ip):
        return "Datos incompletos en el comentario", 400

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 2.1) Buscamos el ID del voto más reciente para este envio+IP
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
            # 2.2) Actualizamos solo ese registro con el comentario
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
        logging.error(f"Error al guardar el comentario: {e}")
        return f"Error al guardar el comentario: {e}", 500


# ---------------------------------------------------
# 3) Endpoint para descargar todos los registros como CSV:
#    /descargar
# ---------------------------------------------------
@app.route("/descargar")
def descargar():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, timestamp, sucursal, envio, respuesta, ip, comentario FROM votos ORDER BY timestamp;")
        rows = cur.fetchall()
        conn.close()

        # Creamos CSV en memoria
        output = []
        output.append(['id', 'timestamp', 'sucursal', 'envio', 'respuesta', 'ip', 'comentario'])
        for row in rows:
            ts = row[1]
            # Damos formato dd/mm/YYYY HH:MM:SS
            formatted_ts = ts.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))\
                              .strftime("%d/%m/%Y %H:%M:%S")
            output.append([row[0], formatted_ts, row[2], row[3], row[4], row[5], row[6] or ""])

        import io
        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerows(output)
        csv_output.seek(0)

        return Response(
            csv_output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=resultados.csv"}
        )

    except Exception as e:
        logging.error(f"Error al acceder a los datos: {e}")
        return f"Error al acceder a los datos: {e}", 500


# ---------------------------------------------------
# 4) Endpoint que genera el Dashboard:
#    /dashboard
# ---------------------------------------------------
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
        logging.error(f"Error al leer votos en Dashboard: {e}")
        return f"Error al leer votos en Dashboard: {e}", 500

    # 4.1) Por cada `envio`, quedarnos solamente con el primer (más antiguo) o con el último (más reciente).
    #     Aquí elegimos “el primero que aparezca”—pudieras cambiar a “último” si prefieres:
    votos_unicos = {}
    for sucursal, respuesta, envio, timestamp, comentario in votos:
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta, timestamp, comentario)

    # 4.2) Calculamos métricas:
    #      - cuántos “si” por sucursal
    #      - cuántos “si” / “no” por día
    #      - últimos 100 votos únicos (con comentario)
    positivos_por_sucursal = Counter()       # conteo de “si” por sucursal
    votos_por_dia = defaultdict(lambda: {"si": 0, "no": 0})
    ultimos_votos = []                       # [(envio, timestamp, sucursal, respuesta, comentario), ...]

    for envio, (sucursal, respuesta, timestamp, comentario) in votos_unicos.items():
        r = respuesta.strip().lower()
        # Incrementar conteo “si” por sucursal
        if r == "si":
            positivos_por_sucursal[sucursal] += 1

        # Votos “si/no” por día
        fecha = timestamp.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).date()
        if r == "si":
            votos_por_dia[fecha]["si"] += 1
        else:
            votos_por_dia[fecha]["no"] += 1

        # Tabla de últimos 100 votos únicos
        ultimos_votos.append((envio, timestamp, sucursal, r, comentario))

    # Ordenar las fechas para Chart.js
    fechas_ordenadas = sorted(votos_por_dia.keys())  # [date, date, ...]
    labels = [fecha.strftime("%Y-%m-%d") for fecha in fechas_ordenadas]
    data_si = [votos_por_dia[fecha]["si"] for fecha in fechas_ordenadas]
    data_no = [votos_por_dia[fecha]["no"] for fecha in fechas_ordenadas]

    # Top sucursales ordenado de mayor a menor por cantidad de “si”
    top_positivos = sorted(positivos_por_sucursal.items(), key=lambda x: x[1], reverse=True)

    # Ordenar últimos votos por timestamp descendente y limitarlos a 100
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]

    # Renderizamos plantilla
    return render_template(
        "dashboard.html",
        top_si=top_positivos,      # [("Sucursal A", 12), ("Sucursal B", 8), ...]
        labels=labels,              # ["2025-05-10", "2025-05-11", ...]
        data_si=data_si,            # [5, 7, 2, ...]
        data_no=data_no,            # [3, 1, 4, ...]
        ultimos_votos=ultimos_votos # [(envio, timestamp, sucursal, respuesta, comentario), ...]
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
