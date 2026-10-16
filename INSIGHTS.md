# Insights de los dos archivos recibidos

Los resultados provienen de la comparación y los reportes generados a partir de los Parquet de Tyba. No se deben interpretar automáticamente como saldos de clientes.

## 1. El segundo archivo tiene menos filas

T contiene **50.000** movimientos y T1 **49.000**: son **1.000 filas menos (2 %)**. Esto no demuestra que se hayan eliminado 1.000 transacciones: también existen correcciones y altas.

## 2. Las entradas son más frecuentes

En T aparecen **27.606** entradas frente a **22.394** salidas. En T1 hay **27.100** entradas y **21.900** salidas. Los totales son conteos de filas, no montos netos.

## 3. Hay diferencias entre los fondos

**Renta fija** tiene más movimientos en ambos archivos: **7.389** en T y **7.261** en T1. **Internacional** pasa de **7.138** a **6.946**, la reducción más alta en cantidad (**192 filas**).

## 4. Todos los importes negativos son entradas

En los archivos entregados se observaron **1.034 importes negativos en T** y **989 en T1**. Según el reporte de calidad, todos están marcados como `entrada`. **Se conservan tal como llegaron** porque no se conoce si corresponden a reversos, ajustes o un error de origen.

## 5. Los negativos afectan el flujo calculado

Tomando los valores originales de T, la suma de entradas menos la suma de salidas es de aproximadamente **75,48 mil millones** (en las unidades monetarias del archivo). Excluyendo los importes negativos, esa misma operación da aproximadamente **101,51 mil millones**. La diferencia aproximada es de **26,03 mil millones**, lo que muestra cuánto influyen las entradas negativas en el cálculo. Estas cifras son aproximadas y no representan dos saldos bancarios válidos: la convención contable del archivo no está confirmada. Las cantidades exactas se generan en `resumen_flujo.csv` y `data/output/insights.md`.

## 6. Posibles correcciones de monto

El pipeline calcula el efecto de las parejas que solo difieren en `amount`. En `resumen_impacto_montos.csv` informa cuántas parejas aparecen, en cuántas hay un valor vacío y cuánto suman las diferencias entre montos conocidos. La cifra también se incorpora al informe `data/output/insights.md` cuando se ejecuta el programa.

Este resultado no representa dinero efectivamente corregido: las parejas son **posibles coincidencias**, no transacciones confirmadas. Además, los montos `NULL` no permiten calcular una diferencia numérica.

## Cuidado al interpretar las correcciones

La guía describe `id` como identificador de transacción, pero el archivo trae `id_cliente`. El programa busca coincidencias exactas y posibles correcciones de un solo campo, pero **no puede confirmar que dos movimientos parecidos sean la misma transacción**. Por eso los resultados de la tabla `historial_cambios` distinguen `coincidencia_exacta` y `posible`.

En este repositorio también se genera automáticamente `data/output/insights.md` para mostrar cifras actualizadas si se añaden otros cortes.
