"""Unifica los campos para comparar los cortes sin modificar los Parquet."""


def limpiar(con):
    """Crea una vista con fechas, tipos, fondos e importes normalizados."""
    con.execute("""
        CREATE OR REPLACE TEMP VIEW datos_limpios AS
        SELECT * REPLACE (
            CASE
                WHEN lower(trim(type)) IN ('in', 'entrada') THEN 'entrada'
                WHEN lower(trim(type)) IN ('out', 'salida') THEN 'salida'
                ELSE lower(trim(type))
            END AS type,
            lower(trim(regexp_replace(fund, ' +', ' ', 'g'))) AS fund,
            CAST(COALESCE(
                try_strptime(date, '%Y-%m-%d'),
                try_strptime(date, '%d/%m/%Y')
            ) AS DATE) AS date,
            CAST(amount AS DECIMAL(18, 2)) AS amount
        )
        FROM datos_raw
    """)
