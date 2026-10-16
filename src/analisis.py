"""Reportes que se pueden consultar en DuckDB y exportar a CSV."""

from pathlib import Path


def analizar(con):
    """Calcula totales, calidad, flujo y efecto de las posibles correcciones."""
    for tabla, campo in (
        ("resumen_tipos", "type"),
        ("resumen_fondos", "fund"),
        ("resumen_productos", "product"),
        ("resumen_fechas", "date"),
    ):
        con.execute(f"""
            CREATE OR REPLACE TABLE {tabla} AS
            SELECT corte, {campo}, COUNT(*) AS movimientos,
                   COUNT(*) FILTER (WHERE amount IS NULL) AS montos_vacios,
                   SUM(amount) AS monto_total
            FROM movimientos
            GROUP BY corte, {campo}
            ORDER BY corte, {campo}
        """)

    con.execute("""
        CREATE OR REPLACE TABLE resumen_flujo AS
        SELECT corte,
               SUM(amount) FILTER (WHERE type = 'entrada') AS entradas_firmadas,
               SUM(amount) FILTER (WHERE type = 'salida') AS salidas_firmadas,
               SUM(CASE WHEN type = 'entrada' THEN amount ELSE -amount END) AS flujo_neto_reportado,
               COUNT(*) FILTER (WHERE type = 'entrada' AND amount < 0) AS entradas_negativas,
               SUM(amount) FILTER (WHERE type = 'entrada' AND amount < 0) AS total_entradas_negativas,
               SUM(CASE WHEN amount >= 0
                    THEN CASE WHEN type = 'entrada' THEN amount ELSE -amount END
                    ELSE 0 END) AS flujo_excluyendo_negativos
        FROM movimientos
        GROUP BY corte ORDER BY corte
    """)
    con.execute("""
        CREATE OR REPLACE TABLE resumen_comparacion AS
        SELECT corte_anterior, corte_actual, estado, certeza, detalle, COUNT(*) AS cantidad
        FROM historial_cambios
        GROUP BY ALL ORDER BY corte_anterior, corte_actual, estado, detalle
    """)
    con.execute("""
        CREATE OR REPLACE TABLE resumen_impacto_montos AS
        SELECT h.corte_anterior, h.corte_actual,
               COUNT(*) AS cantidad,
               COUNT(*) FILTER (WHERE a.amount IS NULL) AS antes_vacios,
               COUNT(*) FILTER (WHERE b.amount IS NULL) AS ahora_vacios,
               SUM(b.amount - a.amount) AS variacion_montos_conocidos
        FROM historial_cambios h
        JOIN movimientos a ON a.corte = h.corte_anterior AND a.fila_id = h.fila_anterior
        JOIN movimientos b ON b.corte = h.corte_actual AND b.fila_id = h.fila_actual
        WHERE h.estado = 'corregido' AND h.detalle = 'monto'
        GROUP BY h.corte_anterior, h.corte_actual
    """)
    con.execute("""
        CREATE OR REPLACE TABLE resumen_calidad AS
        WITH marcadas AS (
            SELECT *, COUNT(*) OVER (
                PARTITION BY corte, id_cliente, date, product, type, fund,
                             description, commercial_name
            ) AS mismas_siete_columnas
            FROM movimientos
        )
        SELECT corte, COUNT(*) AS movimientos,
               COUNT(*) FILTER (WHERE amount IS NULL) AS montos_vacios,
               COUNT(*) FILTER (WHERE amount < 0) AS montos_negativos,
               COUNT(*) FILTER (WHERE amount = 0) AS montos_cero,
               COUNT(*) FILTER (WHERE type = 'entrada' AND amount < 0) AS entradas_negativas,
               COUNT(*) FILTER (WHERE description IS NULL) AS descripciones_vacias,
               COUNT(*) FILTER (WHERE commercial_name IS NULL) AS nombres_comerciales_vacios,
               COUNT(*) FILTER (WHERE mismas_siete_columnas > 1) AS filas_con_siete_campos_repetidos
        FROM marcadas GROUP BY corte ORDER BY corte
    """)


def exportar_reportes(con, carpeta: Path):
    """Escribe los CSV y reemplaza resultados anteriores sin generar duplicados."""
    tablas = (
        "resumen_tipos", "resumen_fondos", "resumen_productos",
        "resumen_fechas", "resumen_flujo", "resumen_comparacion",
        "resumen_impacto_montos", "resumen_calidad",
    )
    for tabla in tablas:
        ruta = carpeta / f"{tabla}.csv"
        temporal = carpeta / f"{tabla}.tmp.csv"
        temporal.unlink(missing_ok=True)
        sql_ruta = temporal.as_posix().replace("'", "''")
        con.execute(f"COPY (SELECT * FROM {tabla} ORDER BY ALL) TO '{sql_ruta}' (HEADER, DELIMITER ',')")
        temporal.replace(ruta)
    print(f"Se generaron {len(tablas)} reportes CSV")


