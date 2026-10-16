# Pipeline de movimientos financieros

Este proyecto procesa archivos Parquet con movimientos financieros, compara cada corte con el anterior y guarda los resultados en DuckDB.

## Ejecutar

Es necesario tener Docker Desktop instalado y funcionando. Desde la carpeta principal del proyecto (`repo`), ejecutar:

```bash
docker compose up --build
```

La primera ejecución crea `data/output/`, la base `movimientos.duckdb`, ocho reportes CSV y un informe `insights.md`.

Para ejecutar las pruebas automáticas:

```bash
docker compose run --rm pipeline python -m pytest -q tests/
```

También se puede ejecutar localmente con Python, después de instalar las dependencias:

```bash
python -m pip install -r requirements.txt
python -m pytest -q tests/
python src/pipeline.py
```

## Carpetas

- `data/raw/`: archivos Parquet originales, sin modificar.
- `data/output/`: base de datos y reportes generados.
- `src/pipeline.py`: encuentra los archivos, carga los cortes y ejecuta los pasos del proceso.
- `src/limpieza.py`: unifica fechas, fondos, tipos de movimiento y montos.
- `src/validaciones.py`: comprueba la calidad de los datos y los resultados.
- `src/comparacion.py`: identifica coincidencias y posibles cambios sin utilizar una misma fila en varias parejas.
- `src/analisis.py`: calcula los reportes financieros.
- `tests/test_pipeline.py`: contiene pruebas con datos pequeños y del proceso completo.
- `scripts/benchmark.py`: permite medir el rendimiento con datos sintéticos.
- `INSIGHTS.md`: presenta los principales hallazgos de los archivos recibidos.

## Cómo compara los movimientos

Los archivos representan cortes completos de información: T, T1 y, si se añaden después, T2, T3, etc.

El programa conserva los movimientos de cada corte en la tabla `movimientos`. Además, guarda información del archivo procesado en `cortes`, para evitar cargarlo nuevamente.

Al recibir un nuevo corte, lo compara con el anterior.

La tabla `historial_cambios` contiene cuatro estados:

| Estado | Qué significa | Certeza |
|---|---|---|
| `sin_cambios` | La fila aparece exactamente igual en ambos cortes | Coincidencia de contenido observada |
| `corregido` | Se encontró una posible pareja con diferencia en un solo campo | Posible |
| `eliminado` | Una fila del corte anterior no encontró pareja | Posible |
| `nuevo` | Una fila del corte más reciente no encontró pareja | Posible |

### Identificación de los movimientos

Durante la revisión encontré una diferencia entre la guía y los archivos recibidos.

El documento describe una columna `id` como identificador de cada transacción, pero los Parquet contienen `id_cliente`.

Como un mismo cliente puede tener varios movimientos, no sería correcto utilizar solamente ese campo para identificar una transacción.

Por eso decidí:

- Identificar primero las filas completamente iguales.
- Buscar posibles cambios en las demás columnas, comprobando que las parejas sean únicas.
- Evitar relacionar una misma fila con varios movimientos.
- Conservar por separado los movimientos que no tienen una pareja identificada.
- Marcar como posibles las correcciones, altas y bajas que no pueden confirmarse.

No se crea un identificador artificial para asumir que dos operaciones son la misma transacción.

La tabla `historial_movimientos` permite consultar los valores anteriores y posteriores de las filas relacionadas.

Los campos `corte_anterior` y `corte_actual` indican entre qué archivos se encontró una diferencia. No representan fechas reales de inicio o fin de vigencia de una transacción.

La columna `date` corresponde a la fecha del movimiento.

También existe la vista `movimientos_actuales`, que permite consultar todas las filas del último corte procesado.

## Cómo maneja la calidad de los datos

Antes de comparar los movimientos, el programa revisa y prepara la información.

Las decisiones de limpieza fueron:

- Convertir fechas escritas como `YYYY-MM-DD` y `DD/MM/YYYY` a un mismo tipo de fecha.
- Unificar `IN`, `in` y `entrada` como `entrada`, y sus equivalentes de salida como `salida`.
- Quitar espacios adicionales y unificar mayúsculas y minúsculas en los fondos.
- Convertir los importes a `DECIMAL(18,2)`.
- Conservar los importes vacíos, negativos y en cero.
- Permitir descripciones y nombres comerciales vacíos, sin inventar valores.
- Conservar movimientos que compartan datos similares, porque podrían ser operaciones diferentes.

