"""
Que documento es este: el veredicto que decide la siguiente pantalla.

Se mira antes de preguntar nada, en cuanto el usuario elige el fichero, porque
de esto depende todo lo demas: **14 de los 32 PDF distintos del cliente son
escaneados sin capa de texto**. La herramienta se va a encontrar con uno muy a
menudo, asi que no vale con un "no se puede": hay que decir que se ha mirado,
cuantas paginas y por que.

Tres senales por pagina y cuatro veredictos:

    caracteres < 100                 -> la pagina no tiene capa de texto
    imagenes cubren mas del 80 %     -> la pagina es una foto
    paginas sin texto > 30 %         -> documento mixto poco fiable

    protegido   el PDF pide contrasena
    escaneado   ninguna pagina tiene texto
    mixto       unas si y otras no (se dice cuales)
    traducible  adelante

No depende de Modal ni de la red: solo de PyMuPDF, para poder probarlo en
cualquier ordenador.
"""

from __future__ import annotations

import pymupdf

#: Menos caracteres que esto en una pagina y no hay capa de texto que traducir.
MINIMO_CARACTERES = 100
#: Por encima de esta parte de la pagina cubierta de imagen, es una foto.
MAXIMO_IMAGEN = 0.80
#: Mas paginas sin texto que esta parte del total y el documento no es fiable.
MAXIMO_SIN_TEXTO = 0.30


def _parte_cubierta_por_imagenes(pagina: pymupdf.Page) -> float:
    """Cuanta superficie de la pagina ocupan sus imagenes, de 0 a 1."""
    superficie = pagina.rect.get_area()
    if superficie <= 0:
        return 0.0
    cubierto = 0.0
    for imagen in pagina.get_images(full=True):
        for rect in pagina.get_image_rects(imagen[0]):
            cubierto += (pymupdf.Rect(rect) & pagina.rect).get_area()
    return min(1.0, cubierto / superficie)


def analizar(doc: pymupdf.Document, tamano: int = 0) -> dict:
    """
    Radiografia del documento, lista para enviar al navegador.

    `muestra` es el texto con el que se adivina el idioma; se devuelve aparte
    para que quien llame decida si lo pasa por el detector.
    """
    if doc.needs_pass:
        return {
            'veredicto': 'protegido',
            'paginas': doc.page_count,
            'paginasConTexto': 0,
            'paginasSinTexto': [],
            'fiable': False,
            'firmado': False,
            'tamano': tamano,
            'muestra': '',
        }

    sin_texto: list[int] = []
    con_texto = 0
    muestra: list[str] = []
    for numero, pagina in enumerate(doc, start=1):
        texto = pagina.get_text('text').strip()
        es_foto = _parte_cubierta_por_imagenes(pagina) > MAXIMO_IMAGEN
        if len(texto) < MINIMO_CARACTERES or (es_foto and len(texto) < MINIMO_CARACTERES * 3):
            sin_texto.append(numero)
            continue
        con_texto += 1
        if len(' '.join(muestra)) < 4000:
            muestra.append(texto)

    total = doc.page_count
    if con_texto == 0:
        veredicto = 'escaneado'
    elif sin_texto:
        veredicto = 'mixto'
    else:
        veredicto = 'traducible'

    return {
        'veredicto': veredicto,
        'paginas': total,
        'paginasConTexto': con_texto,
        # Las paginas que se quedaran en el idioma original, para poder decirlo
        # con nombre y apellidos: "las paginas 3, 7 y 8 son imagenes".
        'paginasSinTexto': sin_texto[:40],
        'fiable': len(sin_texto) <= MAXIMO_SIN_TEXTO * total,
        # Una firma digital se pierde al tocar el documento: se avisa antes.
        'firmado': doc.get_sigflags() > 0,
        'tamano': tamano,
        'muestra': ' '.join(muestra),
    }
