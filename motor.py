"""
Motor de traduccion de PDF que conserva el diseño.

Trabaja en dos mitades separadas a proposito:

    fragmentos = leer_pagina(page)          # que hay que traducir y donde
    escribir_pagina(page, fragmentos, tr)   # lo mismo, ya traducido

Entre las dos va una sola peticion al traductor por pagina. Esa es la razon de
que existan las dos mitades: una peticion por parrafo agotaria la cuota diaria
del plan gratuito en un documento de diez paginas.

Las reglas que salieron de probarlo sobre los PDF reales (y que NO hay que
"simplificar"):

 1. Las tablas primero, celda a celda. Sustituir por bloques funde los parrafos
    y destroza la rejilla.
 2. Lo que solo son numeros no se traduce nunca.
 3. Al borrar el texto original hay que conservar imagenes y lineas
    (PDF_REDACT_IMAGE_NONE / PDF_REDACT_LINE_ART_NONE), o desaparecen el logo y
    la rejilla de la tabla.
 4. Antes de encoger la letra, crecer hacia abajo hasta el siguiente elemento.
 5. Conservar la alineacion del original.
 6. subset_fonts() antes de guardar (con chino: 22 MB -> 0,11 MB).
 7. El chino y el japones no necesitan configurar fuentes: insert_htmlbox elige
    una que tenga esos glifos.

Y las que salieron de medir en que se diferencia el resultado del original
teniendo el texto igual (ver pruebas/fidelidad.py, que da el numero):

 8. La letra, la de verdad. Un clon metricamente compatible mide lo mismo que
    la original y por eso las lineas parten por el mismo sitio (ver fuentes.py).
 9. El interlineado, el del documento. Estaba puesto a ojo en 1,15 y casi
    ninguno usa ese.
10. La primera linea, donde apoyaba. Si no, todo baja dos puntos y las viñetas
    dejan de estar a la altura de su linea.
11. Probar en un banco antes de escribir. Es la unica forma de saber si cabe
    sin haberlo escrito ya.
12. La anchura del original manda: ensancharla hasta la columna rehace el
    parrafo aunque la traduccion mida lo mismo.
13. Un punto de lista empieza parrafo, y un cambio de letra tambien. Sin eso,
    una lista entera sale convertida en un parrafo corrido.
14. El «4.» de una lista no se traduce ni se mueve: entre el y el texto hay un
    tabulador, y al reescribirlo se volveria un espacio.
15. Esta debajo lo que acaba mas abajo. La caja de una linea es mas alta que el
    paso de una linea a la siguiente, asi que mirar solo donde empieza dejaba
    fuera a la linea de justo debajo y la traduccion se escribia encima.
16. Una linea corta lo es respecto al hueco en el que vive, no al ancho de la
    pagina, y solo cuenta lo que tenga a la derecha. Si no, en un documento a
    dos columnas cada linea sale como un fragmento suelto.
17. Lo que se lleva la tabla se apunta por identidad, no por geometria: mirar
    dos veces si una linea cae dentro de una celda acaba escribiendo el mismo
    texto dos veces, uno encima del otro.

Solo depende de PyMuPDF (AGPL-3.0; ver LICENSE y el README de esta carpeta).
"""

from __future__ import annotations

import html
import re
import statistics
from dataclasses import dataclass, field

import pymupdf

from fuentes import Cara, taller

# --- Constantes medidas en el prototipo -------------------------------------

#: Corte de parrafo: hueco vertical mayor que esta fraccion de la altura de linea.
HUECO_DE_PARRAFO = 0.75
#: Corte de parrafo: cambio de tamaño de letra, en puntos.
CAMBIO_DE_TAMANO = 0.6
#: Hasta donde se permite encoger la letra antes de avisar.
ESCALA_MINIMA = 0.7
#: Margen que se deja al crecer hacia abajo, para no pegarse al elemento de abajo.
MARGEN = 2.0
#: Un poco de aire a los lados de una caja de texto suelta (no de una celda).
HOLGURA_LATERAL = 1.0
#: Lo que `insert_htmlbox` se guarda para si dentro de la caja: un punto justo,
#: siempre, mida lo que mida la letra (medido con cuatro tamaños y cuatro
#: interlineados distintos). No es tinta, es hueco, y por eso se le puede dar a
#: costa del espacio en blanco que la linea original ya tenia encima. Sin esto,
#: una celda de tabla en la que el texto cabia justo no cabe por una decima y
#: sale con la letra encogida sin ninguna necesidad.
SOBRECOSTE = 1.0
#: Interlineado cuando el fragmento tiene una sola linea y la pagina tampoco
#: dice nada. Es el de un parrafo normal de Word.
INTERLINEADO_POR_DEFECTO = 1.16
#: Entre que valores se da por bueno el interlineado medido. Fuera de ahi, lo
#: que se ha medido no es un parrafo: son dos cosas distintas pegadas.
INTERLINEADO_MINIMO, INTERLINEADO_MAXIMO = 0.9, 2.6


@dataclass
class Fragmento:
    """Un trozo de texto con todo lo que hace falta para volver a escribirlo."""

    id: str
    texto: str
    rect: pymupdf.Rect
    #: Caja en la que se puede escribir la traduccion (crecida hacia abajo).
    caja: pymupdf.Rect
    #: Hueco horizontal disponible y sitio libre por debajo, para recalcular la
    #: caja si cambia la alineacion.
    columna: pymupdf.Rect
    sitio: float
    #: Sitio libre por encima. Hace falta porque la caja de una linea es mas
    #: alta que sus letras: por arriba le sobresale el hueco del interlineado,
    #: que en el original estaba vacio y es suyo.
    arriba: float
    tam: float
    color: str
    negrita: bool
    cursiva: bool
    familia: str  # sans-serif | serif | monospace
    alineacion: str  # left | center | right | justify
    origen: str  # celda | parrafo
    #: El nombre de la fuente tal cual viene en el PDF ('AAAAAA+DejaVuSerif-Bold').
    fuente: str = ''
    #: Donde apoyaba la primera linea del original. Es lo que hay que clavar al
    #: volver a escribir: si el texto nuevo cae dos puntos mas abajo, se nota
    #: enseguida (las viñetas dejan de estar a la altura de su linea).
    base: float = 0.0
    #: Separacion entre lineas del original, en veces el tamaño de la letra.
    #: Cero mientras no se sepa: lo rellena `_interlineado_de_la_pagina`.
    interlineado: float = 0.0
    #: Sangria francesa: lo que las lineas de continuacion van metidas hacia
    #: dentro respecto de la primera. Es lo que hace que en una lista el texto
    #: quede en su columna y el «3.» fuera, a la izquierda.
    sangria: float = 0.0
    #: Cuantas lineas ocupaba en el original. Con una sola, el interlineado no
    #: se ve, y eso da una carta que jugar antes de encoger la letra.
    lineas: int = 1

    def html(self, texto: str, cara: Cara | None = None, interlineado: float = 0.0) -> str:
        estilo = [
            f'font-family:{cara.nombre if cara else self.familia}',
            f'font-size:{self.tam:.2f}px',
            f'color:{self.color}',
            f'text-align:{self.alineacion}',
            'margin:0',
            f'line-height:{interlineado or self.interlineado or INTERLINEADO_POR_DEFECTO:.3f}',
        ]
        if self.sangria:
            estilo.append(f'margin-left:{self.sangria:.2f}px')
            estilo.append(f'text-indent:{-self.sangria:.2f}px')
        # Con una cara de verdad (la negrita real de la familia, no una fingida)
        # hay que decirlo: si se deja `font-weight:bold`, MuPDF engorda encima
        # de una fuente que ya es gorda.
        if self.negrita:
            estilo.append('font-weight:bold' if (cara is None or cara.sintetica) else 'font-weight:normal')
        if self.cursiva:
            estilo.append('font-style:italic' if (cara is None or cara.sintetica) else 'font-style:normal')
        return f'<p style="{";".join(estilo)}">{html.escape(texto)}</p>'


