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

# Configuración del logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# URL de la base de datos inyectada en Render
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Falta definir la variable de entorno DATABASE_URL")

def crear_tablas():
    """ Crea las tablas 'votos' e 'intentos' si no existen. """
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal   TEXT NOT NULL,
            respuesta  TEXT NOT NULL,  -- suponemos 'si' o 'no'
            envio      TEXT NOT NULL,
            ip         TEXT NOT NULL,
            comentario TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS intentos (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            sucursal   TEXT,
            respuesta  TEXT,
            envio      TEXT,
            ip         TEXT,
            motivo     TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tablas()

@app.route("/")
def home():
    return "Servidor activo"

# --------------------------------------------
#   Aquí irían tus endpoints /voto, /gracias, /comentario, /descargar, etc.
#   Asumimos que ya funcionan correctamente.
# --------------------------------------------

@app.route("/dashboard")
def dashboard():
    """
    Levanta todos los registros de 'votos', toma solo un voto único por envío,
    luego calcula para cada día cuántos "si" y cuántos "no",
    arma los arrays data_si y data_no, y lo pasa a la plantilla.
    """
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    # Levantamos todos los votos de la tabla
    cur.execute("SELECT sucursal, respuesta, envio, timestamp, comentario FROM votos")
    filas = cur.fetchall()
    cur.close()
    conn.close()

    # Agrupar por 'envio' para quedarnos con el primer voto (o el más viejo/único)
    votos_unicos = {}
    for sucursal, respuesta, envio, timestamp, comentario in filas:
        if envio not in votos_unicos:
            votos_unicos[envio] = (sucursal, respuesta.lower().strip(), timestamp, comentario)

    # Contadores:
    positivos_por_sucursal = Counter()
    votos_por_dia_si = defaultdict(int)
    votos_por_dia_no = defaultdict(int)
    ultimos_votos = []  # [(envio, timestamp, sucursal, respuesta, comentario), ...]

    for envio, (sucursal, respuesta, timestamp, comentario) in votos_unicos.items():
        fecha = timestamp.date()

        # Marcar voto positivo si respuesta == "si"
        if respuesta == "si":
            positivos_por_sucursal[sucursal] += 1
            votos_por_dia_si[fecha] += 1
        else:
            # asumimos que cualquier respuesta distinta de 'si' cuenta como 'no'
            votos_por_dia_no[fecha] += 1

        # Guardamos en la lista de últimos 100 (luego ordenaremos por timestamp)
        ultimos_votos.append((envio, timestamp, sucursal, respuesta, comentario or ""))

    # Ordenar votos por día cronológicamente
    todas_fechas = set(votos_por_dia_si.keys()) | set(votos_por_dia_no.keys())
    fechas_ordenadas = sorted(todas_fechas)

    # Generar series paralelas de 'si' y 'no' para cada fecha
    labels = [fecha.strftime("%Y-%m-%d") for fecha in fechas_ordenadas]
    data_si = [votos_por_dia_si[fecha] for fecha in fechas_ordenadas]
    data_no = [votos_por_dia_no[fecha] for fecha in fechas_ordenadas]

    # Ordenar los últimos votos únicos de mayor a menor por timestamp
    ultimos_votos = sorted(ultimos_votos, key=lambda x: x[1], reverse=True)[:100]

    # Top de sucursales por votos positivos
    top_positivos = sorted(positivos_por_sucursal.items(), key=lambda x: x[1], reverse=True)

    return render_template(
        "dashboard.html",
        top_positivos=top_positivos,
        ultimos_votos=ultimos_votos,
        labels=labels,
        data_si=data_si,
        data_no=data_no
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
