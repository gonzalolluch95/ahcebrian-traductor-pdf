# Traductor de PDF

Traduce un PDF conservando el diseño: tablas, logos, rejillas, columnas y
colores se quedan donde estaban. Es el microservicio que hay detrás de la
herramienta de `/tools` en ahcebrian.com.

No guarda nada. El documento solo existe en la memoria del contenedor mientras
dura la petición, y Modal lo destruye al terminar: no hay disco, ni base de
datos, ni copia del contenido en ningún sitio.

## Por qué existe este repositorio

Porque [PyMuPDF](https://github.com/pymupdf/PyMuPDF) es AGPL-3.0, y una licencia
AGPL obliga a ofrecer el código fuente a quien usa el programa **por red**. Esto
es lo que corre detrás de la herramienta de
[ahcebrian.com/tools](https://ahcebrian.com/tools/), publicado para cumplir con
esa obligación.

Es una **copia del directorio `traductor/`** del repositorio privado de la web.
No es una biblioteca ni pretende serlo: está escrito para los documentos que le
llegan a una empresa concreta (fichas técnicas, certificados y listas de precios
que vienen de Turquía). Se puede leer, copiar y adaptar bajo los términos de la
AGPL, pero no hay soporte ni se atienden incidencias.

Las pruebas que miden la fidelidad esperan encontrar los PDF del cliente, que
por razones obvias no están aquí: sin ellos se saltan solas y queda el documento
sintético, que sí se construye en el propio código (`pruebas/simulacro.py`).

## Las piezas

| Fichero | Qué hace |
| --- | --- |
| `motor.py` | El motor: lee los fragmentos de una página y vuelve a escribirlos traducidos. Solo depende de PyMuPDF. |
| `fuentes.py` | Con qué letra se reescribe cada cosa: busca en el sistema un clon métricamente compatible de la fuente original. |
| `analisis.py` | El veredicto: traducible, escaneado, mixto o protegido, y qué páginas se quedan fuera. |
| `ocr.py` | El camino del escaneado: rasteriza, pasa Tesseract, tapa y escribe encima. |
| `idiomas.py` | Detección del idioma con lingua-py. |
| `mt.py` | Cliente de traducción: pide los textos a `/api/tools/mt` de la web. |
| `servicio.py` | La aplicación web (Starlette): `/analizar`, `/traducir`, `/despertar`. |
| `app.py` | El envoltorio de Modal. Publica `servicio.py` con autenticación de proxy. |

`motor.py`, `fuentes.py`, `analisis.py`, `ocr.py` y `idiomas.py` no saben nada
de Modal ni de la web: se pueden usar y probar en cualquier ordenador con
PyMuPDF.

## Cómo funciona

El servicio **nunca habla con un modelo de lenguaje**. Pide las traducciones de
vuelta al Worker de Cloudflare de la propia web, firmando con un vale que ese
Worker emitió al empezar el trabajo. Así la clave del modelo no sale de
Cloudflare, el gasto se lleva en un sitio y se puede cambiar de motor sin tocar
este contenedor.

```
navegador → Worker (/api/tools/pdf/traducir) → Modal (/traducir)
                    ↑                              │
                    └──── /api/tools/mt ───────────┘   (una petición por página)
```

Una petición por página, y no una por párrafo, es lo que hace que quepa en la
cuota gratuita: un documento de 10 páginas gasta 10 peticiones, no 200.

Por el camino de vuelta, el servicio va contando: suelta una línea de progreso
cada vez que termina un paso **dentro** de cada página (leída, traducida,
escrita), y otra al empezar a cerrar el documento. No es un capricho: lo que
tarda una página es casi todo la espera al modelo, y contando solo páginas
enteras la barra del navegador se quedaba parada minutos enteros.

## Qué tan parecido sale

La prueba es tonta y por eso sirve: se «traduce» el documento dejando el texto
**exactamente igual**. Si el motor fuera perfecto, la salida sería el original
píxel por píxel, así que todo lo que se separe de cero es cosa suya. La segunda
mitad mide lo que de verdad duele: con el texto un 15 % más largo (lo que crece
el turco), cuántas líneas acaban escritas **encima de otra**.

```bash
python pruebas/fidelidad.py
```

| | antes | ahora |
| --- | --- | --- |
| diferencia con el texto igual | 7,68 % | **6,17 %** |
| líneas pisadas con el texto un 15 % más largo | **89** | **3** |

Las dos columnas no miden lo mismo, y la segunda es la que importa: una línea
pisada no es un defecto estético, es texto que no se puede leer.

**Que un documento salga «distinto» no quiere decir que salga mal.** El peor de
los ocho (`Food Defence`) marca casi un 12 % y no tiene ni una línea pisada: lo
que cambia es cómo parten las líneas dentro de cada párrafo. Y buena parte de
lo que queda es una deriva de una décima de punto por palabra, porque MuPDF
separa las palabras un pelín menos que el generador original; al final de una
línea larga eso suma un punto y el contador de píxeles lo ve, pero un ojo no.

El corpus son ocho documentos elegidos porque cada uno rompía algo: tablas con
rejilla, una ficha de seguridad, una política llena de listas, un folleto a dos
columnas y un par de fichas normales.

## Las reglas del motor

Salieron de probarlo sobre documentos reales y no conviene «simplificarlas»:

1. **Las tablas primero, celda a celda.** Sustituir por bloques funde los
   párrafos y destroza la rejilla.
2. **Lo que solo son números no se traduce nunca.** Ahorra peticiones y quita el
   riesgo de que un modelo «mejore» un precio.
3. **Al borrar el texto original, conservar imágenes y líneas**
   (`PDF_REDACT_IMAGE_NONE` / `PDF_REDACT_LINE_ART_NONE`), o desaparecen el logo
   y la rejilla de la tabla.
4. **Antes de encoger la letra, crecer hacia abajo** hasta el siguiente
   elemento.
5. **Conservar la alineación del original**, deducida del sitio que le sobra al
   texto a cada lado dentro de su columna.
6. **`subset_fonts()` antes de guardar.** Con chino: 22 MB → 0,11 MB.
7. **El chino y el japonés no necesitan configurar fuentes**: `insert_htmlbox`
   elige una que tenga esos glifos.

Y estas salieron de medir la fidelidad, no de mirarla:

8. **La letra, la de verdad.** La fuente incrustada en el PDF no sirve (viene
   subconjuntada y sin tabla de caracteres), así que se busca en el sistema un
   clon **métricamente compatible**: Liberation por Arial y Times, Carlito por
   Calibri, Caladea por Cambria. Miden lo mismo letra por letra, y por eso las
   líneas parten por el mismo sitio. Si la fuente elegida no tiene algún
   carácter del texto traducido, se deja que MuPDF elija: más vale otra letra
   que un cuadrado vacío. **Las fuentes hay que instalarlas en la imagen de
   Modal** (ver `FUENTES` en `app.py`); sin ellas no hay nada que hacer.
9. **El interlineado, el del documento.** Estaba puesto a ojo en 1,15 y casi
   ninguno usa ese: los de Word van por 1,27.
10. **La primera línea, donde apoyaba.** `insert_htmlbox` escribe desde el borde
    de arriba de la caja, no desde la línea base, y sin corregirlo todo el
    documento baja dos puntos: poco, pero basta para que las viñetas dejen de
    estar a la altura de su línea.
11. **Probar en un banco antes de escribir.** `insert_htmlbox` no deja
    preguntar: o escribe o no escribe. Cada intento se ensaya primero en una
    página de mentira, y de ahí salen las dos cosas que hacen falta antes de
    tocar la buena: si cabe y cuánto ha habido que encoger la letra.
12. **La anchura del original manda.** Es lo que decide por dónde parten las
    líneas. Ensanchar la caja hasta la columna rehacía el párrafo entero aunque
    la traducción midiera lo mismo.
13. **Un punto de lista empieza párrafo, y un cambio de letra también.** Sin
    eso, una política de alérgenos entera salía convertida en un párrafo
    corrido.
14. **El «4.» de una lista ni se traduce ni se mueve.** Entre el número y el
    texto casi nunca hay un espacio: hay un tabulador, y al reescribirlo se
    convertía en un espacio y la lista se descuadraba cuatro puntos.
15. **Está debajo lo que acaba más abajo.** La caja de una línea es más alta
    que el paso de una línea a la siguiente, así que la de debajo *empieza* por
    encima de donde acaba ésta. Mirando solo dónde empieza, la línea de justo
    detrás no contaba como obstáculo y la traducción se escribía encima.
16. **Una línea corta lo es respecto al hueco en el que vive**, no respecto al
    ancho de la página, y solo cuenta lo que tenga a la **derecha**. Midiéndolo
    contra la página, en un documento a dos columnas todas las líneas parecen
    cortas y todas tienen compañía al lado (la otra columna): cada línea salía
    como un fragmento suelto, se traducían de una en una y al crecer se
    escribían unas encima de otras. 55 líneas pisadas en un folleto de tres
    páginas.
17. **Lo que se lleva la tabla se apunta por identidad, no por geometría.** Se
    miraba dos veces si una línea caía dentro de una celda, y bastaba con que
    PyMuPDF midiera una celda un poco estrecha para que contara como celda en
    el primer reparto y como párrafo en el segundo: el mismo texto escrito dos
    veces, uno encima del otro. Salió con una página girada 270°.
18. **Los gemelos invisibles, de vuelta a casa.** Al escribir con una fuente
    incrustada, MuPDF apunta que el glifo del espacio es un espacio duro y el
    del guion un guion blando (comparten dibujo y elige el código más alto). Se
    ve perfecto, pero buscar «White-yellowish» dentro del PDF ya no encontraba
    nada. Se reescribe esa tabla al guardar.

## Publicar y probar

```bash
python -m modal deploy app.py

# Las pruebas del motor y del veredicto (69 comprobaciones, sin red ni cuota):
python pruebas/motor_test.py

# Lo que sale por el cable mientras se traduce (20 comprobaciones): que se
# cuente varias veces por pagina, que el progreso no vaya hacia atras y que un
# PDF con contraseña se explique en vez de reventar. Necesita starlette:
python pruebas/servicio_test.py

# Cuánto se parece el resultado al original, en un número (necesita numpy,
# que es solo para mirar: el contenedor no lo lleva):
python pruebas/fidelidad.py

# Mirar el resultado, sin gastar cuota (texto un 15 % más largo, o chino):
python pruebas/simulacro.py "ruta/al.pdf" largo

# De punta a punta contra el Worker en local (gasta cuota: una petición/página):
npx wrangler dev          # en otra terminal, desde web/
python pruebas/real.py "ruta/al.pdf" es

# El camino del escaneado, dentro de Modal (Tesseract solo está allí):
python -m modal run pruebas/ocr_modal.py --ruta "ruta/al/escaneado.pdf" --origen tr

# Qué fuentes hay DENTRO del contenedor y con cuál se escribiría cada cosa.
# Merece la pena después de cada `modal deploy`: si el apt_install de las
# fuentes se cayera, todo seguiría funcionando pero con otra letra, y no
# avisaría nadie.
python -m modal run pruebas/fuentes_modal.py
```

Dos avisos que cuestan tiempo si no se saben:

- Después de `modal deploy`, **un contenedor caliente puede seguir sirviendo el
  código anterior**. Si el resultado no cuadra con lo que acabas de cambiar,
  vuelve a publicar (o `modal app stop ahcebrian-traductor -y`) y repite.
- Las rutas son **Starlette, no FastAPI**, a propósito: con FastAPI, una ruta
  `async def analizar(request: Request)` respondía `422 query.request field
  required` a todo. Ver el comentario de cabecera de `servicio.py`.

## Licencia

**AGPL-3.0** (ver `LICENSE`). No es una elección de estilo: este servicio usa
[PyMuPDF](https://github.com/pymupdf/PyMuPDF), que es AGPL-3.0, y una licencia
AGPL obliga a ofrecer el código fuente a quien usa el programa **por red**. Por
eso este código es público y `/tools` enlaza aquí al pie.

La web y el panel de ahcebrian.com son programas distintos, que solo hablan con
este servicio por HTTP, y no quedan afectados por esta licencia.