@dataclass
class Aviso:
    """Algo que el usuario merece saber del resultado, sin ser un error."""

    pagina: int
    texto: str


@dataclass
class Resultado:
    avisos: list[Aviso] = field(default_factory=list)


# --- Regla 2: que no se traduce ---------------------------------------------

#: Trozos de letras seguidas, en cualquier alfabeto.
_PALABRAS = re.compile(r'[^\W\d_]+', re.UNICODE)

#: Con que empieza el punto de una lista: «3.», «3)», «c)», «•», «–»...
#: Una linea que empieza asi es un punto nuevo, no la continuacion del
#: anterior, por muy pegada que vaya. Es lo unico que distingue los dos casos
#: en una lista apretada, y sin ello una politica de alergenos entera sale
#: convertida en un solo parrafo corrido.
_MARCADOR = re.compile(r'(\d{1,3}[.)]|[a-zA-Z][.)]|[•·▪●−–—*-])\s')

#: Unidades y simbolos que acompañan a un numero y no son texto que traducir.
_UNIDADES = frozenset((
    'mm', 'cm', 'dm', 'm', 'km', 'um', 'nm', 'mg', 'g', 'kg', 'kgs', 't', 'lb', 'oz',
    'ml', 'cl', 'dl', 'l', 'm2', 'm3', 'cm2', 'cm3', 'ppm', 'ppb', 'ufc', 'cfu',
    'kcal', 'kj', 'kwh', 'pcs', 'uds', 'ud', 'un', 'nr', 'no', 'min', 'max', 'h',
    'kv', 'mpa', 'psi', 'rpm', 'eur', 'usd', 'try', 'c', 'f', 'k',
))


def es_traducible(texto: str) -> bool:
    """
    Falso para precios, medidas, codigos de lote, fechas y celdas vacias.

    Ahorra peticiones y, sobre todo, quita de en medio el riesgo de que un
    modelo de lenguaje "mejore" un precio o un numero de lote. Tres casos que
    parecen texto y no lo son, y que salieron de los documentos del cliente:
    "9,0 - 14,9 mm" (una medida), "€/Kg" (una unidad) y "2025/02WP" (un numero
    de documento). La regla: quitando numeros y unidades tiene que quedar alguna
    palabra de verdad, y si hay digitos, alguna de mas de tres letras.
    """
    limpio = texto.strip()
    if len(limpio) < 2:
        return False
    palabras = _PALABRAS.findall(limpio)
    utiles = [p for p in palabras if len(p) >= 2 and p.lower() not in _UNIDADES]
    if not utiles:
        return False
    if any(c.isdigit() for c in limpio) and all(len(p) <= 3 for p in utiles):
        return False
    return True


def _centro(rect: pymupdf.Rect) -> pymupdf.Point:
    return pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def _color(entero: int) -> str:
    return f'#{entero & 0xFFFFFF:06x}'


#: Fuentes que se sabe de que familia son. El nombre manda sobre las banderas
#: de PyMuPDF: ArialMT viene marcada como "con serifa" (bandera 4), y si se hace
#: caso a la bandera, un documento en Arial sale traducido en Times.
_MONO = ('courier', 'consol', 'menlo', 'mono')
_SERIF = ('times', 'roman', 'georgia', 'garamond', 'cambria', 'book', 'minion',
          'palatino', 'century', 'baskerville', 'caslon', 'serif', 'schoolbook')
_SANS = ('arial', 'helvetica', 'calibri', 'verdana', 'tahoma', 'segoe', 'roboto',
         'lato', 'futura', 'gill', 'myriad', 'univers', 'frutiger', 'avenir',
         'franklin', 'trebuchet', 'candara', 'corbel', 'sans')


def _familia(fuente: str, flags: int) -> str:
    nombre = fuente.lower()
    if any(x in nombre for x in _MONO) or flags & 2 ** 3:
        return 'monospace'
    for x in _SERIF:
        if x in nombre:
            return 'serif'
    for x in _SANS:
        if x in nombre:
            return 'sans-serif'
    return 'serif' if flags & 2 ** 2 else 'sans-serif'


def _es_negrita(flags: int, fuente: str) -> bool:
    return bool(flags & 2 ** 4) or 'bold' in fuente.lower()


def _es_cursiva(flags: int, fuente: str) -> bool:
    return bool(flags & 2 ** 1) or 'italic' in fuente.lower() or 'oblique' in fuente.lower()


# --- Lectura de la pagina ----------------------------------------------------

