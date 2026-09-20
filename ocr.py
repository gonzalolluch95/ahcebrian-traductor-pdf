"""
El camino del escaneado.

Es un motor distinto al de motor.py, no una variante: en un escaneado el texto
original esta DENTRO de la imagen, asi que no se puede borrar, solo tapar. El
resultado nunca sera igual de limpio que el original y la herramienta lo dice
con todas las letras antes de empezar.

    1. rasterizar la pagina a 250 ppp
    2. OCR con Tesseract (con el paquete del idioma, no el de serie)
    3. descartar lo que NO se debe traducir: siglas, sellos, marcas de agua
    4. el modelo recibe la IMAGEN de la pagina y el texto del OCR, y devuelve el
       texto corregido y traducido
    5. tapar cada caja con el color dominante de su borde
    6. escribir la traduccion encima

El paso 4 es el que salva el resultado: al ver la imagen, el modelo arregla lo
que el OCR leyo mal. Donde el OCR lee "yrarlogekoydugunu", el modelo ve la
palabra y entiende "yürürlüğe koyduğunu".

Tesseract (Apache-2.0) y no el modelo de serie de rapidocr: probado sobre el
un certificado ISO 9001 turco escaneado, el de serie (chino + ingles) se come TODAS las
diacriticas turcas.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass

import pymupdf

from motor import Aviso, es_traducible

#: Resolucion de rasterizado. A menos, el OCR falla; a mas, la memoria se
#: dispara y no compensa.
PPP = 250
#: Confianza minima de Tesseract para hacer caso a una palabra.
CONFIANZA = 45

#: Nuestro codigo de idioma -> paquete de Tesseract.
TESSERACT = {
    'tr': 'tur', 'es': 'spa', 'en': 'eng', 'pt': 'por', 'fr': 'fra',
    'de': 'deu', 'zh': 'chi_sim', 'ja': 'jpn',
}

#: Marcas de agua de escaner que no se traducen jamas.
_BASURA = re.compile(r'scanned with|camscanner|cam ?scanner|adobe scan', re.I)
#: Siglas: tres letras o menos en mayusculas (BEC, IAF, ISO, TSE).
_SIGLA = re.compile(r'^[A-ZÇĞİÖŞÜ0-9./-]{1,3}$')


@dataclass
class Trozo:
    id: str
    texto: str
    rect: pymupdf.Rect          # en puntos de PDF
    caja_px: tuple[int, int, int, int]
    alto: float


def _lineas_del_ocr(datos: dict, escala: float, numero: int) -> list[Trozo]:
    """Junta las palabras de Tesseract en lineas, con su caja."""
    lineas: dict[tuple, list[int]] = {}
    for i, texto in enumerate(datos['text']):
        if not texto.strip():
            continue
        try:
            confianza = float(datos['conf'][i])
        except (TypeError, ValueError):
            continue
        if confianza < CONFIANZA:
            continue
        clave = (datos['block_num'][i], datos['par_num'][i], datos['line_num'][i])
        lineas.setdefault(clave, []).append(i)

    trozos: list[Trozo] = []
    for n, (_, indices) in enumerate(sorted(lineas.items()), start=1):
        palabras = [datos['text'][i].strip() for i in indices]
        texto = ' '.join(p for p in palabras if p)
        x0 = min(datos['left'][i] for i in indices)
        y0 = min(datos['top'][i] for i in indices)
        x1 = max(datos['left'][i] + datos['width'][i] for i in indices)
        y1 = max(datos['top'][i] + datos['height'][i] for i in indices)
        trozos.append(Trozo(
            id=f'{numero}-{n}',
            texto=texto,
            rect=pymupdf.Rect(x0 * escala, y0 * escala, x1 * escala, y1 * escala),
            caja_px=(x0, y0, x1, y1),
            alto=(y1 - y0) * escala,
        ))
    return trozos


def _se_traduce(trozo: Trozo) -> bool:
    """Regla 3: fuera siglas, sellos, marcas de agua y numeros."""
    texto = trozo.texto.strip()
    if _BASURA.search(texto):
        return False
    if _SIGLA.match(texto):
        return False
    return es_traducible(texto)


def _color_dominante(pix, caja_px: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """
    El color del borde de la caja, que es el del fondo sobre el que esta el
    texto. Se toma la mediana de una tira de pixeles por encima y por debajo:
    asi un sello de colores no tiñe el parche de un texto que tiene al lado.
    """
    x0, y0, x1, y1 = caja_px
    muestras: list[tuple[int, int, int]] = []
    ancho, alto = pix.width, pix.height
    for y in (max(0, y0 - 2), min(alto - 1, y1 + 2)):
        for x in range(max(0, x0), min(ancho, x1), max(1, (x1 - x0) // 12 or 1)):
            try:
                muestras.append(pix.pixel(x, y)[:3])
            except (IndexError, ValueError):
                continue
    if not muestras:
        return (1.0, 1.0, 1.0)
    canal = lambda i: sorted(m[i] for m in muestras)[len(muestras) // 2]  # noqa: E731
    return (canal(0) / 255, canal(1) / 255, canal(2) / 255)


def _hueco(trozo: Trozo, todos: list[Trozo], pagina: pymupdf.Rect) -> pymupdf.Rect:
    """
    El rectangulo que se puede tapar sin comerse lo de al lado: hasta el
    siguiente trozo a cada lado y hasta la siguiente linea por abajo.
    """
    r = trozo.rect
    izquierda, derecha = max(pagina.x0, r.x0 - 3), min(pagina.x1, r.x1 + 24)
    arriba, abajo = r.y0 - 1.5, r.y1 + 2.5
    for otro in todos:
        if otro is trozo:
            continue
        o = otro.rect
        mismo_renglon = min(r.y1, o.y1) - max(r.y0, o.y0) > min(r.height, o.height) * 0.4
        if mismo_renglon:
            if o.x1 <= r.x0:
                izquierda = max(izquierda, o.x1 + 1)
            elif o.x0 >= r.x1:
                derecha = min(derecha, o.x0 - 1)
        elif o.x1 > r.x0 and o.x0 < r.x1:  # justo encima o justo debajo
            if o.y1 <= r.y0:
                arriba = max(arriba, o.y1 + 0.5)
            elif o.y0 >= r.y1:
                abajo = min(abajo, o.y0 - 0.5)
    return pymupdf.Rect(min(izquierda, r.x0), min(arriba, r.y0),
                        max(derecha, r.x1), max(abajo, r.y1))


def _sobre_fondo_de_color(color: tuple[float, float, float]) -> bool:
    """Un sello o un logo: fondo muy saturado. Ahi no se toca nada."""
    return max(color) - min(color) > 0.25 or max(color) < 0.45


def traducir_escaneada(pagina: pymupdf.Page, numero: int, traductor, origen: str,
                       destino: str):
    """
    Traduce una pagina escaneada sobre la propia imagen.

    Es un **generador**: va soltando por donde va, de 0 a 1 dentro de esta
    pagina, y al terminar devuelve los avisos (`avisos = yield from ...`). Lo
    es porque cada uno de sus tres pasos tarda lo suyo —rasterizar, pasar el
    OCR y esperar al modelo— y una pagina escaneada puede irse a medio minuto:
    si no cuenta nada por el camino, al usuario se le queda la barra parada y
    cree que se ha roto.
    """
    import pytesseract
    from PIL import Image

    pix = pagina.get_pixmap(dpi=PPP)
    escala = 72.0 / PPP
    imagen_png = pix.tobytes('png')
    idioma_ocr = TESSERACT.get(origen, 'eng')
    if idioma_ocr != 'eng':
        idioma_ocr = f'{idioma_ocr}+eng'  # los documentos tecnicos mezclan ingles
    yield 0.20  # rasterizada

    datos = pytesseract.image_to_data(
        Image.open(io.BytesIO(imagen_png)),
        lang=idioma_ocr,
        output_type=pytesseract.Output.DICT,
    )
    trozos = _lineas_del_ocr(datos, escala, numero)
    yield 0.55  # leida

    utiles: list[Trozo] = []
    colores: dict[str, tuple[float, float, float]] = {}
    for t in trozos:
        if not _se_traduce(t):
            continue
        color = _color_dominante(pix, t.caja_px)
        if _sobre_fondo_de_color(color):
            continue  # sello, logo o cabecera de color: se deja como esta
        colores[t.id] = color
        utiles.append(t)

    if not utiles:
        return [Aviso(numero, f'en la página {numero} no se ha reconocido texto que traducir')]
    yield 0.62  # limpia

    # El modelo ve la pagina entera: es lo que le deja corregir al OCR.
    miniatura = pagina.get_pixmap(dpi=110).tobytes('png')
    traducciones = traductor.traducir(
        {t.id: t.texto for t in utiles}, origen, destino,
        imagen=base64.b64encode(miniatura).decode('ascii'),
    )
    yield 0.90  # traducida

    encogidos = 0
    for t in utiles:
        texto = (traducciones.get(t.id) or '').strip()
        if not texto:
            continue
        # Hasta donde se puede tapar sin comerse lo de al lado. Importa mas aqui
        # que en un PDF con texto: como el original no se borra, lo que se salga
        # del parche se lee ENCIMA de lo que habia. Un parche que llega hasta el
        # siguiente trozo evita la mayoria de esos solapes.
        hueco = _hueco(t, trozos, pagina.rect)
        pagina.draw_rect(hueco, color=None, fill=colores[t.id], width=0)
        alto = max(6.0, t.alto)
        html = (
            f'<p style="font-family:sans-serif;font-size:{alto * 0.78:.1f}px;'
            f'margin:0;line-height:1.05;color:#111">{texto}</p>'
        )
        caja = pymupdf.Rect(hueco.x0 + 0.5, hueco.y0, hueco.x1 - 0.5, hueco.y1)
        sobra, escala_usada = pagina.insert_htmlbox(caja, html, scale_low=0.6)
        if sobra < 0:
            pagina.insert_htmlbox(caja, html, scale_low=0)
            encogidos += 1
        elif escala_usada < 0.999:
            encogidos += 1

    avisos = []
    if encogidos:
        avisos.append(Aviso(numero, f'en la página {numero}, {encogidos} '
                                    f'{"línea ha quedado" if encogidos == 1 else "líneas han quedado"} '
                                    'con la letra más pequeña'))
    return avisos
