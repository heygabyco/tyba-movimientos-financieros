"""Genera cortes de prueba y mide cuánto tarda ejecutar el pipeline."""

import argparse
import sys
import tempfile
import time
from pathlib import Path

try:
    import resource  # Disponible en Linux, incluido el contenedor Docker.
except ImportError:
    resource = None

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pipeline import main


def generar_archivos(raiz, filas):
    """Crea dos archivos Parquet sin guardar los datos en listas de Python."""
    carpeta = raiz / "data" / "raw"
    carpeta.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for corte in ("T", "T1"):
            ajuste = "CASE WHEN i % 100 = 0 THEN 1 ELSE 0 END" if corte == "T1" else "0"
            ruta = (carpeta / f"movimientos_dia_{corte}.parquet").as_posix()
            con.execute(f"""
                COPY (
                    SELECT 'CLI' || CAST(i % 3000 AS VARCHAR) AS id_cliente,
                           '2024-10-01' AS date,
                           'Acciones' AS product,
                           'entrada' AS type,
                           'Renta Fija' AS fund,
                           CAST(100 + i * 0.01 + {ajuste} AS DOUBLE) AS amount,
                           'Compra de activo' AS description,
                           'Banco A' AS commercial_name
                    FROM range({filas}) AS r(i)
                ) TO '{ruta}' (FORMAT PARQUET)
            """)
    finally:
        con.close()


def main_benchmark():
    """Ejecuta una prueba temporal y muestra su duración y tamaño en disco."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=10000,
                        help="Filas por corte (por ejemplo, 5000000)")
    args = parser.parse_args()
    if args.rows <= 0:
        parser.error("--rows debe ser mayor que cero")
    with tempfile.TemporaryDirectory(prefix="tyba_benchmark_") as carpeta:
        raiz = Path(carpeta)
        generar_archivos(raiz, args.rows)
        inicio = time.perf_counter()
        main(raiz)
        duracion = time.perf_counter() - inicio
        db = raiz / "data" / "output" / "movimientos.duckdb"
        print(f"Filas por corte: {args.rows:,}")
        print(f"Tiempo total: {duracion:.2f} segundos")
        print(f"Base creada: {db.stat().st_size / 1024**2:.1f} MiB")
        if resource is not None:
            pico_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            print(f"Memoria máxima aproximada del proceso: {pico_kib / 1024:.1f} MiB")
        print("Datos sintéticos. No extrapolar directamente a producción.")


if __name__ == "__main__":
    main_benchmark()
