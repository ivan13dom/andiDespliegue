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
