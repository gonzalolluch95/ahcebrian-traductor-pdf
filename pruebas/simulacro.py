"""
Prueba el motor sobre un PDF real SIN gastar cuota de traduccion.

El "traductor" de aqui no traduce: alarga el texto un 15 % (lo que crece el
turco) o lo sustituye por chino, que es el caso duro para las fuentes. Sirve
para mirar el resultado, que es lo unico que dice si el diseño aguanta.

    python traductor/pruebas/simulacro.py "ruta/al.pdf" [largo|chino|igual]

Deja el PDF resultante y un PNG de cada pagina, antes y despues, en
traductor/pruebas/out/.
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from motor import Resultado, escribir_pagina, guardar, leer_pagina  # noqa: E402

OUT = pathlib.Path(__file__).parent / 'out'
CHINO = '本文件为测试用途所生成的中文文本内容质量检验'


def simular(texto: str, modo: str) -> str:
    if modo == 'igual':
        return texto
    if modo == 'chino':
        n = max(2, round(len(texto) / 2.2))
        return (CHINO * (n // len(CHINO) + 1))[:n]
    # 15 % mas largo, respetando las palabras.
    objetivo = round(len(texto) * 1.15)
    palabras = texto.split(' ')
    salida = list(palabras)
    i = 0
    while len(' '.join(salida)) < objetivo and i < 400:
        salida.append(palabras[i % len(palabras)])
        i += 1
    return ' '.join(salida)


# --- Un documento de mentira con todo lo que importa --------------------------
#
# Vive aqui, y no en una prueba concreta, porque lo usan varias: es el unico
# documento con el que estas pruebas se pueden ejecutar en cualquier ordenador,
# sin depender de que esten a mano los PDF del cliente.

def documento_de_prueba() -> bytes:
    """Titulo centrado, dos parrafos, una tabla con rejilla y una imagen."""
    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_htmlbox(
        pymupdf.Rect(60, 50, 535, 90),
        '<p style="font-family:sans-serif;font-size:20px;text-align:center;margin:0">'
        '<b>Informe de ensayo</b></p>')
    pagina.insert_htmlbox(
        pymupdf.Rect(60, 110, 535, 150),
        '<p style="font-family:sans-serif;font-size:11px;margin:0">'
        'Este documento recoge los resultados del ensayo realizado sobre la muestra '
        'recibida en el laboratorio.</p>')
    # Una tabla de 3 x 3 con sus rayas, que es lo que PyMuPDF detecta como tabla.
    x0, y0, ancho, alto = 60, 180, 150, 22
    filas = [['Parametro', 'Resultado', 'Unidad'],
             ['Humedad', '5,2', '%'],
             ['Proteina', '11,4', 'g/100 g']]
    for f, fila in enumerate(filas):
        for c, valor in enumerate(fila):
            celda = pymupdf.Rect(x0 + c * ancho, y0 + f * alto, x0 + (c + 1) * ancho, y0 + (f + 1) * alto)
            pagina.draw_rect(celda, color=(0, 0, 0), width=0.7)
            pagina.insert_htmlbox(
                celda + (3, 3, -3, -3),
                f'<p style="font-family:sans-serif;font-size:10px;margin:0">{valor}</p>')
    pagina.insert_htmlbox(
        pymupdf.Rect(60, 280, 535, 320),
        '<p style="font-family:sans-serif;font-size:11px;margin:0">'
        'Los resultados se refieren exclusivamente a la muestra ensayada.</p>')
    # Un "logo": un rectangulo de color hecho imagen.
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
    pix.set_rect(pix.irect, (200, 60, 30))
    pagina.insert_image(pymupdf.Rect(480, 40, 520, 80), pixmap=pix)
    return doc.tobytes()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    origen = pathlib.Path(sys.argv[1])
    modo = sys.argv[2] if len(sys.argv) > 2 else 'largo'
    OUT.mkdir(exist_ok=True)
    nombre = origen.stem[:40].replace(' ', '_')

    doc = pymupdf.open(origen)
    resultado = Resultado()
    total = 0
    t0 = time.time()
    for numero, pagina in enumerate(doc, start=1):
        pagina.get_pixmap(dpi=110).save(OUT / f'{nombre}-{numero}-antes.png')
        fragmentos = leer_pagina(pagina, numero)
        total += len(fragmentos)
        traducciones = {f.id: simular(f.texto, modo) for f in fragmentos}
        escribir_pagina(pagina, fragmentos, traducciones, resultado, numero)
        celdas = sum(1 for f in fragmentos if f.origen == 'celda')
        print(f'  pagina {numero}: {len(fragmentos)} fragmentos ({celdas} de tabla)')

    datos = guardar(doc)
    destino = OUT / f'{nombre}-{modo}.pdf'
    destino.write_bytes(datos)
    hecho = pymupdf.open(destino)
    for numero, pagina in enumerate(hecho, start=1):
        pagina.get_pixmap(dpi=110).save(OUT / f'{nombre}-{numero}-despues.png')

    print(f'{total} fragmentos, {len(datos) / 1024:.0f} KB, {time.time() - t0:.1f} s -> {destino}')
    for aviso in resultado.avisos:
        print(f'  aviso: {aviso.texto}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
