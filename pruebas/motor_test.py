"""
Pruebas del motor. No tocan la red ni gastan cuota: el "traductor" es una
funcion que alarga el texto un 15 %, que es lo que crece el turco.

    python traductor/pruebas/motor_test.py [carpeta-con-PDF-del-cliente]

Sin carpeta, prueba solo con el documento sintetico que se construye aqui
(tabla, parrafos, titulo centrado y una imagen), que es el que garantiza que
esto se puede ejecutar en cualquier sitio. Si se le pasa la carpeta de los
documentos del cliente, comprueba ademas los de verdad, que son los que
enseñaron las siete reglas.

Termina con «Todo bien» o con el numero de fallos.
"""

from __future__ import annotations

import pathlib
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from analisis import analizar  # noqa: E402
from motor import (  # noqa: E402
    Resultado, es_traducible, escribir_pagina, guardar, leer_pagina,
)
import corpus  # noqa: E402
from simulacro import documento_de_prueba, simular  # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, extra: str = '') -> None:
    global fallos  # noqa: PLW0603
    print(f'{"OK  " if ok else "FALLO"} {nombre}{" · " + extra if extra else ""}')
    if not ok:
        fallos += 1


def traducir_todo(datos: bytes, modo: str = 'largo') -> tuple[bytes, Resultado, list]:
    doc = pymupdf.open(stream=datos, filetype='pdf')
    resultado = Resultado()
    todos = []
    for numero, pagina in enumerate(doc, start=1):
        fragmentos = leer_pagina(pagina, numero)
        todos.extend(fragmentos)
        escribir_pagina(pagina, fragmentos,
                        {f.id: simular(f.texto, modo) for f in fragmentos}, resultado, numero)
    return guardar(doc), resultado, todos


def normaliza(texto: str) -> str:
    """
    Deshace las ligaduras antes de comparar.

    PyMuPDF extrae "Classiﬁcation" con la ligadura ﬁ en un solo caracter, y al
    volver a escribirla con otra fuente sale como dos letras. Sin normalizar,
    una comprobacion de "no se ha perdido texto" da falsos positivos en
    cualquier documento hecho con Word.
    """
    return unicodedata.normalize('NFKC', texto)


def cuenta(datos: bytes) -> dict:
    doc = pymupdf.open(stream=datos, filetype='pdf')
    return {
        'paginas': doc.page_count,
        'imagenes': sum(len(p.get_images(full=True)) for p in doc),
        'dibujos': sum(len(p.get_drawings()) for p in doc),
        'texto': normaliza('\n'.join(p.get_text('text') for p in doc)),
    }


# --- Regla 2: que no se traduce -------------------------------------------------

for texto, esperado in [
    ('56,72', False), ('2025/02WP', False), ('01.06.2025', False), ('%', False),
    ('9,0 - 14,9 mm', False), ('€/Kg', False), ('', False), ('  ', False),
    ('mesh size', True), ('Nylon ® twines', True), ('DATE', True), ('pH', True),
]:
    check(f'no traducible: {texto!r}' if not esperado else f'traducible: {texto!r}',
          es_traducible(texto) is esperado)

# --- El documento sintetico ------------------------------------------------------

original = documento_de_prueba()
antes = cuenta(original)
salida, resultado, fragmentos = traducir_todo(original)
despues = cuenta(salida)

check('el documento no cambia de páginas', antes['paginas'] == despues['paginas'])
check('la imagen (el logo) sigue ahí', despues['imagenes'] == antes['imagenes'],
      f"{antes['imagenes']} -> {despues['imagenes']}")
check('la rejilla de la tabla sigue ahí', despues['dibujos'] >= antes['dibujos'],
      f"{antes['dibujos']} -> {despues['dibujos']}")
check('se detectan las celdas de la tabla', any(f.origen == 'celda' for f in fragmentos),
      f"{sum(1 for f in fragmentos if f.origen == 'celda')} celdas")