def _quitar_marcador(spans: list[dict]) -> tuple[list[dict], pymupdf.Rect | None]:
    """
    Aparta el numero o la viñeta del principio de un punto de una lista.

    Entre el «4.» y el texto casi nunca hay un espacio: hay un tabulador, y
    puede medir el triple. Si se mete el marcador dentro del texto que se
    traduce, al volver a escribirlo el tabulador se convierte en un espacio
    normal y la lista entera se descuadra cuatro puntos hacia la izquierda.

    Asi que el marcador no se toca: se queda donde estaba, dibujado como
    estaba, y lo que se traduce empieza en la primera letra de verdad. De paso,
    el modelo recibe la frase sin el numero delante, que tampoco le hacia falta.

    Solo se puede cuando el marcador viene en su propio trozo, que es lo
    normal. Si va pegado al texto en el mismo trozo, no hay forma de saber
    donde acaba y se deja como estaba.
    """
    primero = spans[0]['text'].strip()
    if not primero or not _MARCADOR.fullmatch(primero + ' '):
        return spans, None
    resto = spans[1:]
    if not resto:
        return spans, None
    return resto, pymupdf.Rect(spans[0]['bbox'])


def _lineas_de(pagina: pymupdf.Page) -> list[dict]:
    """Todas las lineas con texto de la pagina, en orden de lectura."""
    lineas = []
    for bloque in pagina.get_text('dict')['blocks']:
        if bloque['type'] != 0:
            continue
        for linea in bloque['lines']:
            spans = [s for s in linea['spans'] if s['text'].strip()]
            if not spans:
                continue
            spans, marcador = _quitar_marcador(spans)
            rect = pymupdf.Rect(spans[0]['bbox'])
            for s in spans[1:]:
                rect |= pymupdf.Rect(s['bbox'])
            lineas.append({
                'rect': rect,
                'texto': ''.join(s['text'] for s in spans),
                'span': spans[0],
                # Donde apoyan las letras, que no es el borde de la caja: la
                # caja incluye el hueco de los acentos y de las colas aunque la
                # linea no tenga ni unos ni otras, y por eso no sirve para
                # colocar el texto nuevo a la altura del viejo.
                'base': spans[0]['origin'][1],
                'bloque': bloque['number'],
                'estilo': (spans[0]['font'], round(spans[0]['size'], 1),
                           _es_negrita(spans[0]['flags'], spans[0]['font']),
                           _es_cursiva(spans[0]['flags'], spans[0]['font'])),
                'marcador': marcador is not None or bool(_MARCADOR.match(
                    ''.join(s['text'] for s in linea['spans']).lstrip())),
                #: Donde estaba el «4.» que se ha dejado fuera, si lo habia.
                'marca': marcador,
            })
    return lineas


def _obstaculos(pagina: pymupdf.Page, lineas: list[dict]) -> list[pymupdf.Rect]:
    """
    Todo lo que ocupa sitio en la pagina: lineas de texto, imagenes y dibujos.

    Sirve para la regla 4: cuanto puede crecer hacia abajo una caja antes de
    pisar lo siguiente.
    """
    # Los marcadores de lista van aparte porque no forman parte de ninguna
    # linea (ver `_quitar_marcador`), pero ocupan sitio igual: si no estan
    # aqui, una caja alineada a la derecha se los come.
    fuera = [l['rect'] for l in lineas] + [l['marca'] for l in lineas if l['marca']]
    for imagen in pagina.get_images(full=True):
        for rect in pagina.get_image_rects(imagen[0]):
            fuera.append(pymupdf.Rect(rect))
    for dibujo in pagina.get_drawings():
        rect = pymupdf.Rect(dibujo['rect'])
        # Las lineas de una rejilla son rectangulos de altura casi cero: no
        # estorban para crecer, pero el borde inferior de una celda si.
        if rect.height >= 0.5 or rect.width >= 0.5:
            fuera.append(rect)
    return fuera


def _sitio_libre_debajo(rect: pymupdf.Rect, obstaculos: list[pymupdf.Rect], limite: float) -> float:
    """Distancia hasta el primer elemento que hay debajo, o hasta `limite`."""
    suelo = limite
    for otro in obstaculos:
        # Esta debajo el que empieza y acaba mas abajo que nosotros. Mirar solo
        # donde empieza no vale: la caja de una linea es mas alta que el paso de
        # una linea a la siguiente, asi que la linea de debajo EMPIEZA por
        # encima de donde acaba esta. Comprobandolo mal, la linea que viene
        # justo detras no contaba como obstaculo, el parrafo se creia con sitio
        # de sobra y la traduccion se escribia encima de ella.
        if otro.y0 <= rect.y0 + 0.5 or otro.y1 <= rect.y1 + 0.5:
            continue
        # Solo estorba lo que esta justo debajo, no lo que hay en otra columna.
        if otro.x1 <= rect.x0 + 0.5 or otro.x0 >= rect.x1 - 0.5:
            continue
        suelo = min(suelo, otro.y0)
    return max(0.0, suelo - rect.y1 - MARGEN)


def _sitio_libre_encima(rect: pymupdf.Rect, obstaculos: list[pymupdf.Rect], limite: float) -> float:
    """Lo mismo, hacia arriba. Lo poco que hay suele bastar (ver `Fragmento.arriba`)."""
    techo = limite
    for otro in obstaculos:
        if otro.y1 >= rect.y1 - 0.5 or otro.y0 >= rect.y0 - 0.5:
            continue
        if otro.x1 <= rect.x0 + 0.5 or otro.x0 >= rect.x1 - 0.5:
            continue
        techo = max(techo, otro.y1)
    return max(0.0, rect.y0 - techo)


def _empieza_fila(linea: dict, lineas: list[dict]) -> bool:
    """
    Si esta linea es la celda de una "tabla sin rayas": tiene compania a su
    derecha o a su izquierda, a la misma altura, y **no llega ni de lejos**
    hasta ella.

    Hace falta porque el hueco vertical no basta. En una ficha de seguridad, la
    columna de etiquetas ("Clasificacion:", "Otros peligros:") va tan junta que
    el corte por hueco las funde en un parrafo, y entonces cada etiqueta se
    traduce con la de al lado y se escribe encima de la fila siguiente. Una
    linea que comparte fila con otra empieza fragmento; las que vienen debajo
    sin compania son su continuacion y se le unen.

    Dos cosas costo entenderlas, y las dos las enseño el mismo documento a dos
    columnas, en el que cada linea salia como un fragmento suelto: se traducian
    de una en una, sin poder reflotar entre ellas, y al crecer la traduccion se
    escribian unas encima de otras.

    La primera: **lo corta que es una linea se mide contra el sitio que tenia,
    no contra el ancho de la pagina**. Contra la pagina, en un documento a dos
    columnas todas las lineas parecen cortas.

    La segunda: **solo cuenta lo que tenga a la DERECHA**. Un vecino a la
    izquierda no le impide a una linea llegar mas lejos, asi que no dice nada
    de si es una etiqueta; y ademas, cuanto mas corto fuera ese vecino, mas
    "sitio libre" parecia haber y mas facil era equivocarse.

    Lo que queda es una regla que se explica sola: una linea que se para mucho
    antes de lo que tiene al lado es una etiqueta; una linea de prosa llena su
    columna hasta el final.
    """
    rect = linea['rect']
    alto = max(rect.height, 1.0)
    for otro in lineas:
        if otro is linea:
            continue
        o = otro['rect']
        if min(rect.y1, o.y1) - max(rect.y0, o.y0) < alto * 0.5:
            continue  # no estan a la misma altura
        if o.x0 < rect.x1 + 3:
            continue  # no esta a su derecha
        if rect.width < 0.6 * (o.x0 - rect.x0):
            return True
    return False


