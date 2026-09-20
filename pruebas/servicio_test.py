"""
Pruebas del servicio: lo que sale por el cable mientras se traduce.

No tocan la red ni gastan cuota (el traductor es de mentira) y no abren
ningun navegador. Lo que comprueban es el **contrato del progreso**, que es de
lo que vive la barra: que se cuente varias veces por pagina, que nunca vaya
hacia atras, que se avise antes de cerrar el documento y que al final vuelva el
PDF. Si esto se rompe, la barra del navegador vuelve a dar saltos y nadie se
entera hasta que lo dice el cliente.

Comprueba tambien el camino del escaneado, que es un generador dentro de otro
(`yield from`): ahi lo facil es perder los avisos por el camino. El OCR de
verdad solo esta en el contenedor, asi que aqui se finge; lo que se prueba es
el engarce, no Tesseract.

    python traductor/pruebas/servicio_test.py [carpeta-con-PDF-del-cliente]

Necesita starlette, que es lo unico que servicio.py importa de mas. Si no esta,
la prueba lo dice y no falla.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, extra: str = '') -> None:
    global fallos  # noqa: PLW0603
    print(f'{"OK  " if ok else "FALLO"} {nombre}{" · " + extra if extra else ""}')
    if not ok:
        fallos += 1


try:
    import servicio
except ModuleNotFoundError as err:
    print(f'(no se puede probar el servicio aqui: falta {err.name}; '
          f'se instala con `pip install starlette`)')
    raise SystemExit(0) from None

from motor import Aviso  # noqa: E402
from simulacro import documento_de_prueba, simular  # noqa: E402


class TraductorDeMentira:
    """Alarga el texto un 15 %, que es lo que crece el turco."""

    motor = 'falso'
    peticiones = 0

    def traducir(self, textos, origen, destino, imagen=None):  # noqa: ANN001, ARG002
        self.peticiones += 1
        return {k: simular(v, 'largo') for k, v in textos.items()}


def correr(datos: bytes, ocr: bool = False) -> list[dict]:
    servicio.Traductor = lambda base, vale: TraductorDeMentira()  # noqa: ARG005
    par = {'origen': 'en', 'destino': 'es', 'vale': 'x', 'base': servicio.BASES[0], 'ocr': ocr}
    return [json.loads(linea) for linea in servicio.trabajo(datos, par)]


DATOS = documento_de_prueba()
PAGINAS = pymupdf.open(stream=DATOS, filetype='pdf').page_count

# --- El camino normal -------------------------------------------------------

sucesos = correr(DATOS)
tipos = [s['tipo'] for s in sucesos]
progreso = [s['hechas'] for s in sucesos if s['tipo'] == 'progreso']

check('lo primero que se manda es el inicio', tipos[0] == 'inicio', tipos[0])
check('el inicio dice cuantas paginas son', sucesos[0].get('paginas') == PAGINAS)
check('se cuenta varias veces por pagina', len(progreso) >= PAGINAS * 2,
      f'{len(progreso)} avisos para {PAGINAS} página(s)')
check('el progreso nunca va hacia atras', progreso == sorted(progreso), str(progreso))
check('el progreso acaba en el total de paginas', progreso[-1] == PAGINAS, str(progreso[-1]))
check('ningun progreso se pasa del total', all(p <= PAGINAS for p in progreso))
check('se avisa antes de cerrar el documento',
      'guardando' in tipos and tipos.index('guardando') < tipos.index('fin'))
check('lo ultimo es el fin', tipos[-1] == 'fin', tipos[-1])
check('y trae el PDF traducido', bool(sucesos[-1].get('pdf')))
check('y cuanto ha tardado', isinstance(sucesos[-1].get('segundos'), (int, float)))
check('no se queda ninguna pagina sin traducir', sucesos[-1]['paginasSinTraducir'] == 0)

# --- El camino del escaneado (el OCR se finge) --------------------------------

def ocr_falso(pagina, numero, traductor, origen, destino):  # noqa: ANN001, ARG001
    yield 0.2
    yield 0.9
    return [Aviso(numero, f'aviso de la página {numero}')]


de_verdad = servicio.traducir_escaneada
servicio.traducir_escaneada = ocr_falso
try:
    sucesos = correr(DATOS, ocr=True)
finally:
    servicio.traducir_escaneada = de_verdad

progreso = [s['hechas'] for s in sucesos if s['tipo'] == 'progreso']
check('el escaneado tambien cuenta por dentro', len(progreso) >= PAGINAS * 2, str(progreso))
check('el escaneado tambien acaba en el total', progreso[-1] == PAGINAS)
check('el inicio avisa de que va con OCR', sucesos[0].get('ocr') is True)
# Lo que de verdad se prueba aqui: `yield from` devuelve lo que el generador
# devuelve al terminar, y es facil perderlo sin que falle nada.
check('los avisos del OCR llegan hasta el final',
      sucesos[-1]['avisos'] == [f'aviso de la página {n}' for n in range(1, PAGINAS + 1)],
      str(sucesos[-1]['avisos']))

# --- Lo que se rechaza antes de empezar ----------------------------------------

sucesos = correr(b'esto no es un PDF')
check('un fichero que no es PDF se rechaza con un error claro',
      sucesos[0]['tipo'] == 'error' and 'PDF' in sucesos[0]['texto'], sucesos[0].get('texto', ''))

protegido = pymupdf.open(stream=DATOS, filetype='pdf').tobytes(
    encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='secreta', owner_pw='secreta')
sucesos = correr(protegido)
# Estos tres mensajes se usaban sin estar definidos: un PDF con contraseña
# reventaba con un NameError y el usuario veia un 500 pelado.
check('un PDF con contraseña se explica, no revienta',
      sucesos[0]['tipo'] == 'error' and 'contraseña' in sucesos[0]['texto'],
      sucesos[0].get('texto', ''))
check('y se dice como arreglarlo', 'sin protección' in sucesos[0]['texto'])
check('el mensaje de demasiadas paginas se puede construir',
      str(servicio.MAX_PAGINAS) in servicio._demasiadas_paginas(999))
check('y el de un fichero demasiado grande tambien',
      'MB' in servicio._pesa_demasiado(999 * 1024 * 1024))

print('\nTodo bien' if fallos == 0 else f'\n{fallos} fallo(s)')
sys.exit(0 if fallos == 0 else 1)