check('los números de la tabla no se tocan', '5,2' in despues['texto'] and '11,4' in despues['texto'])
check('el título centrado se reconoce como centrado',
      any(f.alineacion == 'center' and 'Informe' in f.texto for f in fragmentos),
      ' | '.join(f'{f.alineacion}:{f.texto[:18]}' for f in fragmentos[:4]))
check('los párrafos del cuerpo quedan a la izquierda',
      all(f.alineacion in ('left', 'justify') for f in fragmentos if 'exclusivamente' in f.texto))
check('la traducción se escribe', 'Los resultados' in despues['texto'])
check('cada fragmento tiene su caja dentro de la página',
      all(f.caja.x0 >= -1 and f.caja.y0 >= -1 for f in fragmentos))
check('las cajas no se pisan entre sí',
      all((a.caja & b.caja).get_area() < 0.3 * min(a.caja.get_area(), b.caja.get_area())
          for i, a in enumerate(fragmentos) for b in fragmentos[i + 1:]))

# El chino sirve para dos cosas: comprobar la regla 6 (subconjuntar las fuentes)
# y ver que el texto original se ha BORRADO de verdad, cosa que con una
# traduccion simulada en el mismo idioma no se puede distinguir.
chino, _, _ = traducir_todo(original, 'chino')
texto_chino = cuenta(chino)['texto']
check('con chino, el PDF no se dispara de tamaño', len(chino) < 1_000_000, f'{len(chino) // 1024} KB')
check('con chino, el texto se escribe', '本' in texto_chino)
check('el texto original ya no está',
      'Los resultados se refieren' not in ' '.join(texto_chino.split()))

# --- Paginas giradas ----------------------------------------------------------------
#
# Llegan de los escaneres y de cualquier hoja de calculo apaisada. Con el texto
# igual, el resultado tiene que leerse igual: ni una palabra de menos (se ha
# perdido) ni una de mas (se ha escrito dos veces). Lo de "una de mas" no es
# rebuscado: con 270 grados, PyMuPDF media una celda un poco estrecha, la misma
# linea contaba como celda y como parrafo, y el texto salia duplicado encima de
# si mismo.
for giro in (0, 90, 180, 270):
    girado = pymupdf.open(stream=original, filetype='pdf')
    girado[0].set_rotation(giro)
    datos = girado.tobytes()
    salida, _, _ = traducir_todo(datos, 'igual')
    antes_txt = normaliza(cuenta(datos)['texto']).split()
    despues_txt = normaliza(cuenta(salida)['texto']).split()
    sobran = [p for p in despues_txt if despues_txt.count(p) > antes_txt.count(p)]
    faltan = [p for p in antes_txt if despues_txt.count(p) < antes_txt.count(p)]
    check(f'girada {giro}°: no se pierde nada', not faltan, ' '.join(faltan[:5]))
    check(f'girada {giro}°: no se escribe nada dos veces', not sobran, ' '.join(sobran[:5]))
    check(f'girada {giro}°: la pagina sigue girada igual',
          pymupdf.open(stream=salida, filetype='pdf')[0].rotation == giro)

# --- El veredicto: que documento es este -------------------------------------------

def escaneada(paginas: int = 1, con_texto: int = 0) -> bytes:
    """Un PDF "escaneado": paginas que son una foto de lado a lado."""
    doc = pymupdf.open()
    for n in range(paginas):
        pagina = doc.new_page(width=595, height=842)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85))
        pix.set_rect(pix.irect, (240, 240, 235))
        pagina.insert_image(pagina.rect, pixmap=pix)
        if n < con_texto:
            pagina.insert_htmlbox(
                pymupdf.Rect(60, 60, 535, 300),
                '<p style="font-family:sans-serif;font-size:11px;margin:0">'
                + ('Esta página sí tiene una capa de texto de verdad, con bastantes '
                   'caracteres como para que el análisis la dé por buena. ' * 3)
                + '</p>')
    return doc.tobytes()


