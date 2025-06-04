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


@app.route("/comentario", methods=["POST"])
def comentario():
    comentario = request.form.get("comentario")
    envio     = request.form.get("envio")
    ip        = request.form.get("ip")

    if not (envio and ip):
        return "Datos incompletos", 400

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1) Busco el ID del voto más reciente para este envío+IP
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
            # 2) Actualizo ese registro en particular
            cur.execute("""
                UPDATE votos
                SET comentario = %s
                WHERE id = %s
            """, (comentario, voto_id))
            conn.commit()
        # si no existe ningún registro con ese envío/IP, podés decidir devolver un error
        else:
            cur.close()
            conn.close()
            return "No se encontró el voto para actualizar.", 404

        cur.close()
        conn.close()
        return "¡Gracias por tu comentario!"

    except Exception as e:
        return f"Error al guardar el comentario: {e}", 500



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
