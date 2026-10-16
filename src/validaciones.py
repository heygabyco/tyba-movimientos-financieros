"""Comprobaciones de calidad y de los resultados del pipeline."""

COLUMNAS = {
    "id_cliente", "date", "product", "type", "fund", "amount",
    "description", "commercial_name",
}


def validar_entrada(con):
    """Comprueba que el Parquet no esté vacío y tenga las columnas esperadas."""
    nombres = {r[0] for r in con.execute("DESCRIBE SELECT * FROM datos_raw").fetchall()}
    faltantes = COLUMNAS - nombres
    if faltantes:
        raise ValueError(f"Faltan columnas: {sorted(faltantes)}")
    total = con.execute("SELECT COUNT(*) FROM datos_raw").fetchone()[0]
    if not total:
        raise ValueError("El corte está vacío; no se procesará como una eliminación masiva")
    return total


def validar_limpieza(con, total):
    """Detiene cortes inválidos, pero conserva importes vacíos, cero y negativos."""
    cuenta, tipos, fechas, clientes, productos, fondos, vacios, negativos, ceros = con.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE type IS NULL OR type NOT IN ('entrada', 'salida')),
               COUNT(*) FILTER (WHERE date IS NULL),
               COUNT(*) FILTER (WHERE id_cliente IS NULL OR trim(id_cliente) = ''),
               COUNT(*) FILTER (WHERE product IS NULL OR trim(product) = ''),
               COUNT(*) FILTER (WHERE fund IS NULL OR fund = ''),
               COUNT(*) FILTER (WHERE amount IS NULL),
               COUNT(*) FILTER (WHERE amount < 0),
               COUNT(*) FILTER (WHERE amount = 0)
        FROM datos_limpios
    """).fetchone()
    if cuenta != total or any((tipos, fechas, clientes, productos, fondos)):
        raise ValueError(f"Corte inválido: filas={cuenta}, tipos={tipos}, "
                         f"fechas={fechas}, clientes={clientes}, "
                         f"productos={productos}, fondos={fondos}")
    print(f"  {cuenta} filas · montos vacíos={vacios}, negativos={negativos}, cero={ceros}")


def validar_comparacion(con, anterior, actual):
    """Comprueba que cada fila de los dos cortes aparezca una sola vez."""
    n_anterior = con.execute(
        "SELECT COUNT(*) FROM movimientos WHERE corte = ?", [anterior]
    ).fetchone()[0]
    n_actual = con.execute(
        "SELECT COUNT(*) FROM movimientos WHERE corte = ?", [actual]
    ).fetchone()[0]
    contados = con.execute("""
        SELECT COUNT(fila_anterior), COUNT(fila_actual)
        FROM historial_cambios
        WHERE corte_anterior = ? AND corte_actual = ?
    """, [anterior, actual]).fetchone()
    if contados != (n_anterior, n_actual):
        raise ValueError(f"No cuadran los movimientos {anterior} -> {actual}: {contados}")
    duplicados = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT fila_anterior FROM historial_cambios
            WHERE corte_anterior = ? AND corte_actual = ? AND fila_anterior IS NOT NULL
            GROUP BY fila_anterior HAVING COUNT(*) > 1
            UNION ALL
            SELECT fila_actual FROM historial_cambios
            WHERE corte_anterior = ? AND corte_actual = ? AND fila_actual IS NOT NULL
            GROUP BY fila_actual HAVING COUNT(*) > 1
        )
    """, [anterior, actual, anterior, actual]).fetchone()[0]
    if duplicados:
        raise ValueError(f"Hay {duplicados} filas usadas más de una vez")
    print(f"  {anterior}={n_anterior}; {actual}={n_actual}; ninguna fila sin contabilizar")


def validar_reportes(con):
    """Comprueba sumas por tipo, fondo, producto, fecha y flujo."""
    cortes = con.execute("SELECT corte FROM cortes ORDER BY orden").fetchall()
    for (corte,) in cortes:
        original = con.execute(
            "SELECT SUM(amount) FROM movimientos WHERE corte = ?", [corte]
        ).fetchone()[0]
        for tabla in ("resumen_tipos", "resumen_fondos", "resumen_productos", "resumen_fechas"):
            suma = con.execute(f"SELECT SUM(monto_total) FROM {tabla} WHERE corte = ?", [corte]).fetchone()[0]
            if original != suma:
                raise ValueError(f"La suma de {tabla} no coincide para {corte}")
        flujo_esperado = con.execute("""
            SELECT SUM(CASE WHEN type = 'entrada' THEN amount ELSE -amount END)
            FROM movimientos WHERE corte = ?
        """, [corte]).fetchone()[0]
        flujo_calculado = con.execute(
            "SELECT flujo_neto_reportado FROM resumen_flujo WHERE corte = ?", [corte]
        ).fetchone()[0]
        if flujo_esperado != flujo_calculado:
            raise ValueError(f"El flujo no coincide para {corte}")
        sin_negativos = con.execute("""
            SELECT SUM(CASE WHEN amount >= 0
                THEN CASE WHEN type = 'entrada' THEN amount ELSE -amount END
                ELSE 0 END)
            FROM movimientos WHERE corte = ?
        """, [corte]).fetchone()[0]
        reportado_sin_negativos = con.execute("""
            SELECT flujo_excluyendo_negativos FROM resumen_flujo WHERE corte = ?
        """, [corte]).fetchone()[0]
        if sin_negativos != reportado_sin_negativos:
            raise ValueError(f"El flujo sin negativos no coincide para {corte}")
    esperados = con.execute("""
        SELECT COUNT(*) FROM historial_cambios
        WHERE estado = 'corregido' AND detalle = 'monto'
    """).fetchone()[0]
    observados = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM resumen_impacto_montos").fetchone()[0]
    if esperados != observados:
        raise ValueError("No coincide la cantidad de posibles cambios de monto")
    print("Sumas por tipo, fondo, producto, fecha y flujo verificadas")