informe = analizar(pymupdf.open(stream=original, filetype='pdf'), tamano=len(original))
check('veredicto: un PDF con texto es traducible', informe['veredicto'] == 'traducible', informe['veredicto'])
check('veredicto: sin firma digital, no se avisa de firma', informe['firmado'] is False)
check('veredicto: cuenta bien las páginas', informe['paginas'] == 1 and informe['paginasConTexto'] == 1)
check('veredicto: saca muestra para adivinar el idioma', 'ensayo' in informe['muestra'])

informe = analizar(pymupdf.open(stream=escaneada(3), filetype='pdf'))
check('veredicto: todo fotos es un escaneado', informe['veredicto'] == 'escaneado', informe['veredicto'])
check('escaneado: ninguna página con texto', informe['paginasConTexto'] == 0)
check('escaneado: se dicen cuáles son', informe['paginasSinTexto'] == [1, 2, 3], str(informe['paginasSinTexto']))

informe = analizar(pymupdf.open(stream=escaneada(4, con_texto=3), filetype='pdf'))
check('veredicto: unas con texto y otras no es mixto', informe['veredicto'] == 'mixto', informe['veredicto'])
check('mixto: se dice qué página se queda sin traducir', informe['paginasSinTexto'] == [4],
      str(informe['paginasSinTexto']))
check('mixto: con una de cuatro, sigue siendo fiable', informe['fiable'] is True)

informe = analizar(pymupdf.open(stream=escaneada(4, con_texto=1), filetype='pdf'))
check('mixto: con tres de cuatro en blanco, ya no es fiable', informe['fiable'] is False)

protegido = pymupdf.open(stream=original, filetype='pdf').tobytes(
    encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='secreta', owner_pw='secreta')
informe = analizar(pymupdf.open(stream=protegido, filetype='pdf'))
check('veredicto: un PDF con contraseña es «protegido»', informe['veredicto'] == 'protegido', informe['veredicto'])

# --- Los documentos del cliente, si estan a mano ----------------------------------

# Cuales son sale de `corpus.txt`, que no se publica: ver pruebas/corpus.py.
raiz = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[3]
for fichero, nombre in corpus.documentos(raiz):
    datos = fichero.read_bytes()
    antes = cuenta(datos)
    salida, resultado, fragmentos = traducir_todo(datos)
    despues = cuenta(salida)
    check(f'{nombre}: mismas páginas', antes['paginas'] == despues['paginas'])
    check(f'{nombre}: las imágenes siguen', despues['imagenes'] == antes['imagenes'],
          f"{antes['imagenes']} -> {despues['imagenes']}")
    check(f'{nombre}: los dibujos siguen', despues['dibujos'] >= antes['dibujos'] * 0.95,
          f"{antes['dibujos']} -> {despues['dibujos']}")
    check(f'{nombre}: se traduce algo', len(fragmentos) > 5, f'{len(fragmentos)} fragmentos')
    perdidos = [f.texto for f in fragmentos
                if len(f.texto) >= 3 and f.texto.split()
                and normaliza(f.texto.split()[0]) not in despues['texto']]
    check(f'{nombre}: no se pierde texto por el camino', not perdidos,
          ' | '.join(t[:40] for t in perdidos[:3]))
    informe = analizar(pymupdf.open(stream=datos, filetype='pdf'), tamano=len(datos))
    check(f'{nombre}: el veredicto es «traducible»', informe['veredicto'] == 'traducible', informe['veredicto'])

# Y un escaneado de verdad, que es el caso que mas se va a dar.
escaneado_real = corpus.escaneado(raiz)
if escaneado_real:
    datos = escaneado_real.read_bytes()
    informe = analizar(pymupdf.open(stream=datos, filetype='pdf'), tamano=len(datos))
    check('escaneado real: se reconoce como escaneado', informe['veredicto'] == 'escaneado', informe['veredicto'])
    check('escaneado real: ninguna página con texto', informe['paginasConTexto'] == 0)

aviso = corpus.aviso(raiz)
if aviso:
    print(aviso)

print('\nTodo bien' if fallos == 0 else f'\n{fallos} fallo(s)')
sys.exit(0 if fallos == 0 else 1)
