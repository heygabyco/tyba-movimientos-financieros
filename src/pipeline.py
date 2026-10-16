"""Punto de entrada: python src/pipeline.py o docker compose up --build."""

import hashlib
import re
from pathlib import Path

import duckdb

from limpieza import limpiar
from validaciones import (
    validar_entrada, validar_limpieza, validar_comparacion, validar_reportes,
)
from comparacion import comparar
from analisis import analizar, exportar_reportes, exportar_insights


PATRON = re.compile(r"movimientos_dia_(T\d*)\.parquet$", re.IGNORECASE)


def _archivos(raiz):
    """Encuentra los cortes T, T1, T2... en su orden, sin confundir sus fechas."""
    encontrados = []
    for archivo in (raiz / "data" / "raw").glob("*.parquet"):
        coincidencia = PATRON.fullmatch(archivo.name)
        if coincidencia:
            numero = int(coincidencia.group(1)[1:] or "0")
            corte = f"T{numero}" if numero else "T"
            encontrados.append((numero, corte, archivo))
    encontrados.sort()
    if len(encontrados) < 2:
        raise ValueError("Se necesitan al menos dos cortes: T y T1")
    for esperado, (numero, _, _) in enumerate(encontrados):
        if numero != esperado:
            raise ValueError("Los cortes deben ser consecutivos: T, T1, T2...")
    return encontrados


def _sha256(archivo):
    """Calcula una huella del archivo para saber si ya se procesó antes."""
    resumen = hashlib.sha256()
    with archivo.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def crear_base(con):
    """Prepara tablas para conservar todos los cortes y sus comparaciones."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS cortes (
            corte VARCHAR PRIMARY KEY, orden INTEGER UNIQUE,
            nombre_archivo VARCHAR, sha256 VARCHAR, filas BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS movimientos (
            corte VARCHAR, fila_id BIGINT,
            id_cliente VARCHAR, date DATE, product VARCHAR, type VARCHAR,
            fund VARCHAR, amount DECIMAL(18,2), description VARCHAR,
            commercial_name VARCHAR,
            PRIMARY KEY (corte, fila_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS historial_cambios (
            corte_anterior VARCHAR, corte_actual VARCHAR,
            fila_anterior BIGINT, fila_actual BIGINT,
            estado VARCHAR, certeza VARCHAR, detalle VARCHAR
        )
    """)


def cargar_corte(con, corte, numero, archivo):
    """Carga una sola vez cada Parquet; si cambia un corte conocido, avisa."""
    huella = _sha256(archivo)
    anterior = con.execute("SELECT sha256 FROM cortes WHERE corte = ?", [corte]).fetchone()
    if anterior:
        if anterior[0] != huella:
            raise ValueError(f"El archivo de {corte} cambió; se necesita revisar el reproceso")
        print(f"{corte}: ya estaba cargado; no se duplican filas")
        return False

    ultimo = con.execute("SELECT MAX(orden) FROM cortes").fetchone()[0]
    if ultimo is not None and numero != ultimo + 1:
        raise ValueError("Los nuevos cortes deben llegar en orden; no se insertan días anteriores")

    # Al pasar al siguiente corte retiramos las vistas temporales anteriores.
    con.execute("DROP VIEW IF EXISTS datos_limpios")
    con.execute("DROP VIEW IF EXISTS datos_raw")
    ruta_sql = archivo.as_posix().replace("'", "''")
    con.execute(f"CREATE TEMP VIEW datos_raw AS SELECT * FROM read_parquet('{ruta_sql}')")
    total = validar_entrada(con)
    limpiar(con)
    validar_limpieza(con, total)
    con.execute("""
        INSERT INTO movimientos
        SELECT ?, ROW_NUMBER() OVER () AS fila_id,
               id_cliente, date, product, type, fund, amount,
               description, commercial_name
        FROM datos_limpios
    """, [corte])
    con.execute("INSERT INTO cortes VALUES (?, ?, ?, ?, ?)",
                [corte, numero, archivo.name, huella, total])
    return True


def preparar_consultas(con):
    """Permite consultar el último corte y ver los cambios de cada par de días."""
    con.execute("""
        CREATE OR REPLACE VIEW movimientos_actuales AS
        SELECT id_cliente, date, product, type, fund, amount,
               description, commercial_name
        FROM movimientos
        WHERE corte = (SELECT corte FROM cortes ORDER BY orden DESC LIMIT 1)
    """)
    con.execute("""
        CREATE OR REPLACE VIEW historial_movimientos AS
        SELECT h.corte_anterior, h.corte_actual, h.estado, h.certeza, h.detalle,
               a.id_cliente AS cliente_anterior, b.id_cliente AS cliente_actual,
               a.date AS fecha_anterior, b.date AS fecha_actual,
               a.product AS producto_anterior, b.product AS producto_actual,
               a.type AS tipo_anterior, b.type AS tipo_actual,
               a.fund AS fondo_anterior, b.fund AS fondo_actual,
               a.amount AS monto_anterior, b.amount AS monto_actual,
               a.description AS descripcion_anterior, b.description AS descripcion_actual,
               a.commercial_name AS comercial_anterior,
               b.commercial_name AS comercial_actual,
               h.fila_anterior, h.fila_actual
        FROM historial_cambios h
        LEFT JOIN movimientos a
          ON a.corte = h.corte_anterior AND a.fila_id = h.fila_anterior
        LEFT JOIN movimientos b
          ON b.corte = h.corte_actual AND b.fila_id = h.fila_actual
    """)


def main(raiz=None):
    """Carga cortes nuevos, compara los consecutivos y genera reportes."""
    raiz = Path(raiz) if raiz is not None else Path(__file__).resolve().parents[1]
    raiz = raiz.resolve()
    salida = raiz / "data" / "output"
    salida.mkdir(parents=True, exist_ok=True)
    archivos = _archivos(raiz)

    con = duckdb.connect(str(salida / "movimientos.duckdb"))
    try:
        con.execute("BEGIN TRANSACTION")
        crear_base(con)
        for numero, corte, archivo in archivos:
            cargar_corte(con, corte, numero, archivo)
            if numero:
                anterior = "T" if numero == 1 else f"T{numero - 1}"
                existe = con.execute("""
                    SELECT COUNT(*) FROM historial_cambios
                    WHERE corte_anterior = ? AND corte_actual = ?
                """, [anterior, corte]).fetchone()[0]
                if not existe:
                    comparar(con, anterior, corte)
                validar_comparacion(con, anterior, corte)

        preparar_consultas(con)
        analizar(con)
        validar_reportes(con)
        con.execute("COMMIT")
        exportar_reportes(con, salida)
        exportar_insights(con, salida)
        print("\nProyecto terminado correctamente")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