El proceso se detiene cuando falta una columna necesaria, el archivo está vacío o se encuentran datos inválidos en campos necesarios para el procesamiento, como la fecha, el cliente, el producto, el fondo o el tipo de movimiento.

En esos casos, el corte no se registra como cargado.

### Ejemplos encontrados en los archivos entregados

| Revisión | T | T1 |
|---|---:|---:|
| Montos vacíos | 1.543 | 1.440 |
| Montos negativos | 1.034 | 989 |
| Montos en cero | 940 | 903 |
| Descripciones vacías | 4.563 | 4.485 |
| Nombres comerciales vacíos | 8.307 | 8.167 |
| Grupos repetidos por siete campos después de limpiar | 0 | 1 |

Un ejemplo fue el cliente `CLI002674`, que tenía dos movimientos con siete campos iguales, pero montos diferentes.

Decidí conservar ambas filas porque no había información suficiente para afirmar que fueran duplicadas incorrectamente.

Estas comprobaciones se calculan automáticamente para cada corte y sus resultados se guardan en `resumen_calidad.csv`.

### Montos negativos y flujo

Encontré que los **1.034 importes negativos de T** y los **989 de T1** están registrados como movimientos de tipo `entrada`.

No convertí esos valores a positivos ni los eliminé, porque no se conoce si corresponden a ajustes, reversos u otra regla del negocio.

Para mostrar su impacto, el reporte de flujo presenta dos cálculos:

- El flujo utilizando los signos originales: entradas menos salidas.
- El mismo cálculo excluyendo los importes negativos.

Esto permite observar cuánto influyen los negativos en el resultado, pero no significa que uno de los dos cálculos represente un saldo bancario confirmado.

Los montos `NULL` se conservan y se cuentan, aunque no participan en las sumas de SQL.

## Reportes generados

El programa genera ocho archivos CSV dentro de `data/output/`.

| Archivo | Contenido |
|---|---|
| `resumen_tipos.csv` | Cantidad de entradas, salidas e importes por corte |
| `resumen_fondos.csv` | Cantidad de movimientos e importes por fondo |
| `resumen_productos.csv` | Movimientos e importes por producto |
| `resumen_fechas.csv` | Movimientos e importes por fecha |
| `resumen_flujo.csv` | Flujo financiero y efecto de los montos negativos |
| `resumen_comparacion.csv` | Cantidad de movimientos por estado y comparación |
| `resumen_impacto_montos.csv` | Diferencias de importe en las posibles correcciones de monto |
| `resumen_calidad.csv` | Valores vacíos, negativos, ceros y combinaciones repetidas |

También se genera `data/output/insights.md` con los hallazgos calculados a partir de los datos procesados.

## Resultados de la comparación

Los archivos recibidos tienen:

| Corte | Movimientos |
|---|---:|
| T | 50.000 |
| T1 | 49.000 |

El segundo archivo contiene 1.000 filas menos, lo que representa una disminución del **2 %**.

Sin embargo, esa diferencia no significa necesariamente que se hayan eliminado 1.000 transacciones, porque también existen posibles correcciones y movimientos nuevos.

La comparación permitió identificar:

| Resultado | Cantidad |
|---|---:|
| Sin cambios | 35.159 |
| Posibles cambios de monto | 2.340 |
| Posibles cambios de descripción | 1.501 |
| Sin relacionar en T | 11.000 |
| Sin relacionar en T1 | 10.000 |

También revisé posibles cambios en nombre comercial, fondo, producto, tipo y fecha.

Para evitar relacionar movimientos incorrectamente, busqué coincidencias únicas en ambos sentidos.

**Esa búsqueda no encontró parejas adicionales, por lo que los resultados se mantuvieron iguales.**

Los movimientos sin pareja se conservan como posibles altas o bajas, sin afirmar que sean casos confirmados.

Entre los resultados financieros, encontré que `renta fija` fue el fondo con mayor cantidad de movimientos. Por su parte, `internacional` presentó la mayor disminución en cantidad, con 192 registros menos en T1.

Estos resultados describen diferencias entre los cortes, pero no necesariamente entradas o salidas reales de dinero.