def _columna(rect: pymupdf.Rect, obstaculos: list[pymupdf.Rect],
             contenido: pymupdf.Rect) -> pymupdf.Rect:
    """
    El hueco horizontal en el que vive un texto: hasta el elemento que tenga a
    cada lado, o hasta el margen del documento.

    Hace falta para la regla 5. La caja del propio texto no sirve para deducir
    la alineacion: un titulo centrado empieza y acaba en su propio borde, igual
    que uno alineado a la izquierda. Lo que distingue a uno de otro es el sitio
    que le sobra a cada lado dentro de su columna.
    """
    izquierda, derecha = contenido.x0, contenido.x1
    for otro in obstaculos:
        if otro.y1 <= rect.y0 + 0.5 or otro.y0 >= rect.y1 - 0.5:
            continue  # no esta a la misma altura
        if otro.x1 <= rect.x0 + 0.5:
            izquierda = max(izquierda, otro.x1)
        elif otro.x0 >= rect.x1 - 0.5:
            derecha = min(derecha, otro.x0)
    return pymupdf.Rect(min(izquierda, rect.x0), rect.y0, max(derecha, rect.x1), rect.y1)


def _alineacion(lineas: list[dict], columna: pymupdf.Rect) -> str:
    """
    Regla 5. Con varias lineas se ve en como quedan sus bordes; con una sola, en
    la proporcion entre el sitio que le sobra a cada lado.

    Lo de la proporcion no es un capricho: un titulo puede estar centrado sobre
    un eje que no es el centro de la pagina (en la lista de precios lo esta
    sobre el 447 de 841). Comparar margen izquierdo con margen derecho lo pilla;
    comparar con el centro de la columna, no.
    """
    if len(lineas) > 1:
        izq = [l['rect'].x0 for l in lineas]
        der = [l['rect'].x1 for l in lineas]
        centros = [(l['rect'].x0 + l['rect'].x1) / 2 for l in lineas]
        cuadra = lambda v: max(v) - min(v) < 1.5  # noqa: E731
        if cuadra(izq) and cuadra(der[:-1] or der):
            return 'justify' if len(lineas) > 2 else 'left'
        if cuadra(centros) and not cuadra(izq):
            return 'center'
        if cuadra(der) and not cuadra(izq):
            return 'right'
        if cuadra(izq):
            return 'left'
    rect = lineas[0]['rect']
    for l in lineas[1:]:
        rect |= l['rect']
    margen_izq = max(0.0, rect.x0 - columna.x0)
    margen_der = max(0.0, columna.x1 - rect.x1)
    # Lo que sobra a los lados tiene que ser bastante, no el relleno de una
    # celda: si no, cualquier etiqueta con dos milimetros de aire sale centrada.
    minimo = max(4.0, columna.width * 0.08)
    if margen_izq < minimo and margen_der < minimo:
        return 'left'
    menor, mayor = min(margen_izq, margen_der), max(margen_izq, margen_der)
    if menor > minimo and menor / mayor >= 0.45:
        return 'center'
    if margen_der < margen_izq:
        return 'right'
    return 'left'


def _caja_de_escritura(rect: pymupdf.Rect, columna: pymupdf.Rect,
                       alineacion: str, sitio: float) -> pymupdf.Rect:
    """
    Donde se escribe la traduccion: el texto crece hacia el lado libre y hacia
    abajo, pero **sin moverse de donde estaba**. Un parrafo alineado a la
    izquierda mantiene su margen izquierdo exacto; uno centrado, su eje.
    """
    if alineacion == 'center':
        centro = (rect.x0 + rect.x1) / 2
        semiancho = max(rect.width / 2, min(centro - columna.x0, columna.x1 - centro))
        x0, x1 = centro - semiancho, centro + semiancho
    elif alineacion == 'right':
        x0, x1 = columna.x0, rect.x1
    else:
        x0, x1 = rect.x0, columna.x1
    return pymupdf.Rect(x0, rect.y0, x1, rect.y1 + sitio)


def _interlineado(lineas: list[dict], tam: float) -> float:
    """
    Cada cuanto se repiten las lineas del original, en veces el tamaño de letra.

    Hace falta porque el interlineado estaba puesto a ojo (1,15) y casi ningun
    documento usa ese: los de Word van por 1,27 y los de LibreOffice por 1,17.
    Con el valor equivocado, un parrafo de cuatro lineas acaba tres puntos mas
    arriba o mas abajo de donde estaba, y el de al lado ya no le cuadra.

    Se usa la mediana y no la media porque un parrafo que empieza justo debajo
    de un titulo trae un primer salto que no es el suyo.
    """
    if len(lineas) < 2 or tam <= 0:
        return 0.0
    saltos = [b - a for a, b in zip(
        [l['base'] for l in lineas], [l['base'] for l in lineas[1:]]) if b > a]
    if not saltos:
        return 0.0
    medido = statistics.median(saltos) / tam
    if not INTERLINEADO_MINIMO <= medido <= INTERLINEADO_MAXIMO:
        return 0.0
    return medido


def _interlineado_de_la_pagina(fragmentos: list[Fragmento]) -> None:
    """
    A los fragmentos de una sola linea se les pone el interlineado tipico de la
    pagina: si la traduccion les hace dar el salto a dos lineas, que respiren
    igual que el resto del documento y no como decida MuPDF.
    """
    medidos = [f.interlineado for f in fragmentos if f.interlineado]
    tipico = statistics.median(medidos) if medidos else INTERLINEADO_POR_DEFECTO
    for f in fragmentos:
        if not f.interlineado:
            f.interlineado = tipico


