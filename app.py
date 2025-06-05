# app.py

from flask import Flask, request, redirect, render_template
import psycopg2
import os
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

# --- Para mensajes flash si lo necesitás más adelante
app.secret_key = os.environ.get("SECRET_KEY", "andreani-secret-key")

# --- URL de conexión a Postgres (definida en las variables de entorno de Render)
DATABASE_URL = os.environ["DATABASE_URL"]

@app.route("/dashboard")
def dashboard():
    """
    Genera métricas sobre cuántos clientes
    respondieron “Sí” o “No” a la pregunta de ANDI.
    """

    # 1) Levanto todos los votos
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT sucursal, respuesta, envio, timestamp, comentario FROM votos")
    filas = cur.fetchall()
    cur.close()
    conn.close()

    # 2) Primero, elijo un solo registro por envío
    votos_unicos = {}
    # cada 'envio' queda mapeado a (sucursal, respuesta, timestamp, comentario)
    for sucursal, respuesta, envio, ts, comentario in filas:
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta.lower(), ts, comentario)

    # 3) Calcular métricas por sucursal
    conteo_si = Counter()       # cuenta “sí” por sucursal
    conteo_total = Counter()    # cuenta total (“sí” + “no”) por sucursal
    votos_por_dia = defaultdict(lambda: {"si": 0, "no": 0})

    # También acumulamos últimos 100 votos
    ultimos_votos = []

    for envio, (sucursal, respuesta, ts, comentario) in votos_unicos.items():
        conteo_total[sucursal] += 1
        if respuesta == "si":
            conteo_si[sucursal] += 1

        # Para la serie temporal: contamos separadamente “si” y “no” por fecha
        fecha = ts.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).date()
        if respuesta == "si":
            votos_por_dia[fecha]["si"] += 1
        else:
            votos_por_dia[fecha]["no"] += 1

        # Recojo para la tabla de últimos 100: (envio, fecha, sucursal, respuesta, comentario)
        ultimos_votos.append((envio, ts.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")),
                              sucursal, respuesta, comentario))

    # 4) Top 10 sucursales con más “sí”
    top_si = sorted(conteo_si.items(), key=lambda x: x[1], reverse=True)[:10]

    # 5) Top 5 sucursales con mayor % de “sí” vs total
    porcentajes = []
    for suc, total in conteo_total.items():
        si = conteo_si.get(suc, 0)
        pct = (si / total) * 100 if total > 0 else 0
        porcentajes.append((suc, round(pct, 1), si, total))
    # Ordeno por % descendente y tomo los 5
    top_pct = sorted(porcentajes, key=lambda x: x[1], reverse=True)[:5]
    # Cada tupla queda: (sucursal, porcentaje, cant_si, cant_total)

    # 6) Datos para el gráfico de “Sí vs No” por día
    dias_ordenados = sorted(votos_por_dia.items(), key=lambda x: x[0])
    labels = [fecha.strftime("%d/%m") for fecha, _ in dias_ordenados]
    data_si = [counts["si"] for _, counts in dias_ordenados]
    data_no = [counts["no"] for _, counts in dias_ordenados]

    # 7) Ordeno últimos 100 por timestamp descendente y limito
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]

    return render_template(
        "dashboard.html",
        top_si=top_si,
        top_pct=top_pct,
        labels=labels,
        data_si=data_si,
        data_no=data_no,
        ultimos_votos=ultimos_votos
    )

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