### Seguimiento de movimientos entre varios cortes

Actualmente, el programa compara cada corte con el inmediatamente anterior.

Si un movimiento desaparece en T1 y vuelve a aparecer en T2, podría quedar identificado primero como posible eliminado y después como posible nuevo.

Con los dos archivos entregados en la prueba no es posible comprobar una reaparición.

Si necesitara ampliar el seguimiento, buscaría coincidencias también en cortes anteriores para intentar relacionar las operaciones a lo largo del tiempo.

Una consulta recursiva podría ayudar a reconstruir cadenas de posibles coincidencias. Sin embargo, esas relaciones seguirían siendo posibles mientras no exista un identificador único por transacción.

No incorporé esa funcionalidad porque no es necesaria para comparar los dos cortes recibidos.

## Pruebas y repetición segura

El proyecto incluye pruebas automáticas para comprobar situaciones como:

- Fechas en diferentes formatos.
- Fondos escritos con espacios y mayúsculas distintas.
- Montos vacíos.
- Tipos de movimiento desconocidos.
- Operaciones repetidas.
- Posibles cambios de diferentes campos.
- Movimientos que podrían relacionarse con más de una fila.
- Validaciones que deben detener el proceso cuando encuentran errores.
- Generación de reportes CSV y del archivo `insights.md`.
- Archivos vacíos y cortes no consecutivos.
- Ejecución completa y repetición del procesamiento.

Cada corte se guarda una sola vez, utilizando su nombre y una huella del contenido del archivo.

Si se ejecuta nuevamente el programa con los mismos archivos, no se agregan filas ni se repiten las comparaciones.

Si un archivo previamente cargado cambia, el programa detiene el procesamiento para que se revise antes de actualizar la información histórica.

Para agregar un nuevo corte, se puede incluir `movimientos_dia_T2.parquet` en `data/raw/` y volver a ejecutar el pipeline.

Los archivos deben seguir el orden T, T1, T2, etc. El programa puede procesarlos sin modificar el código, aunque todavía no incluye una ejecución diaria programada automáticamente.

## Informe de insights

Además de los reportes CSV, el proyecto incluye un archivo `INSIGHTS.md` en la carpeta principal.

Este documento presenta los resultados más importantes de los archivos entregados y explica su interpretación.

También se genera automáticamente `data/output/insights.md`, que contiene cifras calculadas durante la ejecución del pipeline.

Entre los hallazgos se encuentran:

- La disminución del 2 % en la cantidad de filas entre T y T1.
- La mayor cantidad de movimientos de entrada frente a salida.
- Las diferencias de registros entre los fondos.
- La presencia de montos negativos exclusivamente en movimientos de entrada.
- El efecto de esos importes negativos sobre el flujo calculado.
- El impacto de las posibles correcciones de monto cuyos valores son conocidos.

## Rendimiento y límites

El procesamiento utiliza principalmente consultas SQL de DuckDB, sin cargar todos los movimientos en listas de Python.

El programa permite agregar cortes consecutivos y evita cargar dos veces un archivo que ya fue procesado.

**La prueba de cinco millones de filas por corte no se realizó; sí se midió el rendimiento con un millón de filas por corte**, como se muestra a continuación.

Una medición con datos sintéticos permite observar el comportamiento del programa con un volumen mayor, pero no garantiza el mismo rendimiento con datos reales o en un entorno de producción.

### Prueba de rendimiento con datos sintéticos

Ejecuté `scripts/benchmark.py` dentro de Docker con 1.000.000 de filas en T y 1.000.000 en T1.

El programa terminó correctamente, contabilizó todas las filas, comprobó las sumas y generó los ocho reportes CSV y el archivo `insights.md`.

| Medida | Resultado |
|---|---:|
| Filas por corte | 1.000.000 |
| Tiempo total | 6,49 segundos |
| Tamaño de la base generada | 31,8 MiB |
| Memoria máxima aproximada del proceso | 1.388,3 MiB |

Comando utilizado:

```bash
docker compose run --rm pipeline python scripts/benchmark.py --rows 1000000
```

Las cifras corresponden a mi ejecución con datos sintéticos y no a una medición del sistema en producción.

El script genera archivos de prueba en un directorio temporal y no modifica los Parquet originales.

Para repetir la prueba con otra cantidad de filas, se puede cambiar el valor de `--rows`.