def _sangria(lineas: list[dict], ancho: float) -> float:
    """
    Cuanto van metidas las lineas de continuacion respecto de la primera.

    Es la sangria francesa de toda la vida: el «3.» se queda fuera, a la
    izquierda, y el texto forma columna aparte. Sin reproducirla, la segunda
    linea de cada punto de una lista vuelve al margen del numero y la lista
    deja de leerse como una lista.
    """
    if len(lineas) < 2:
        return 0.0
    dentro = min(l['rect'].x0 for l in lineas[1:])
    hueco = dentro - lineas[0]['rect'].x0
    # Menos de un punto es ruido de medida; mas de un tercio de la caja no es
    # una sangria, es otra cosa que se ha colado en el mismo fragmento.
    return hueco if 1.0 < hueco < ancho / 3 else 0.0


def _fragmento(idf: str, lineas: list[dict], columna: pymupdf.Rect,
               rect: pymupdf.Rect, sitio: float, arriba: float, origen: str) -> Fragmento:
    span = lineas[0]['span']
    texto = ' '.join(l['texto'].strip() for l in lineas).strip()
    texto = re.sub(r'\s+', ' ', texto)
    sangria = _sangria(lineas, rect.width or columna.width)
    # Con sangria francesa los bordes izquierdos no cuadran y el detector de
    # alineacion se cree que es un titulo centrado. Una lista va a la izquierda.
    alineacion = 'left' if sangria else _alineacion(lineas, columna)
    # La caja va a pelo: el aire que hace falta para que `insert_htmlbox` no
    # parta la ultima palabra se le da al escribir, en `_intentos`, para que
    # este en un solo sitio y valga igual para las celdas.
    caja = _caja_de_escritura(rect, columna, alineacion, sitio)
    return Fragmento(
        id=idf,
        texto=texto,
        rect=rect,
        caja=caja,
        columna=columna,
        sitio=sitio,
        arriba=arriba,
        tam=span['size'],
        color=_color(span['color']),
        negrita=_es_negrita(span['flags'], span['font']),
        cursiva=_es_cursiva(span['flags'], span['font']),
        familia=_familia(span['font'], span['flags']),
        alineacion=alineacion,
        origen=origen,
        fuente=span['font'],
        base=lineas[0]['base'],
        interlineado=_interlineado(lineas, span['size']),
        sangria=sangria,
        lineas=len(lineas),
    )


def leer_pagina(pagina: pymupdf.Page, numero: int) -> list[Fragmento]:
    """
    Los fragmentos traducibles de una pagina: primero las celdas de las tablas
    (regla 1) y despues los parrafos de fuera.
    """
    lineas = _lineas_de(pagina)
    obstaculos = _obstaculos(pagina, lineas)
    # El area util del documento: la que ocupa todo lo que hay en la pagina. Da
    # los margenes reales del original, mejor que inventarse unos.
    contenido = pymupdf.Rect(obstaculos[0]) if obstaculos else pymupdf.Rect(pagina.rect)
    for o in obstaculos[1:]:
        contenido |= o
    contenido &= pagina.rect
    limite = pagina.rect.y1 - MARGEN
    fragmentos: list[Fragmento] = []
    n = 0

    # 1. Tablas, celda a celda.
    #
    # `usadas` lleva la cuenta de QUE LINEAS se ha llevado la tabla, por
    # identidad y no por geometria. Antes se volvia a mirar si cada linea caia
    # dentro de alguna celda, y bastaba con que una celda saliera un poco
    # estrecha para que la linea contara como celda en el primer reparto y como
    # parrafo en el segundo: el mismo texto escrito dos veces, uno encima del
    # otro. Salio con una pagina girada 270 grados, pero podia pasar con
    # cualquier tabla que PyMuPDF midiera raro.
    usadas: set[int] = set()
    tablas = pagina.find_tables()
    for tabla in tablas.tables:
        for celda in tabla.cells:
            if celda is None:
                continue
            rect_celda = pymupdf.Rect(celda)
            # Por el CENTRO de la linea, no por su esquina: el borde de una
            # celda cae justo en la esquina superior de la linea de la celda de
            # abajo, y con `contains(tl)` las dos filas se funden en una.
            dentro = [l for l in lineas if rect_celda.contains(_centro(l['rect']))]
            if not dentro:
                continue
            # Se apuntan aunque no haya nada que traducir en ellas: son de la
            # tabla igual, y no pueden volver a salir como parrafo.
            usadas.update(id(l) for l in dentro)
            texto = ' '.join(l['texto'].strip() for l in dentro).strip()
            if not es_traducible(texto):
                continue
            n += 1
            rect = pymupdf.Rect(dentro[0]['rect'])
            for l in dentro[1:]:
                rect |= l['rect']
            # Una celda no crece: rompería la rejilla. Se usa el alto que tiene.
            sitio = max(0.0, rect_celda.y1 - rect.y1 - 1.0)
            arriba = max(0.0, rect.y0 - rect_celda.y0)
            fragmentos.append(_fragmento(f'{numero}-{n}', dentro, rect_celda, rect, sitio, arriba, 'celda'))

    # 2. Parrafos de fuera de las tablas.
    def en_tabla(linea: dict) -> bool:
        return id(linea) in usadas

    grupo: list[dict] = []

    def cerrar() -> None:
        nonlocal n, grupo
        if not grupo:
            return
        texto = ' '.join(l['texto'].strip() for l in grupo).strip()
        if es_traducible(texto):
            n += 1
            rect = pymupdf.Rect(grupo[0]['rect'])
            for l in grupo[1:]:
                rect |= l['rect']
            sitio = _sitio_libre_debajo(rect, obstaculos, limite)
            arriba = _sitio_libre_encima(rect, obstaculos, MARGEN)
            columna = _columna(rect, obstaculos, contenido)
            fragmentos.append(_fragmento(f'{numero}-{n}', grupo, columna, rect, sitio, arriba, 'parrafo'))
        grupo = []

    anterior: dict | None = None
    for linea in lineas:
        if en_tabla(linea):
            cerrar()
            anterior = None
            continue
        if anterior is not None:
            altura = max(anterior['rect'].height, 1.0)
            hueco = linea['rect'].y0 - anterior['rect'].y1
            cambia_tamano = abs(linea['span']['size'] - anterior['span']['size']) > CAMBIO_DE_TAMANO
            otra_columna = linea['rect'].x1 <= anterior['rect'].x0 or linea['rect'].x0 >= anterior['rect'].x1
            if (hueco > HUECO_DE_PARRAFO * altura or cambia_tamano or otra_columna
                    or linea['bloque'] != anterior['bloque']
                    # Un punto de lista empieza parrafo aunque vaya pegado al
                    # anterior, y un cambio de letra (a negrita, a cursiva, a
                    # otra fuente) es un titulillo metido entre medias: si no
                    # se cortan aqui, se traducen juntos y se escriben como un
                    # bloque corrido donde habia una lista.
                    or linea['marcador'] or linea['estilo'] != anterior['estilo']
                    or _empieza_fila(linea, lineas)):
                cerrar()
        grupo.append(linea)
        anterior = linea
    cerrar()

    _alinear_entre_hermanos(fragmentos)
    _interlineado_de_la_pagina(fragmentos)
    return fragmentos


