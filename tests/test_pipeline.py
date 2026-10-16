"""Pruebas de la limpieza, comparación, reportes y ejecución diaria."""

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analisis import analizar
from comparacion import comparar
from limpieza import limpiar
from pipeline import crear_base, main
from validaciones import (
    validar_entrada, validar_limpieza, validar_comparacion, validar_reportes,
)

COLUMNAS = """
    id_cliente VARCHAR, date VARCHAR, product VARCHAR, type VARCHAR,
    fund VARCHAR, amount DOUBLE, description VARCHAR, commercial_name VARCHAR
"""


def fila(cliente, monto, descripcion="compra", tipo="entrada",
         fecha="2024-10-01", fondo="Renta Fija"):
    """Crea un movimiento de ejemplo sin depender de los datos de Tyba."""
    return cliente, fecha, "Acciones", tipo, fondo, monto, descripcion, "Banco A"


def preparar(t, t1):
    """Carga dos cortes pequeños en una base temporal."""
    con = duckdb.connect(":memory:")
    crear_base(con)
    for numero, corte, filas in ((0, "T", t), (1, "T1", t1)):
        con.execute("DROP VIEW IF EXISTS datos_limpios")
        con.execute("DROP TABLE IF EXISTS datos_raw")
        con.execute(f"CREATE TEMP TABLE datos_raw ({COLUMNAS})")
        if filas:
            con.executemany("INSERT INTO datos_raw VALUES (?, ?, ?, ?, ?, ?, ?, ?)", filas)
        validar_entrada(con)
        limpiar(con)
        validar_limpieza(con, len(filas))
        con.execute("""
            INSERT INTO movimientos
            SELECT ?, ROW_NUMBER() OVER (), id_cliente, date, product, type,
                   fund, amount, description, commercial_name FROM datos_limpios
        """, [corte])
        con.execute("INSERT INTO cortes VALUES (?, ?, ?, ?, ?)",
                    [corte, numero, f"archivo_{corte}", "test", len(filas)])
    comparar(con, "T", "T1")
    validar_comparacion(con, "T", "T1")
    return con


def contar(con, estado, detalle=None):
    """Cuenta un tipo de cambio del historial."""
    if detalle:
        return con.execute(
            "SELECT COUNT(*) FROM historial_cambios WHERE estado=? AND detalle=?",
            [estado, detalle]
        ).fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM historial_cambios WHERE estado=?", [estado]
    ).fetchone()[0]


