"""Relaciona cada pareja de cortes sin reutilizar filas ni inventar IDs."""

# Columnas que deben coincidir para considerar que dos filas son iguales.
CAMPOS = ("id_cliente", "date", "product", "type", "fund",
          "amount", "description", "commercial_name")


def _coinciden(campos):
    """Construye la comparación incluyendo correctamente los valores NULL."""
    return " AND ".join(f"a.{c} IS NOT DISTINCT FROM b.{c}" for c in campos)


def _restantes(con, tabla_a, tabla_b, parejas, prefijo):
    """Prepara las filas que todavía no aparecen en una pareja encontrada."""
    for lado, tabla, id_col in (("a", tabla_a, "fila_anterior"),
                                ("b", tabla_b, "fila_actual")):
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE {prefijo}_{lado} AS
            SELECT x.* FROM {tabla} x
            WHERE NOT EXISTS (
                SELECT 1 FROM {parejas} p WHERE p.{id_col} = x.fila_id
            )
        """)


def _parejas_unicas(con, nombre, campo_diferente, prefijo):
    """Acepta una pareja solamente si es la única opción en ambos sentidos."""
    iguales = _coinciden([c for c in CAMPOS if c != campo_diferente])
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {nombre} AS
        WITH candidatos AS (
            SELECT a.fila_id AS fila_anterior, b.fila_id AS fila_actual,
                   COUNT(*) OVER (PARTITION BY a.fila_id) AS opciones_a,
                   COUNT(*) OVER (PARTITION BY b.fila_id) AS opciones_b
            FROM {prefijo}_a a JOIN {prefijo}_b b ON {iguales}
            WHERE a.{campo_diferente} IS DISTINCT FROM b.{campo_diferente}
        )
        SELECT fila_anterior, fila_actual FROM candidatos
        WHERE opciones_a = 1 AND opciones_b = 1
    """)


def comparar(con, anterior, actual):
    """Guarda iguales, posibles correcciones y posibles altas/bajas entre dos cortes."""
    # Los nombres de corte proceden de archivos T, T1, T2... validados.
    con.execute(f"CREATE OR REPLACE TEMP VIEW a AS SELECT * FROM movimientos WHERE corte = '{anterior}'")
    con.execute(f"CREATE OR REPLACE TEMP VIEW b AS SELECT * FROM movimientos WHERE corte = '{actual}'")

    # Las filas repetidas exactamente iguales se emparejan una por una.
    campos = ", ".join(CAMPOS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE parejas_iguales AS
        WITH aa AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {campos} ORDER BY fila_id
            ) AS numero FROM a
        ), bb AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {campos} ORDER BY fila_id
            ) AS numero FROM b
        )
        SELECT a.fila_id AS fila_anterior, b.fila_id AS fila_actual
        FROM aa a JOIN bb b ON {_coinciden(CAMPOS)} AND a.numero = b.numero
    """)
    _restantes(con, "a", "b", "parejas_iguales", "sin_iguales")

    # Probamos una columna distinta cada vez, siempre con pareja única en ambos lados.
    etapas = (
        ("monto", "amount"),
        ("descripcion", "description"),
        ("comercial", "commercial_name"),
        ("fondo", "fund"),
        ("producto", "product"),
        ("tipo", "type"),
        ("fecha", "date"),
    )
    restante = "sin_iguales"
    for nombre, campo in etapas:
        _parejas_unicas(con, f"parejas_{nombre}", campo, restante)
        siguiente = f"sin_{nombre}"
        _restantes(con, f"{restante}_a", f"{restante}_b",
                   f"parejas_{nombre}", siguiente)
        restante = siguiente

    # La diferencia entre 'observado' y 'posible' queda guardada en la tabla.
    for tabla, detalle in (("parejas_iguales", "igual"),) + tuple(
        (f"parejas_{nombre}", nombre) for nombre, _ in etapas
    ):
        estado = "sin_cambios" if detalle == "igual" else "corregido"
        certeza = "coincidencia_exacta" if detalle == "igual" else "posible"
        con.execute(f"""
            INSERT INTO historial_cambios
            SELECT ?, ?, fila_anterior, fila_actual, ?, ?, ? FROM {tabla}
        """, [anterior, actual, estado, certeza, detalle])
    con.execute(f"""
        INSERT INTO historial_cambios
        SELECT ?, ?, fila_id, NULL, 'eliminado', 'posible', 'sin_pareja'
        FROM {restante}_a
    """, [anterior, actual])
    con.execute(f"""
        INSERT INTO historial_cambios
        SELECT ?, ?, NULL, fila_id, 'nuevo', 'posible', 'sin_pareja'
        FROM {restante}_b
    """, [anterior, actual])