def _alinear_entre_hermanos(fragmentos: list[Fragmento]) -> None:
    """
    Dos fragmentos que empiezan exactamente en la misma x no estan centrados,
    estan alineados a la izquierda con sangria.

    Mirando un fragmento solo no se puede distinguir un titulo centrado de una
    etiqueta sangrada: los dos tienen sitio libre a los dos lados. Mirando a sus
    hermanos, si: una columna de etiquetas comparte el borde izquierdo.
    """
    por_borde: dict[int, list[Fragmento]] = {}
    for f in fragmentos:
        por_borde.setdefault(round(f.rect.x0), []).append(f)
    for grupo in por_borde.values():
        if len(grupo) < 2:
            continue
        for f in grupo:
            if f.alineacion == 'center':
                f.alineacion = 'left'
                f.caja = _caja_de_escritura(f.rect, f.columna, 'left', f.sitio)


# --- Escritura de la pagina ---------------------------------------------------

#: Donde cae la primera linea dentro de su caja, por tamaño e interlineado.
#: Se mide una vez por combinacion y se guarda: ver `_desplazamiento`.
_DESPLAZAMIENTOS: dict[tuple[float, float], float] = {}


def _desplazamiento(tam: float, interlineado: float) -> float:
    """
    Cuanto hay del borde de arriba de la caja a la primera linea base.

    `insert_htmlbox` no escribe pegado al borde: deja el medio interlineado de
    arriba y sube hasta el alto de las mayusculas, todo ello con unas metricas
    fijas suyas que NO son las de la fuente (comprobado: Times, Segoe, Tahoma,
    Courier y Georgia dan exactamente el mismo numero). Por eso se puede medir
    una sola vez por tamaño e interlineado y guardarlo.

    Se mide en vez de calcularse porque es lo unico que no envejece: si MuPDF
    cambia de metricas en una version, aqui se entera solo.
    """
    clave = (round(tam, 2), round(interlineado, 3))
    if clave not in _DESPLAZAMIENTOS:
        _DESPLAZAMIENTOS[clave] = _medir_desplazamiento(*clave)
    return _DESPLAZAMIENTOS[clave]


def _medir_desplazamiento(tam: float, interlineado: float) -> float:
    arriba = 20.0
    try:
        doc = pymupdf.open()
        pagina = doc.new_page(width=600, height=200)
        pagina.insert_htmlbox(
            pymupdf.Rect(20, arriba, 580, 180),
            f'<p style="font-size:{tam:.2f}px;line-height:{interlineado:.3f};margin:0">Hxg</p>',
            scale_low=0)
        for bloque in pagina.get_text('dict')['blocks']:
            for linea in bloque['lines']:
                doc.close()
                return linea['spans'][0]['origin'][1] - arriba
        doc.close()
    except Exception:  # noqa: BLE001 - medir es una mejora, no una condicion
        pass
    # Lo que dio la medida en PyMuPDF 1.27, por si algun dia no se puede medir.
    return 0.8 * tam + 1.0 + (interlineado - 1) * tam / 2


class _Banco:
    """
    Una pagina de usar y tirar donde se prueba a escribir antes de hacerlo de
    verdad.

    Hace falta porque `insert_htmlbox` no deja preguntar: o escribe, o no
    escribe, y en los dos casos te lo cuenta despues. Y hay dos cosas que se
    necesitan saber ANTES de tocar la pagina buena: si el texto cabe en la caja
    del original (para no ensancharla sin motivo y cambiar por donde parten las
    lineas) y cuanto ha habido que encoger la letra (para saber donde va a caer
    la primera linea y corregirlo).

    Se tira una pagina nueva por cada pagina del documento: la de pruebas se va
    llenando de texto que no se usa y no tiene sentido arrastrarla.
    """

    def __init__(self, como: pymupdf.Page) -> None:
        self._doc = pymupdf.open()
        self.pagina = self._doc.new_page(width=como.rect.width, height=como.rect.height)

    def cabe(self, caja: pymupdf.Rect, html: str, minima: float,
             css: str | None, archivo) -> tuple[bool, float]:
        try:
            sobra, escala = self.pagina.insert_htmlbox(
                caja, html, css=css, archive=archivo, scale_low=minima)
        except Exception:  # noqa: BLE001 - un ensayo que peta no tumba la pagina
            return False, 1.0
        return sobra >= 0, escala

    def cerrar(self) -> None:
        self._doc.close()


def escribir_pagina(pagina: pymupdf.Page, fragmentos: list[Fragmento],
                    traducciones: dict[str, str], resultado: Resultado, numero: int) -> None:
    """Borra el texto original y escribe la traduccion en su sitio."""
    utiles = [f for f in fragmentos if traducciones.get(f.id, '').strip()]
    if not utiles:
        return

    # Regla 3: borrar solo el texto. Sin estas dos banderas desaparecen el logo
    # y la rejilla de la tabla.
    for f in utiles:
        pagina.add_redact_annot(f.rect)
    pagina.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
    )

    encogidos = 0
    desbordados = 0
    banco = _Banco(pagina)
    try:
        for f in utiles:
            texto = traducciones[f.id].strip()
            encogido, desbordado = _escribir(pagina, f, texto, banco)
            encogidos += encogido
            desbordados += desbordado
    finally:
        banco.cerrar()
    if encogidos:
        resultado.avisos.append(Aviso(
            pagina=numero,
            texto=f'la página {numero} llevaba mucho texto y {encogidos} '
                  f'{"bloque ha quedado" if encogidos == 1 else "bloques han quedado"} con la letra más pequeña',
        ))
    if desbordados:
        resultado.avisos.append(Aviso(
            pagina=numero,
            texto=f'en la página {numero}, {desbordados} '
                  f'{"bloque no cabía" if desbordados == 1 else "bloques no cabían"} en su sitio y se '
                  f'{"ha" if desbordados == 1 else "han"} salido un poco',
        ))


