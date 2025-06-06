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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- Asegúrate de tener la tabla 'votos' creada tal cual:
#    id SERIAL PK, timestamp TIMESTAMPTZ, sucursal TEXT, envio TEXT, respuesta TEXT, ip TEXT, comentario TEXT
#    (lo más sencillo es copiar la función crear_tabla_votos() que ya tenías).

# --------------------------------------------------------------------------------
# Endpoint /dashboard (versión “usar todos los registros” y nuevo armado de datos)
# --------------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # Recuperamos todos los votos (sin filtrar duplicados)
        cur.execute("SELECT sucursal, respuesta, envio, timestamp, comentario FROM votos ORDER BY timestamp;")
        filas = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error al leer votos en Dashboard: {e}")
        return f"Error al leer votos en Dashboard: {e}", 500

    # ----------------------------------------------------------------
    # 1) Calcular Totales "Sí" vs "No" (para el gráfico de dona)
    # ----------------------------------------------------------------
    total_si = 0
    total_no = 0
    for suc, resp, envio, ts, com in filas:
        if resp.strip().lower() == "si":
            total_si += 1
        else:
            total_no += 1

    # ----------------------------------------------------------------
    # 2) Calcular VOTOS POR DÍA (solo total diario, sin distinguir sí/no)
    # ----------------------------------------------------------------
    votos_por_dia = defaultdict(int)
    for suc, resp, envio, ts, com in filas:
        fecha = ts.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).date()
        votos_por_dia[fecha] += 1

    fechas_ordenadas = sorted(votos_por_dia.keys())
    labels_dias = [f.strftime("%Y-%m-%d") for f in fechas_ordenadas]
    data_dias = [votos_por_dia[f] for f in fechas_ordenadas]

    # ----------------------------------------------------------------
    # 3) Top de sucursales por # de "SI"
    # ----------------------------------------------------------------
    si_por_sucursal = Counter()
    for suc, resp, envio, ts, com in filas:
        if resp.strip().lower() == "si":
            si_por_sucursal[suc] += 1

    top_si = sorted(si_por_sucursal.items(), key=lambda x: x[1], reverse=True)

    # ----------------------------------------------------------------
    # 4) Últimos 100 votos (sin filtrar duplicados), en orden descendente
    # ----------------------------------------------------------------
    ultimos_100 = sorted(
        [(envio, ts, suc, resp.strip().lower(), com) for suc, resp, envio, ts, com in filas],
        key=lambda x: x[1],
        reverse=True
    )[:100]

    # ----------------------------------------------------------------
    # Finalmente devolvemos al template todos estos datos:
    # ----------------------------------------------------------------
    return render_template("dashboard.html",
                           total_si=total_si,
                           total_no=total_no,
                           labels_dias=labels_dias,
                           data_dias=data_dias,
                           top_si=top_si,
                           ultimos_100=ultimos_100)


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
