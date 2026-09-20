"""
Traduce un PDF de verdad, de punta a punta, contra el Worker en local.

Es la prueba que de verdad cierra el circulo: motor de Python -> /api/tools/mt
del Worker -> Gemini -> PDF traducido. Usa el mismo camino y el mismo prompt que
en produccion; lo unico que no pasa por aqui es Modal (el motor corre en esta
maquina, que para mirar el resultado da igual).

    npx wrangler dev                    # en otra terminal, desde web/
    python traductor/pruebas/real.py "ruta/al.pdf" es

Gasta cuota de Gemini: una peticion por pagina. No la lances en bucle.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from motor import Resultado, escribir_pagina, guardar, leer_pagina  # noqa: E402
from mt import Traductor  # noqa: E402

BASE = 'http://localhost:8787'
OUT = pathlib.Path(__file__).parent / 'out'


def secreto() -> str:
    """TOOLS_SECRET de web/.dev.vars, que es lo que usa `wrangler dev`."""
    fichero = pathlib.Path(__file__).resolve().parents[2] / '.dev.vars'
    for linea in fichero.read_text(encoding='utf-8').splitlines():
        if linea.startswith('TOOLS_SECRET='):
            return linea.split('=', 1)[1].strip()
    raise SystemExit('Falta TOOLS_SECRET en web/.dev.vars')


def vale(paginas: int, origen: str, destino: str) -> str:
    """Un vale con el mismo formato que firma worker/tools.ts."""
    def b64(datos: bytes) -> str:
        return base64.urlsafe_b64encode(datos).decode().rstrip('=')

    cuerpo = b64(json.dumps({
        'uid': 'prueba', 'paginas': paginas, 'origen': origen, 'destino': destino,
        'exp': int(time.time() * 1000) + 30 * 60 * 1000,
    }, separators=(',', ':')).encode())
    firma = hmac.new(secreto().encode(), cuerpo.encode(), hashlib.sha256).digest()
    return f'{cuerpo}.{b64(firma)}'


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    origen_pdf = pathlib.Path(sys.argv[1])
    destino = sys.argv[2] if len(sys.argv) > 2 else 'es'
    idioma_origen = sys.argv[3] if len(sys.argv) > 3 else 'auto'
    OUT.mkdir(exist_ok=True)
    nombre = origen_pdf.stem[:40].replace(' ', '_')

    doc = pymupdf.open(origen_pdf)
    traductor = Traductor(BASE, vale(doc.page_count, idioma_origen, destino))
    resultado = Resultado()
    t0 = time.time()
    for numero, pagina in enumerate(doc, start=1):
        fragmentos = leer_pagina(pagina, numero)
        traducciones = traductor.traducir({f.id: f.texto for f in fragmentos}, idioma_origen, destino)
        escribir_pagina(pagina, fragmentos, traducciones, resultado, numero)
        print(f'  página {numero}: {len(fragmentos)} fragmentos, {len(traducciones)} traducidos')

    datos = guardar(doc)
    salida = OUT / f'{nombre}-{destino}.pdf'
    salida.write_bytes(datos)
    hecho = pymupdf.open(salida)
    for numero, pagina in enumerate(hecho, start=1):
        pagina.get_pixmap(dpi=110).save(OUT / f'{nombre}-{numero}-{destino}.png')

    print(f'{traductor.peticiones} peticiones a {traductor.motor}, '
          f'{len(datos) / 1024:.0f} KB, {time.time() - t0:.1f} s -> {salida}')
    for aviso in resultado.avisos:
        print(f'  aviso: {aviso.texto}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