def _intentos(f: Fragmento) -> list[tuple[pymupdf.Rect, float, float]]:
    """
    Las cajas que se van a probar, de la mas fiel a la menos, con cuanto se
    permite encoger la letra en cada una.

    La primera es la del original, ni un punto mas ancha. **La anchura es lo
    que decide por donde parten las lineas**: con la misma fuente y la misma
    anchura, una traduccion que ocupe lo mismo que el original se parte por el
    mismo sitio y el parrafo queda calcado. Ensanchar la caja hasta la columna,
    que es lo que se hacia siempre, rehacia el parrafo entero aunque la
    traduccion midiera igual.

    Las celdas no pasan por ahi: la caja de una celda YA es su anchura de
    verdad (el texto se partia dentro de la celda, no en el ancho de sus
    letras), y medirla por las letras solo consigue que no quepa por un pelo.

    Encoger la letra va siempre detras de ensanchar la caja: que un parrafo
    baje una linea se nota menos que que salga con la letra mas pequeña que el
    de al lado.
    """
    subir = _hueco_de_linea(f)
    interlineado = f.interlineado or INTERLINEADO_POR_DEFECTO
    holgada = pymupdf.Rect(f.caja.x0 - HOLGURA_LATERAL, f.caja.y0 - subir,
                           f.caja.x1 + HOLGURA_LATERAL, f.caja.y1)
    intentos = []
    if f.origen == 'parrafo':
        exacta = pymupdf.Rect(f.rect.x0 - HOLGURA_LATERAL, f.rect.y0 - subir,
                              f.rect.x1 + HOLGURA_LATERAL, f.rect.y1 + f.sitio)
        intentos.append((exacta, 1.0, interlineado))
    intentos.append((holgada, 1.0, interlineado))
    # Una linea suelta ocupa de alto lo que diga el interlineado aunque no haya
    # segunda linea a la que separar, y por eso un pie de pagina que en el
    # original cabia justo sale encogido. Ahi el interlineado no se ve: se
    # aprieta y no pasa nada. Donde si se veria (dos lineas o mas) no se toca.
    if f.lineas == 1 and interlineado > 1.0:
        intentos.append((holgada, 1.0, 1.0))
    intentos += [(holgada, ESCALA_MINIMA, interlineado), (holgada, 0.0, interlineado)]
    return intentos


def _hueco_de_linea(f: Fragmento) -> float:
    """
    Cuanto se le puede subir el techo a la caja.

    Una caja de texto es mas alta que sus letras: por arriba le sobresale el
    medio interlineado y el hueco que la fuente reserva para los acentos. En el
    original ese hueco estaba ahi, vacio, encima de la primera linea; al volver
    a escribir hay que contar con el, o un bloque de tres lineas no cabe en su
    propio sitio por tres puntos y sale con la letra encogida sin motivo (le
    pasaba al pie de pagina de la lista de precios).

    Se coge lo que hace falta y nunca mas de lo que hay libre encima, pero
    siempre el punto de `SOBRECOSTE`: ese no es hueco del documento, es lo que
    `insert_htmlbox` se guarda para si, y sin el no cabe ni lo que ya cabia.
    """
    falta = _desplazamiento(f.tam, f.interlineado or INTERLINEADO_POR_DEFECTO) - (f.base - f.rect.y0)
    return max(SOBRECOSTE, min(f.arriba, falta))


def _a_su_altura(caja: pymupdf.Rect, f: Fragmento, escala: float,
                 interlineado: float = 0.0) -> pymupdf.Rect:
    """
    Baja o sube la caja para que la primera linea del texto nuevo apoye donde
    apoyaba la del viejo.

    Sin esto, todo el documento sale unos dos puntos mas abajo de donde estaba:
    poco, pero basta para que las viñetas dejen de estar a la altura de su
    linea y para que un pie de pagina se meta en el margen.
    """
    alto = _desplazamiento(f.tam * escala,
                           interlineado or f.interlineado or INTERLINEADO_POR_DEFECTO)
    salto = (f.base - alto) - caja.y0
    # Un tope por si el fragmento trae una linea base rara (texto girado, una
    # formula): mover media letra arriba o abajo es corregir, mas es estropear.
    salto = max(-f.tam, min(f.tam, salto))
    return caja + (0, salto, 0, salto)


def _escribir(pagina: pymupdf.Page, f: Fragmento, texto: str, banco: _Banco) -> tuple[int, int]:
    """
    Escribe un fragmento y dice si hubo que encogerlo o si se salio de su caja.

    Cada intento se prueba primero en el banco, que es una pagina de mentira: de
    ahi salen las dos cosas que no se pueden saber de otra forma, si cabe y
    cuanto se ha encogido la letra. Con eso ya se puede colocar la caja a la
    altura exacta y escribir en la pagina de verdad una sola vez.

    Los intentos van de mas fiel a menos (ver `_intentos`) y, si ninguno cabe,
    queda la caja estirada hacia abajo: es fea y se avisa, pero **perder texto
    no es una opcion**. `insert_htmlbox` no escribe NADA cuando no cabe, y una
    etiqueta que desaparece no se nota hasta que la echa de menos el cliente
    final.
    """
    cara = taller.cara(f.fuente, f.familia, f.negrita, f.cursiva, texto)
    css = taller.css if cara else None
    archivo = taller.archivo if cara else None

    for caja, minima, interlineado in _intentos(f):
        html = f.html(texto, cara, interlineado)
        cabe, escala = banco.cabe(caja, html, minima, css, archivo)
        if not cabe:
            continue
        pagina.insert_htmlbox(_a_su_altura(caja, f, escala, interlineado), html,
                              css=css, archive=archivo, scale_low=minima)
        return (1 if escala < 0.999 else 0), 0

    html = f.html(texto, cara)
    estirada = pymupdf.Rect(f.caja.x0 - HOLGURA_LATERAL, f.caja.y0 - SOBRECOSTE,
                            f.caja.x1 + HOLGURA_LATERAL,
                            min(pagina.rect.y1, f.caja.y0 + max(f.caja.height * 3, 30)))
    _, escala = banco.cabe(estirada, html, 0.0, css, archivo)
    pagina.insert_htmlbox(_a_su_altura(estirada, f, escala), html,
                          css=css, archive=archivo, scale_low=0)
    return 1, 1


# --- La capa de texto ---------------------------------------------------------

