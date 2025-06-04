from flask import Flask, request, redirect, render_template
import psycopg2
import os
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

# --- PARA SEGURIDAD, si quieres usar FLASH messages:
app.secret_key = os.environ.get("SECRET_KEY", "andreani-secret-key")

# --- Definimos la URL de Postgres (Render inyecta DATABASE_URL automáticamente)
DATABASE_URL = os.environ["DATABASE_URL"]

# --- Creamos la tabla votos si no existe
def crear_tabla_votos():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal TEXT NOT NULL,
            envio    TEXT NOT NULL,
            respuesta TEXT NOT NULL,
            ip       TEXT NOT NULL,
            comentario TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tabla_votos()


@app.route("/voto")
def voto():
    """
    Esta ruta recibe los clicks desde el mail:
      /voto?sucursal=MI_SUCURSAL&respuesta=si o no&envio=NUMERO_DE_ENVIO
    
    1) Guarda el voto (sin comentario todavía).
    2) Si la respuesta fue "si", muestra el formulario de comentarios.
       Si fue "no", muestra la página gracias_no.html.
    """
    # 1) Tomamos headers e IP
    user_agent = request.headers.get("User-Agent", "")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip:
        # Si viene un string con comas, solo tomamos la primera
        ip = ip.split(",")[0].strip()

    # 2) Parámetros en la URL
    sucursal = request.args.get("sucursal")
    respuesta = request.args.get("respuesta")
    envio = request.args.get("envio")

    # 3) Verificamos que tengamos lo mínimo
    if not sucursal or not respuesta or not envio:
        return "Datos incompletos en la URL", 400

    # 4) Timestamp en zona Argentina
    timestamp = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    # 5) Insertamos el voto sin comentario (comentario NULL por ahora)
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO votos (timestamp, sucursal, envio, respuesta, ip, comentario)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (timestamp, sucursal, envio, respuesta, ip, None)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return f"Error al guardar el voto: {e}", 500

    # 6) Si el usuario seleccionó "si", lo enviamos al formulario de comentarios
    if respuesta.lower() == "si":
        # Pasamos el envío e IP para que el form pueda incluirlos como hidden
        return render_template("form_comentario.html",
                               envio=envio,
                               ip=ip)
    else:
        # Si respuesta fue "no", mostramos la página de gracias_no.html
        return render_template("gracias_no.html")


@app.route("/comentario", methods=["POST"])
def comentario():
    """
    Aquí procesamos el formulario de comentarios:
    Recibimos (POST):
      comentario, envio, ip
    Hacemos un UPDATE en la fila recién creada.
    Luego mostramos la pantalla "¡Gracias por tu tiempo!".
    """
    comentario = request.form.get("comentario")
    envio = request.form.get("envio")
    ip = request.form.get("ip")

    if not envio or not ip:
        return "Datos incompletos en el comentario", 400

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE votos
               SET comentario = %s
             WHERE envio = %s AND ip = %s
             ORDER BY timestamp DESC
             LIMIT 1
            """,
            (comentario, envio, ip)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return f"Error al guardar el comentario: {e}", 500

    return render_template("gracias_tiempo.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
