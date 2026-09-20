"""
Con que documentos de verdad se mide, y por que sus nombres no estan aqui.

Las pruebas del motor valen lo que valgan los documentos con los que se
prueban, y los buenos son los del cliente: certificados turcos, fichas de
seguridad, listas de precios. Pero **este codigo es publico** (lo obliga la
AGPL de PyMuPDF) y los nombres de esos ficheros no tienen por que serlo: dicen
con quien trabaja la empresa y que le compra.

Asi que la lista vive en `corpus.txt`, al lado de este fichero, y esa lista no
se publica. Sin ella las pruebas siguen corriendo: se saltan los documentos de
verdad y se quedan con el sintetico, que se construye en `simulacro.py` y viene
en el propio codigo.

El formato de `corpus.txt` es una linea por documento:

    etiqueta | ruta relativa a la carpeta del proyecto

Una almohadilla empieza un comentario y las lineas en blanco se ignoran. La
etiqueta `escaneado` marca el que NO tiene capa de texto, que es el que sirve
para probar que el veredicto lo reconoce.
"""

from __future__ import annotations

import pathlib

#: Donde se busca la lista. No se publica: ver la explicacion de arriba.
LISTA = pathlib.Path(__file__).parent / 'corpus.txt'

#: La etiqueta del documento sin capa de texto.
ESCANEADO = 'escaneado'


def _lineas() -> list[tuple[str, str]]:
    if not LISTA.exists():
        return []
    salida = []
    for linea in LISTA.read_text(encoding='utf-8').splitlines():
        limpia = linea.split('#', 1)[0].strip()
        if not limpia or '|' not in limpia:
            continue
        etiqueta, ruta = limpia.split('|', 1)
        salida.append((etiqueta.strip(), ruta.strip()))
    return salida


def documentos(raiz: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Los documentos con texto que estan a mano, con su etiqueta."""
    return [(raiz / ruta, etiqueta) for etiqueta, ruta in _lineas()
            if etiqueta != ESCANEADO and (raiz / ruta).exists()]


def escaneado(raiz: pathlib.Path) -> pathlib.Path | None:
    """El documento sin capa de texto, si esta a mano."""
    for etiqueta, ruta in _lineas():
        if etiqueta == ESCANEADO and (raiz / ruta).exists():
            return raiz / ruta
    return None


def aviso(raiz: pathlib.Path) -> str | None:
    """Que decir cuando no hay ninguno, para no dejar al que mira a oscuras."""
    if documentos(raiz) or escaneado(raiz):
        return None
    if not LISTA.exists():
        return (f'(no hay lista de documentos en {LISTA.name}: solo se ha probado con el '
                f'documento sintetico. Ver pruebas/corpus.py)')
    return f'(ninguno de los documentos de {LISTA.name} esta en {raiz})'