### Límites de la solución

- Sin un identificador único de transacción no es posible confirmar todas las correcciones, altas o bajas.
- La comparación utiliza cortes consecutivos. Las reapariciones en cortes posteriores no se reconocen automáticamente como tales.
- El programa permite agregar nuevos cortes, pero todavía no incluye una programación automática diaria.
- No sobrescribe cortes históricos que ya fueron procesados.
- Antes de utilizarlo en producción sería necesario confirmar el significado de los montos negativos y las reglas del negocio.
- También sería conveniente realizar pruebas de rendimiento adicionales con datos reales de mayor tamaño.


## Mejoras futuras: identificación de transacciones

Una de las principales dificultades que encontré durante el ejercicio fue identificar cada movimiento de forma individual.

En el documento se menciona una columna `id` para las transacciones, pero los archivos recibidos contienen `id_cliente`. Como un mismo cliente puede realizar varias operaciones, este dato no es suficiente para saber con certeza qué movimiento cambió entre un corte y otro.

Por eso, en esta solución decidí identificar las coincidencias que podía comprobar y dejar como **posibles** las correcciones, los movimientos nuevos y los eliminados.

Si tuviera que seguir desarrollando el proyecto, consideraría las siguientes mejoras. Ninguna está implementada en esta entrega.

### 1. Obtener un identificador desde la fuente

Mi primera opción sería consultar con el equipo si es posible recibir un identificador único para cada transacción, como se describe en el documento.

Esto permitiría comparar los movimientos directamente por su `id`, sin depender de que coincidan otras columnas.

También facilitaría guardar el historial de cada movimiento, identificar cuándo aparece o desaparece de los cortes y reconocer qué información fue corregida.

En ese caso, utilizaría la combinación de `id` y `corte` para distinguir las versiones de una misma transacción. Las fechas de vigencia reales podrían incorporarse si la fuente proporciona esa información.

### 2. Crear una clave a partir de los datos disponibles

Si no fuera posible obtener ese identificador, intentaría construir una clave utilizando los campos que normalmente deberían mantenerse iguales, como el cliente, la fecha, el producto, el fondo, el tipo de movimiento y el nombre comercial.

Dejaría por fuera el monto y la descripción porque ya comprobé que pueden cambiar entre cortes.

También tendría que distinguir los movimientos que compartan todos esos datos, por ejemplo, mediante un número de aparición.

Sin embargo, esta alternativa tiene una limitación: si cambia alguno de los campos que utilizo para construir la clave, el programa podría interpretar que desapareció un movimiento y apareció otro. Además, el número de aparición tendría que asignarse de forma consistente para evitar relacionar filas incorrectamente.

Por eso la consideraría una ayuda para comparar, pero no una identificación completamente confiable.

### 3. Asignar un identificador interno

Otra posibilidad sería asignar un identificador interno a cada movimiento cuando aparece por primera vez y conservarlo cuando se encuentre una posible coincidencia en los cortes siguientes.

Esto permitiría construir un historial más amplio y consultar cómo ha cambiado un movimiento con el tiempo. También podría utilizar una consulta recursiva para seguir las relaciones entre diferentes cortes.

El riesgo sería que, si relaciono dos movimientos incorrectamente, ese error podría mantenerse en las comparaciones posteriores.

Por eso conservaría la columna `certeza`, para distinguir las relaciones confirmadas de las posibles y facilitar su revisión.

### ¿Qué haría con los datos que ya procesé?

Si más adelante recibiera un identificador único de transacción, volvería a realizar la comparación utilizando ese campo.

Después contrastaría los nuevos resultados con las posibles coincidencias que encontré en esta versión. Así podría comprobar cuántas relaciones fueron correctas y cuáles necesitan ajustarse.

Mientras tanto, también utilizaría la cantidad de movimientos sin pareja como una señal para revisar la calidad de los datos.

Por ejemplo, si normalmente quedan pocos movimientos sin relacionar y en un nuevo corte esa cantidad aumenta considerablemente, revisaría si cambió la información que entrega la fuente o si hay algún problema en el procesamiento.

Estas mejoras permitirían hacer un seguimiento más preciso de los movimientos, pero para esta entrega decidí priorizar una comparación que conserve todos los registros y no dé por confirmadas relaciones que los datos no permiten comprobar.