#: Gemelos invisibles: caracteres distintos que en la fuente comparten el mismo
#: dibujo, y por cual hay que cambiarlos. No es una lista a ojo, sale de
#: escribir un muestrario con Arial, Times, Calibri y Segoe UI y ver que
#: devolvia cada una (ver el aviso de `_desgemelar`). MuPDF, al dar la vuelta
#: al mapa de la fuente, se queda siempre con el codigo mas alto de los que
#: comparten glifo; aqui se vuelve al de toda la vida, que es el que la gente
#: escribe cuando busca dentro del documento.
_GEMELOS = {
    '00a0': '0020',  # espacio duro          -> espacio
    '00ad': '002d',  # guion blando          -> guion
    '2010': '002d',  # «hyphen»              -> guion
    '2011': '002d',  # guion que no parte    -> guion
    '037e': '003b',  # interrogacion griega  -> punto y coma
    'a78f': '00b7',  # punto sinologico      -> punto medio
    '2219': '00b7',  # punto de operador     -> punto medio
    'fd3e': '0028',  # parentesis ornamental -> parentesis
    'fd3f': '0029',
}

_BLOQUE_RANGOS = re.compile(rb'\d+\s+beginbfrange(.*?)endbfrange', re.DOTALL)
_BLOQUE_CHARS = re.compile(rb'\d+\s+beginbfchar(.*?)endbfchar', re.DOTALL)
_TRIO = re.compile(rb'<([0-9a-fA-F]{2,8})>\s*<([0-9a-fA-F]{2,8})>\s*<([0-9a-fA-F]{2,8})>')
_PAREJA = re.compile(rb'<([0-9a-fA-F]{2,8})>\s*<([0-9a-fA-F]{2,8})>')


def _desgemelar(doc: pymupdf.Document) -> None:
    """
    Deja la capa de texto con espacios y guiones de verdad.

    Cuando se escribe con una fuente incrustada, MuPDF apunta en el PDF que el
    glifo del espacio es un espacio duro (U+00A0) y que el del guion es un
    guion blando (U+00AD). No es un error suyo: en Arial, en Liberation y en
    casi todas, esos dos pares comparten glifo, y al dar la vuelta al mapa le
    toca elegir. El documento se ve perfecto —el dibujo es el bueno—, pero al
    copiar el texto o buscar dentro, «White-yellowish» ya no aparece.

    Asi que aqui se vuelve a escribir esa tabla (la `ToUnicode` de cada fuente)
    cambiando los gemelos invisibles por el caracter de siempre. Va despues de
    subconjuntar, porque subconjuntar la reescribe.
    """
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, 'Type')[1] != '/Font':
                continue
            clase, valor = doc.xref_get_key(xref, 'ToUnicode')
            if clase != 'xref':
                continue
            destino = int(valor.split()[0])
            crudo = doc.xref_stream(destino)
        except Exception:  # noqa: BLE001 - una fuente rara no tumba un trabajo
            continue
        arreglado = _arreglar_mapa(crudo)
        if arreglado is not None:
            try:
                doc.update_stream(destino, arreglado)
            except Exception:  # noqa: BLE001
                pass


def _arreglar_mapa(crudo: bytes) -> bytes | None:
    """La tabla ya arreglada, o None si no habia nada que tocar."""
    if not any(g.encode() in crudo.lower() for g in _GEMELOS):
        return None
    salida = _BLOQUE_RANGOS.sub(_reescribir_rangos, crudo)
    salida = _BLOQUE_CHARS.sub(_reescribir_chars, salida)
    return salida if salida != crudo else None


def _reescribir_rangos(bloque: re.Match[bytes]) -> bytes:
    """
    Saca aparte los caracteres gemelos que caen dentro de un rango.

    Un rango dice «estos glifos seguidos son estos caracteres seguidos», asi
    que para cambiar uno solo hay que partirlo. Lo que no hace falta partir se
    vuelve a juntar, para no dejar la tabla con mil entradas de una.
    """
    cuerpo = bloque.group(1)
    trios = [(a.decode(), b.decode(), c.decode()) for a, b, c in _TRIO.findall(cuerpo)]
    # Si en el bloque hay algo que no sea un trio (un destino en forma de lista,
    # que el formato tambien admite), no se toca: mas vale dejarlo como estaba.
    if not trios or _TRIO.sub(b'', cuerpo).strip():
        return bloque.group(0)
    nuevos: list[tuple[str, ...]] = []
    for desde, hasta, primero in trios:
        if len(primero) != 4:
            nuevos.append((desde, hasta, primero))
            continue
        d, h, p = int(desde, 16), int(hasta, 16), int(primero, 16)
        for i in range(h - d + 1):
            codigo = f'{p + i:04x}'
            nuevos.append((f'{d + i:04x}', f'{d + i:04x}', _GEMELOS.get(codigo, codigo)))
        _juntar(nuevos)
    return _bloque(nuevos, 'bfrange')


def _juntar(entradas: list[tuple[str, ...]]) -> None:
    """Vuelve a pegar los rangos que se han partido y no hacia falta partir."""
    while len(entradas) >= 2:
        (d1, h1, p1), (d2, h2, p2) = entradas[-2], entradas[-1]
        if len(p1) != 4 or len(p2) != 4:
            return
        if int(h1, 16) + 1 != int(d2, 16) or int(p1, 16) + (int(h1, 16) - int(d1, 16)) + 1 != int(p2, 16):
            return
        entradas[-2:] = [(d1, h2, p1)]


def _reescribir_chars(bloque: re.Match[bytes]) -> bytes:
    cuerpo = bloque.group(1)
    parejas = [(a.decode(), b.decode()) for a, b in _PAREJA.findall(cuerpo)]
    if not parejas or _PAREJA.sub(b'', cuerpo).strip():
        return bloque.group(0)
    return _bloque([(a, _GEMELOS.get(b.lower(), b)) for a, b in parejas], 'bfchar')


def _bloque(entradas: list[tuple[str, ...]], nombre: str) -> bytes:
    """Los monta en trozos de 100, que es el maximo que admite el formato."""
    partes = []
    for i in range(0, len(entradas), 100):
        trozo = entradas[i:i + 100]
        cuerpo = ''.join('<' + '> <'.join(e) + '>\n' for e in trozo)
        partes.append(f'{len(trozo)} begin{nombre}\n{cuerpo}end{nombre}')
    return '\n'.join(partes).encode()


def guardar(doc: pymupdf.Document) -> bytes:
    """Regla 6 del plan: subconjuntar las fuentes divide el tamaño por 200."""
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001 - subset_fonts es opcional, nunca debe tumbar un trabajo
        pass
    _desgemelar(doc)
    return doc.tobytes(garbage=3, deflate=True)
