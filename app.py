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
DATABASE_URL = os.environ["DATABASE_URL"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# --- Asegúrate de tener la tabla 'votos' creada tal cual:
#    id SERIAL PK, timestamp TIMESTAMPTZ, sucursal TEXT, envio TEXT, respuesta TEXT, ip TEXT, comentario TEXT
#    (lo más sencillo es copiar la función crear_tabla_votos() que ya tenías).

# --------------------------------------------------------------------------------
# Endpoint /dashboard (versión “usar todos los registros” y nuevo armado de datos)
# --------------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    # 1) Recuperar todos los registros
    cur.execute("SELECT id, timestamp, sucursal, envio, respuesta, ip, comentario FROM votos")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # 2) Variables para “Votos por Día”
    votos_por_dia = defaultdict(int)
    # 3) Variables para “Sí por Sucursal”
    conteo_si_por_sucursal = Counter()
    # 4) Contar total de respuestas y total de “Sí” / “No”
    total_responses = len(rows)
    total_si  = sum(1 for r in rows if r[4].strip().lower() == "si")
    total_no  = total_responses - total_si
    percent_si = (total_si / total_responses * 100) if total_responses > 0 else 0

    # 5) Determinar “Sucursal con más Sí”
    #    (si no hay respuestas, dejar cadena vacía)
    if total_si > 0:
        for _id, ts, suc, envio, resp, ip, com in rows:
            if resp.strip().lower() == "si":
                conteo_si_por_sucursal[suc] += 1
        top_branch_si = conteo_si_por_sucursal.most_common(1)[0][0]
    else:
        top_branch_si = ""

    # 6) Llenar votos_por_dia y sí por sucursal
    for _id, ts, suc, envio, resp, ip, com in rows:
        # ts viene como timestamp PostgreSQL (aware), lo normalizamos a zona AR
        fecha_ar = ts.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).date()
        votos_por_dia[fecha_ar] += 1

    # 7) Preparar arrays “labels_dias” / “data_dias” ordenados cronológicamente
    votos_dia_ordenados = sorted(votos_por_dia.items())  # [(fecha, count), ...]
    labels_dias = [fecha.strftime("%Y-%m-%d") for fecha, _count in votos_dia_ordenados]
    data_dias   = [count for _fecha, count in votos_dia_ordenados]

    # 8) Preparar arrays “bar_labels” / “bar_data” para “Sí por Sucursal”
    bar_labels = [sucursal for sucursal, cnt in conteo_si_por_sucursal.most_common()]
    bar_data   = [cnt for _sucursal, cnt in conteo_si_por_sucursal.most_common()]

    # 9) La variable all_records será exactamente “rows”
    all_records = rows

    return render_template(
        "dashboard.html",
        total_responses=total_responses,
        percent_si=percent_si,
        top_branch_si=top_branch_si,
        labels_dias=labels_dias,
        data_dias=data_dias,
        bar_labels=bar_labels,
        bar_data=bar_data,
        total_si=total_si,
        total_no=total_no,
        all_records=all_records
    )


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