def escribir_parquet(carpeta, corte, filas):
    """Crea un archivo Parquet pequeño para las pruebas completas."""
    raw = carpeta / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    ruta = raw / f"movimientos_dia_{corte}.parquet"
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"CREATE TABLE datos ({COLUMNAS})")
        if filas:
            con.executemany("INSERT INTO datos VALUES (?, ?, ?, ?, ?, ?, ?, ?)", filas)
        con.execute(f"COPY datos TO '{ruta.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    return ruta


def test_identifica_los_cuatro_estados_sin_perder_filas():
    """Reconoce coincidencias y no confirma cambios sin un ID de transacción."""
    con = preparar(
        [fila("A", 10), fila("B", 100), fila("C", 300, "antes"), fila("D", 400)],
        [fila("A", 10), fila("B", 150), fila("C", 300, "despues"), fila("E", 500)],
    )
    try:
        assert contar(con, "sin_cambios") == 1
        assert contar(con, "corregido", "monto") == 1
        assert contar(con, "corregido", "descripcion") == 1
        assert contar(con, "eliminado") == 1
        assert contar(con, "nuevo") == 1
        assert con.execute("SELECT COUNT(*) FROM historial_cambios WHERE certeza='posible'").fetchone()[0] == 4
    finally:
        con.close()


def test_movimientos_iguales_no_se_pierden():
    """Dos filas idénticas del primer corte no se convierten en una sola."""
    dato = fila("A", 100)
    con = preparar([dato, dato], [dato])
    try:
        assert contar(con, "sin_cambios") == 1
        assert contar(con, "eliminado") == 1
    finally:
        con.close()


def test_fechas_formatos_y_fondos_terminan_iguales():
    """Normaliza fechas DD/MM/YYYY, YYYY-MM-DD y espacios/mayúsculas del fondo."""
    con = preparar(
        [fila("A", 100, fecha="01/10/2024", fondo="  RENTA  FIJA ")],
        [fila("A", 100, fecha="2024-10-01", fondo="renta fija")],
    )
    try:
        assert contar(con, "sin_cambios") == 1
        fecha, fondo = con.execute("""
            SELECT date, fund FROM movimientos WHERE corte='T'
        """).fetchone()
        assert str(fecha) == "2024-10-01"
        assert fondo == "renta fija"
    finally:
        con.close()


def test_null_y_tipo_con_variantes():
    """El importe NULL se conserva y 'IN' se interpreta como entrada."""
    con = preparar([fila("B", None, tipo="IN")], [fila("B", 50, tipo="entrada")])
    try:
        assert contar(con, "corregido", "monto") == 1
        assert con.execute("SELECT COUNT(*) FROM movimientos WHERE amount IS NULL").fetchone()[0] == 1
    finally:
        con.close()


def test_tipo_desconocido_detiene_el_proceso():
    """Un valor de tipo que no conocemos no se cambia sin avisar."""
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"CREATE TEMP TABLE datos_raw ({COLUMNAS})")
        con.execute("INSERT INTO datos_raw VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    fila("A", 20, tipo="otro"))
        limpiar(con)
        with pytest.raises(ValueError, match="Corte inválido"):
            validar_limpieza(con, 1)
    finally:
        con.close()


def test_no_reutiliza_una_fila_entre_dos_parejas():
    """Dos movimientos viejos no pueden emparejarse con uno solo nuevo."""
    con = preparar([fila("A", 100), fila("A", 200)], [fila("A", 150)])
    try:
        assert contar(con, "corregido") == 0
        assert contar(con, "eliminado") == 2
        assert contar(con, "nuevo") == 1
    finally:
        con.close()


def test_validar_reportes_detecta_un_total_mal():
    """La validación falla al alterar deliberadamente un reporte de producto."""
    con = preparar([fila("A", 100)], [fila("A", 150)])
    try:
        analizar(con)
        validar_reportes(con)
        con.execute("UPDATE resumen_productos SET monto_total=monto_total+1 WHERE corte='T'")
        with pytest.raises(ValueError, match="resumen_productos"):
            validar_reportes(con)
    finally:
        con.close()


def test_validar_reportes_detecta_un_flujo_mal():
    """La validación también comprueba el flujo, no solo los fondos."""
    con = preparar([fila("A", -50), fila("B", 100, tipo="salida")],
                   [fila("A", -50), fila("B", 100, tipo="salida")])
    try:
        analizar(con)
        validar_reportes(con)
        con.execute("UPDATE resumen_flujo SET flujo_neto_reportado=0 WHERE corte='T'")
        with pytest.raises(ValueError, match="flujo"):
            validar_reportes(con)
    finally:
        con.close()


def test_main_desde_carpeta_limpia_y_repetido(tmp_path):
    """Ejecutar dos veces los mismos Parquet no duplica registros ni reportes."""
    escribir_parquet(tmp_path, "T", [fila("A", 10), fila("B", 100)])
    escribir_parquet(tmp_path, "T1", [fila("A", 10), fila("B", 120), fila("C", 30)])
    main(tmp_path)
    salida = tmp_path / "data" / "output"
    esperado = (
        "resumen_tipos.csv", "resumen_fondos.csv", "resumen_productos.csv",
        "resumen_fechas.csv", "resumen_flujo.csv", "resumen_comparacion.csv",
        "resumen_impacto_montos.csv", "resumen_calidad.csv",
    )
    for archivo in esperado:
        assert (salida / archivo).is_file()
    assert (salida / "insights.md").is_file()
    con = duckdb.connect(str(salida / "movimientos.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM cortes").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM historial_cambios").fetchone()[0] == 3
    finally:
        con.close()
    reportes_antes = {nombre: (salida / nombre).read_bytes() for nombre in esperado + ("insights.md",)}
    main(tmp_path)
    con = duckdb.connect(str(salida / "movimientos.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM historial_cambios").fetchone()[0] == 3
    finally:
        con.close()
    assert reportes_antes == {nombre: (salida / nombre).read_bytes() for nombre in esperado + ("insights.md",)}


def test_acepta_tercer_corte_sin_editar_codigo(tmp_path):
    """Un T2 nuevo se compara con T1 y se conserva todo el historial anterior."""
    escribir_parquet(tmp_path, "T", [fila("A", 10)])
    escribir_parquet(tmp_path, "T1", [fila("A", 10), fila("B", 20)])
    main(tmp_path)
    escribir_parquet(tmp_path, "T2", [fila("A", 10), fila("B", 25)])
    main(tmp_path)
    con = duckdb.connect(str(tmp_path / "data" / "output" / "movimientos.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM cortes").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM historial_cambios").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM movimientos_actuales").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM historial_cambios WHERE corte_actual='T2' AND detalle='monto'").fetchone()[0] == 1
    finally:
        con.close()


def test_no_modifica_silenciosamente_un_corte_ya_cargado(tmp_path):
    """Si un Parquet conocido cambia, se detiene para revisar el reproceso."""
    escribir_parquet(tmp_path, "T", [fila("A", 10)])
    escribir_parquet(tmp_path, "T1", [fila("A", 10)])
    main(tmp_path)
    ruta = tmp_path / "data" / "raw" / "movimientos_dia_T1.parquet"
    ruta.unlink()
    escribir_parquet(tmp_path, "T1", [fila("A", 20)])
    with pytest.raises(ValueError, match="archivo de T1 cambió"):
        main(tmp_path)


def test_fecha_invalida_no_se_carga():
    """Una fecha que no se pueda leer impide procesar el corte."""
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"CREATE TEMP TABLE datos_raw ({COLUMNAS})")
        con.execute("INSERT INTO datos_raw VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    fila("A", 20, fecha="no es una fecha"))
        limpiar(con)
        with pytest.raises(ValueError, match="Corte inválido"):
            validar_limpieza(con, 1)
    finally:
        con.close()


def test_reporta_negativos_y_repeticiones_sin_borrarlos():
    """Conserva una entrada negativa y dos operaciones con siete campos iguales."""
    con = preparar([fila("A", -10)], [fila("A", 10), fila("A", 20)])
    try:
        analizar(con)
        calidad = con.execute("""
            SELECT montos_negativos, entradas_negativas
            FROM resumen_calidad WHERE corte='T'
        """).fetchone()
        assert calidad == (1, 1)
        repetidas = con.execute("""
            SELECT filas_con_siete_campos_repetidos FROM resumen_calidad
            WHERE corte='T1'
        """).fetchone()[0]
        assert repetidas == 2
        assert con.execute("SELECT COUNT(*) FROM movimientos WHERE corte='T1'").fetchone()[0] == 2
    finally:
        con.close()


def test_detecta_correccion_de_fondo_y_nombre_comercial():
    """Prueba otros campos sin confundir dos filas del mismo cliente."""
    previo_fondo = fila("A", 100, fondo="Renta Fija")
    nuevo_fondo = fila("A", 100, fondo="Balanceado")
    previo_comercial = list(fila("B", 200))
    nuevo_comercial = previo_comercial.copy()
    nuevo_comercial[7] = "Banco B"
    con = preparar([previo_fondo, tuple(previo_comercial)],
                   [nuevo_fondo, tuple(nuevo_comercial)])
    try:
        assert contar(con, "corregido", "fondo") == 1
        assert contar(con, "corregido", "comercial") == 1
        assert contar(con, "nuevo") == 0
        assert contar(con, "eliminado") == 0
    finally:
        con.close()


def test_faltando_un_corte_intermedio_no_continua(tmp_path):
    """No compara T con T2 cuando no se ha recibido T1."""
    escribir_parquet(tmp_path, "T", [fila("A", 10)])
    escribir_parquet(tmp_path, "T2", [fila("A", 20)])
    with pytest.raises(ValueError, match="consecutivos"):
        main(tmp_path)


def test_archivo_vacio_no_borra_movimientos(tmp_path):
    """Un corte vacío se rechaza antes de tratarlo como bajas masivas."""
    escribir_parquet(tmp_path, "T", [fila("A", 10)])
    escribir_parquet(tmp_path, "T1", [])
    with pytest.raises(ValueError, match="corte está vacío"):
        main(tmp_path)
    # No se conserva una transacción parcial en la base.
    con = duckdb.connect(str(tmp_path / "data" / "output" / "movimientos.duckdb"))
    try:
        existe = con.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name = 'cortes'
        """).fetchone()[0]
        if existe:
            assert con.execute("SELECT COUNT(*) FROM cortes").fetchone()[0] == 0
    finally:
        con.close()


def test_validar_reportes_detecta_flujo_sin_negativos_incorrecto():
    """Un error en la comparación de flujo también debe detenerse."""
    con = preparar([fila("A", -40), fila("B", 100, tipo="salida")],
                   [fila("A", -40), fila("B", 100, tipo="salida")])
    try:
        analizar(con)
        validar_reportes(con)
        con.execute("""
            UPDATE resumen_flujo SET flujo_excluyendo_negativos = 999
            WHERE corte='T'
        """)
        with pytest.raises(ValueError, match="sin negativos"):
            validar_reportes(con)
    finally:
        con.close()