def exportar_insights(con, carpeta: Path):
    """Escribe cinco hallazgos con cifras calculadas de la base procesada."""
    cortes = [r[0] for r in con.execute(
        "SELECT corte FROM cortes ORDER BY orden").fetchall()]
    primero, ultimo = cortes[0], cortes[-1]
    contar = lambda c: con.execute(
        "SELECT COUNT(*) FROM movimientos WHERE corte = ?", [c]).fetchone()[0]
    n_inicio, n_fin = contar(primero), contar(ultimo)
    diferencia = n_fin - n_inicio
    porcentaje = diferencia / n_inicio * 100

    entradas = con.execute("""
        SELECT corte, movimientos FROM resumen_tipos WHERE type = 'entrada'
    """).fetchall()
    entradas_por_corte = dict(entradas)

    estado = con.execute("""
        SELECT estado, detalle, cantidad FROM resumen_comparacion
        WHERE corte_anterior = ? AND corte_actual = ?
    """, [primero, cortes[1]]).fetchall()
    iguales = sum(n for e, _, n in estado if e == 'sin_cambios')
    cambios = sum(n for e, _, n in estado if e == 'corregido')
    sin_pareja_a = sum(n for e, _, n in estado if e == 'eliminado')
    sin_pareja_b = sum(n for e, _, n in estado if e == 'nuevo')
    detalles = ", ".join(f"{detalle}: {n:,}" for e, detalle, n in estado if e == 'corregido') or 'ninguno'

    fondo, movimientos_fondo = con.execute("""
        SELECT fund, movimientos FROM resumen_fondos
        WHERE corte = ? ORDER BY movimientos DESC, fund LIMIT 1
    """, [ultimo]).fetchone()

    calidad = con.execute("""
        SELECT montos_negativos, entradas_negativas, montos_vacios
        FROM resumen_calidad WHERE corte = ?
    """, [primero]).fetchone()
    negativos, entradas_negativas, vacios = calidad
    negativos_ultimo, entradas_negativas_ultimo = con.execute("""
        SELECT montos_negativos, entradas_negativas
        FROM resumen_calidad WHERE corte = ?
    """, [ultimo]).fetchone()

    flujo = con.execute("""
        SELECT flujo_neto_reportado, flujo_excluyendo_negativos
        FROM resumen_flujo WHERE corte = ?
    """, [primero]).fetchone()
    impacto = con.execute("""
        SELECT COALESCE(SUM(cantidad), 0),
               COALESCE(SUM(antes_vacios), 0),
               COALESCE(SUM(ahora_vacios), 0),
               SUM(variacion_montos_conocidos)
        FROM resumen_impacto_montos
        WHERE corte_anterior = ? AND corte_actual = ?
    """, [primero, cortes[1]]).fetchone()

    def dinero(n):
        return 'sin datos' if n is None else f"{n:,.2f}"

    md = f"""# Insights de movimientos financieros

Estos hallazgos se calculan automáticamente a partir de los datos limpios y los reportes del pipeline.
Los cortes son **fotografías del estado recibido**, no necesariamente movimientos ocurridos ese día.

## 1. Cantidad de movimientos

El corte {primero} tiene **{n_inicio:,} filas** y el corte {ultimo} tiene **{n_fin:,}**.
La diferencia es de **{diferencia:+,} filas ({porcentaje:+.2f} %)**. No equivale por sí sola a cancelaciones reales.

## 2. Comparación entre los dos primeros cortes

Hay **{iguales:,} coincidencias exactas** y **{cambios:,} posibles correcciones** ({detalles}).
Quedaron **{sin_pareja_a:,} filas del primer corte** y **{sin_pareja_b:,} del segundo** sin pareja.
Una fila sin pareja puede ser nueva, eliminada o una corrección de varios campos; no se puede confirmar su identidad sin un ID de transacción.

## 3. Entradas y fondos

Los movimientos de entrada suman **{entradas_por_corte.get(primero, 0):,} filas** en {primero} y **{entradas_por_corte.get(ultimo, 0):,}** en {ultimo}.
En el último corte, el fondo con más filas es **{fondo}**, con **{movimientos_fondo:,} movimientos**.

## 4. Entradas con monto negativo

En {primero} hay **{negativos:,} montos negativos**, de los cuales **{entradas_negativas:,}** aparecen como `entrada`.
En {ultimo} hay **{negativos_ultimo:,} negativos**, de los cuales **{entradas_negativas_ultimo:,}** son `entrada`.
Esta coincidencia merece revisión: puede indicar una convención para reversos o ajustes, pero el archivo no permite confirmarlo.

## 5. Cómo afecta al flujo calculado

Para {primero}, la suma de entradas menos salidas (manteniendo el signo original) es **{dinero(flujo[0])}**.
Excluyendo únicamente los importes negativos del cálculo, es **{dinero(flujo[1])}**.
La diferencia entre ambas sumas es **{dinero(flujo[1] - flujo[0]) if None not in flujo else 'sin datos'}**.
Esta segunda cifra es una comparación para entender el impacto, **no un saldo alternativo validado**.
Además, hay **{vacios:,} montos vacíos** en el primer corte que SQL excluye de las sumas.

## 6. Efecto de las posibles correcciones de monto

Entre {primero} y {cortes[1]} se encontraron **{impacto[0]:,} parejas** clasificadas como posible corrección de monto.
En **{impacto[1]:,}** el monto anterior está vacío y en **{impacto[2]:,}** el monto actual está vacío.
En las parejas donde ambos valores son conocidos, la suma de (monto actual menos monto anterior) es **{dinero(impacto[3])}**.
Esta cifra describe solo las parejas propuestas por nuestra comparación. No prueba cambios monetarios reales de una transacción sin un identificador único.

## Nota sobre la interpretación

No se ha confirmado el significado contable de los signos, la moneda ni la existencia de un ID único por transacción.
Por eso, los resultados se presentan como diferencias y sumas de los cortes, no como pérdidas, ganancias ni saldos definitivos.
"""
    (carpeta / 'insights.md').write_text(md, encoding='utf-8')
    print("Se generó insights.md con los hallazgos calculados")
