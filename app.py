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

# → Configuración mínima de logging para ver intentos en la consola
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# → URL a la base de datos PostgreSQL (Render inyecta automáticamente DATABASE_URL en tu entorno)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("La variable de entorno DATABASE_URL no está definida")



# ──> Creamos la tabla “votos” si no existe aún <──────────────────────────────────────────────────────
def crear_tabla_votos():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal   TEXT  NOT NULL,
            envio      TEXT  NOT NULL,
            respuesta  TEXT  NOT NULL,
            ip         TEXT  NOT NULL,
            comentario TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# Llamamos a la creación de la tabla al iniciar
crear_tabla_votos()



# ──> Ruta raíz “/” (solo para verificar que el servidor esté vivo) <──────────────────────────────────────────
@app.route("/")
def home():
    return "Servidor activo"



# ──> Endpoint GET “/voto”  
# Recibe los parámetros de_QUERY_:
#   - sucursal
#   - respuesta   (se espera “si” o “no”)
#   - envio      (número de envío)
# → Inserta la fila en la tabla “votos”
# → Si respuesta == “si” → muestra el formulario de comentarios
#   sino → muestra la página de “gracias_no.html”
@app.route("/voto")
def voto():
    # Recuperamos datos de la query string 
    raw_query = request.query_string.decode()
    if ";" in raw_query and "&" not in raw_query:
        # Caso en que venga con punto y coma en lugar de ampersand (adaptación genérica)
        params = parse_qs(raw_query.replace(";", "&"))
        sucursal = params.get("sucursal", [None])[0]
        respuesta = params.get("respuesta", [None])[0]
        envio = params.get("envio", [None])[0]
    else:
        sucursal = request.args.get("sucursal")
        respuesta = request.args.get("respuesta")
        envio = request.args.get("envio")

    # Capturamos IP real (si está detrás de un proxy, X-Forwarded-For)
    ip_full = request.headers.get("X-Forwarded-For", request.remote_addr)
    ip = ip_full.split(",")[0].strip() if ip_full else None

    # Si falta alguno de los tres datos, devolvemos 400
    if not (sucursal and respuesta and envio and ip):
        return "Datos incompletos", 400

    # Timestamp en zona Argentina
    timestamp = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    # Insertamos el voto (comentario vacío por defecto)
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

    # Si el usuario dijo “si” → mostramos formulario para comentario
    if respuesta.strip().lower() == "si":
        return render_template("form_comentario.html", envio=envio, ip=ip)
    else:
        # Si “no” → mostramos la página de agradecimiento sin comentario
        return render_template("gracias_no.html")



# ──> Endpoint POST “/comentario”  
# Recibe los campos:
#   - comentario
#   - envio
#   - ip
# Busca el voto más reciente con ese “envio + ip” y actualiza su columna `comentario`.
# Luego muestra “gracias_tiempo.html”.
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

        # 1) Buscamos el ID del voto más reciente para este envío+IP
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

        # 2) Hacemos update en ese ID concreto
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



# ──> Endpoint GET “/descargar”  
# Genera un CSV con TODO lo que hay en la tabla “votos”.
@app.route("/descargar")
def descargar():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, timestamp, sucursal, envio, respuesta, ip, comentario FROM votos")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Creamos CSV en memoria
        import io
        salida = io.StringIO()
        writer = csv.writer(salida)
        # Encabezado
        writer.writerow(["id", "timestamp", "sucursal", "envio", "respuesta", "ip", "comentario"])
        for fila in rows:
            _id, ts, suc, env, resp, ip_, comm = fila
            # Formatear timestamp estilo “DD/MM/YYYY HH:MM:SS”
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%d/%m/%Y %H:%M:%S")
            else:
                ts_str = str(ts)
            writer.writerow([_id, ts_str, suc, env, resp, ip_, comm or ""])
        salida.seek(0)

        return Response(
            salida.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=resultados_votos.csv"}
        )
    except Exception as e:
        logging.error(f"Error al generar CSV: {e}")
        return f"Error al acceder a los datos: {e}", 500



# ──> (Opcional) Endpoint GET “/dashboard”  
# Este ejemplo, igual que entregamos antes, muestra un Dashboard muy básico:
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

    # Quedarnos con un solo elemento por cada “envio” (el primero que encontremos)
    votos_unicos = {}
    for sucursal, respuesta, envio, ts, comentario in votos:
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta, ts, comentario)

    # 1) Top 10 sucursales con más votos “positivo”
    positivos_por_sucursal = Counter()
    # 2) Votos únicos por día
    votos_por_dia = defaultdict(int)
    # 3) Últimos 100 votos únicos
    ultimos_votos = []

    for envio, (sucursal, respuesta, ts, comentario) in votos_unicos.items():
        if respuesta.strip().lower() == "si":
            positivos_por_sucursal[sucursal] += 1
        fecha = ts.date()
        votos_por_dia[fecha] += 1
        ultimos_votos.append((envio, ts, sucursal, respuesta, comentario))

    # Ordenar voto por día
    votos_dia_ordenados = sorted(votos_por_dia.items())  # [ (fecha, cantidad), ... ]
    # Ordenar últimos 100 votos únicos por timestamp descendente
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]

    # Preparar etiquetas y datos para gráficas (Chart.js)
    labels = [fecha.strftime("%Y-%m-%d") for fecha, _ in votos_dia_ordenados]
    data_si = [votos_por_dia[fecha]["si"] if isinstance(votos_por_dia[fecha], dict) else 0 for fecha, votos_por_dia in votos_dia_ordenados]
    # (**Nota**: si quieres desglosar “no” en otra serie, agrégalo a data_no. En este ejemplo solo gráfico totales.)

    top_positivos = sorted(positivos_por_sucursal.items(), key=lambda x: x[1], reverse=True)[:10]

    return render_template(
        "dashboard.html",
        top_positivos=top_positivos,
        votos_dia=votos_dia_ordenados,
        ultimos_votos=ultimos_votos,
        labels=labels,
        data_si=[votos_por_dia[fecha]["si"] for fecha, votos_por_dia in votos_dia_ordenados]
    )



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
