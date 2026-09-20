"""
Cuanto se parece el PDF traducido al original. Un numero, no una impresion.

La idea es tonta y por eso funciona: se «traduce» el documento dejando el texto
EXACTAMENTE igual. Si el motor fuera perfecto, la salida seria el original
pixel por pixel. Todo lo que se separe de cero es cosa del motor —la fuente que
ha elegido, donde ha puesto la linea base, como ha partido los parrafos—, y no
de la traduccion, que en esta prueba no existe.

    python traductor/pruebas/fidelidad.py [carpeta-con-los-PDF]

Por cada documento dice que porcentaje de la pagina sale distinto y cuantos
bloques ha habido que encoger. Al final, la media. Sirve para dos cosas: ver si
un cambio en el motor mejora o empeora, y no fiarse de que «se ve bien».

No hace falta red ni cuota. Usa numpy, que aqui esta para mirar, no para el
servicio: el contenedor de Modal no lo lleva.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy  # noqa: E402
import pymupdf  # noqa: E402

import corpus  # noqa: E402
from motor import Resultado, escribir_pagina, guardar, leer_pagina  # noqa: E402

#: A que resolucion se comparan. 120 es suficiente para ver un punto de
#: desplazamiento y no tan alto como para que el antialias mande.
DPI = 120
#: Cuanto tiene que cambiar un pixel para contarlo. Por debajo de esto es el
#: suavizado de los bordes de las letras, que nunca sale igual dos veces.
UMBRAL = 40

def gris(pagina: pymupdf.Page) -> numpy.ndarray:
    pix = pagina.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    return numpy.frombuffer(pix.samples, dtype=numpy.uint8).reshape(pix.height, pix.width)


def distintos(a: numpy.ndarray, b: numpy.ndarray) -> float:
    """Que parte de la pagina ha cambiado, de 0 a 1."""
    if a.shape != b.shape:
        return 1.0
    return float((numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)) > UMBRAL).mean())


def pisadas(doc: pymupdf.Document) -> int:
    """
    Cuantas lineas del resultado se escriben encima de otra.

    Es la otra mitad de la prueba, y la que de verdad duele: la diferencia de
    pixeles se mide con el texto igual, pero una traduccion ocupa mas, y lo que
    hay que saber es si al crecer se come a la de al lado. Una linea pisada no
    es un defecto estetico: es texto que no se puede leer.
    """
    total = 0
    for pagina in doc:
        cajas = []
        for bloque in pagina.get_text('dict')['blocks']:
            if bloque['type'] != 0:
                continue
            for linea in bloque['lines']:
                if ''.join(s['text'] for s in linea['spans']).strip():
                    cajas.append(pymupdf.Rect(linea['bbox']))
        for i, a in enumerate(cajas):
            for b in cajas[i + 1:]:
                corte = a & b
                if corte.is_valid and corte.get_area() > 0.45 * min(a.get_area(), b.get_area()):
                    total += 1
    return total


def con_traduccion(ruta: pathlib.Path, alarga) -> int:
    """Las lineas pisadas cuando el texto crece, que es lo que pasa de verdad."""
    doc = pymupdf.open(ruta)
    resultado = Resultado()
    for numero, pagina in enumerate(doc, start=1):
        fragmentos = leer_pagina(pagina, numero)
        escribir_pagina(pagina, fragmentos,
                        {f.id: alarga(f.texto) for f in fragmentos}, resultado, numero)
    return pisadas(pymupdf.open(stream=guardar(doc), filetype='pdf'))


def medir(ruta: pathlib.Path) -> tuple[float, int, int] | None:
    """Diferencia media, bloques encogidos y bloques desbordados."""
    doc = pymupdf.open(ruta)
    if doc.needs_pass:
        return None
    antes = [gris(p) for p in doc]
    resultado = Resultado()
    hubo_texto = False
    for numero, pagina in enumerate(doc, start=1):
        fragmentos = leer_pagina(pagina, numero)
        hubo_texto = hubo_texto or bool(fragmentos)
        escribir_pagina(pagina, fragmentos, {f.id: f.texto for f in fragmentos}, resultado, numero)
    if not hubo_texto:
        return None  # un escaneado: aqui no se mide nada
    hecho = pymupdf.open(stream=guardar(doc), filetype='pdf')
    despues = [gris(p) for p in hecho]
    diferencias = [distintos(a, b) for a, b in zip(antes, despues)]
    encogidos = sum(1 for a in resultado.avisos if 'más pequeña' in a.texto)
    desbordados = sum(1 for a in resultado.avisos if 'salido' in a.texto)
    return sum(diferencias) / len(diferencias), encogidos, desbordados


def main() -> int:
    raiz = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from simulacro import simular  # noqa: PLC0415 - solo hace falta aqui

    medidas, lineas_pisadas = [], 0
    print('         texto igual            texto un 15 % mas largo')
    # Cuales son sale de `corpus.txt`, que no se publica: ver corpus.py.
    for fichero, _ in corpus.documentos(raiz):
        medida = medir(fichero)
        if medida is None:
            continue
        diferencia, encogidos, _ = medida
        pisa = con_traduccion(fichero, lambda t: simular(t, 'largo'))
        medidas.append(diferencia)
        lineas_pisadas += pisa
        print(f'{diferencia * 100:6.2f} % distinto   {pisa:3} líneas pisadas    '
              f'{fichero.name[:44]}{f"  ({encogidos} pág. encogidas)" if encogidos else ""}')
    if not medidas:
        print(corpus.aviso(raiz) or 'No hay ningun documento con texto que medir.')
        print(f'Se buscan dentro de: {raiz}')
        return 2
    print(f'\n{sum(medidas) / len(medidas) * 100:6.2f} % de media   {lineas_pisadas:3} líneas pisadas en total'
          f'   ({len(medidas)} documentos; lo ideal es 0 y 0)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
