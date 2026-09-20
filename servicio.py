"""
La aplicacion web del servicio, separada de app.py (que es solo el envoltorio
de Modal).

Es **Starlette pelado, no FastAPI**, y no por gusto: con FastAPI (0.115.12 con
pydantic 2.13 en la imagen de Modal), una ruta `async def analizar(request:
Request)` respondia 422 "query.request field required" a todas las peticiones,
tanto declarada arriba como dentro de una funcion. Starlette no tiene inyeccion
de dependencias: al manejador siempre le llega la peticion y punto. Aqui no se
usaba ni una sola cosa de FastAPI (no hay validacion de modelos ni documentacion
automatica), asi que no se pierde nada.

Este modulo solo se importa dentro del contenedor, que es donde estan pymupdf y
starlette.
"""

import base64
import json
import time

import pymupdf
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from analisis import analizar as radiografia
from idiomas import detectar
from motor import Resultado, escribir_pagina, guardar, leer_pagina
from mt import ErrorDeTraduccion, Traductor
from ocr import traducir_escaneada

#: A quien se le permite pedir traducciones. El servicio llama de vuelta a la
#: web para traducir, asi que la direccion no puede venir libre en la peticion.
BASES = ('https://ahcebrian.com', 'https://www.ahcebrian.com')

#: Limites del plan. Mas alla de esto se rechaza antes de empezar, en vez de
#: dejar al usuario esperando media hora.
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGINAS = 60
MAX_PAGINAS_OCR = 40
#: Se procesa por bloques para soltar la memoria de las paginas ya hechas.
BLOQUE = 15
#: Por debajo de esta confianza, el idioma detectado no se preselecciona.
SEGURIDAD = 0.8


#: Lo que se le dice al usuario cuando el PDF viene con contraseña. Con el
#: camino de salida, que es lo unico que le sirve.
PROTEGIDO = ('El PDF está protegido con contraseña. Ábrelo, guárdalo sin protección '
             'y vuelve a subirlo.')


def _pesa_demasiado(bytes_: int) -> str:
    return (f'Este PDF pesa {round(bytes_ / 1024 / 1024)} MB; '
            f'el límite son {MAX_BYTES // (1024 * 1024)} MB.')


def _demasiadas_paginas(paginas: int) -> str:
    return (f'Este documento tiene {paginas} páginas; el límite son '
            f'{MAX_PAGINAS} ({MAX_PAGINAS_OCR} si es un escaneado).')


def _suceso(**datos) -> str:
    return json.dumps(datos, ensure_ascii=False) + '\n'


def _pagina(doc, numero: int, par: dict, traductor: Traductor, resultado: Resultado):
    """
    Una pagina, contando por donde va.

    Suelta fracciones de 0 a 1 **dentro de esta pagina**. No es un lujo: lo que
    tarda una pagina es casi todo la espera al modelo, y si solo se contara al
    acabarla, la barra estaria parada el 80 % del tiempo y daria la sensacion de
    haberse colgado. Contando los pasos, se mueve tres veces por pagina.
    """
    pagina = doc[numero - 1]
    if par['ocr']:
        resultado.avisos.extend(
            (yield from traducir_escaneada(pagina, numero, traductor, par['origen'], par['destino'])))
        return
    fragmentos = leer_pagina(pagina, numero)
    yield 0.25  # leida
    traducciones = traductor.traducir(
        {f.id: f.texto for f in fragmentos}, par['origen'], par['destino'])
    yield 0.85  # traducida
    escribir_pagina(pagina, fragmentos, traducciones, resultado, numero)


def trabajo(datos: bytes, par: dict):
    """
    El trabajo, contado linea a linea segun avanza.

    Es un generador y no una funcion que devuelve el PDF al final porque de eso
    depende que no haya corte por tiempo: la respuesta empieza en el primer
    segundo (el suceso `inicio`) y la conexion sigue viva mientras se trabaja.
    """
    t0 = time.time()
    try:
        doc = pymupdf.open(stream=datos, filetype='pdf')
    except Exception:  # noqa: BLE001
        yield _suceso(tipo='error', texto='Solo se pueden subir ficheros PDF')
        return
    if doc.needs_pass:
        yield _suceso(tipo='error', texto=PROTEGIDO)
        return

    total = doc.page_count
    tope = MAX_PAGINAS_OCR if par['ocr'] else MAX_PAGINAS
    if total > tope:
        yield _suceso(tipo='error', texto=_demasiadas_paginas(total))
        return

    traductor = Traductor(par['base'], par['vale'])
    resultado = Resultado()
    sin_traducir = 0
    yield _suceso(tipo='inicio', paginas=total, ocr=par['ocr'])

    for primera in range(0, total, BLOQUE):
        for numero in range(primera + 1, min(primera + BLOQUE, total) + 1):
            try:
                for parte in _pagina(doc, numero, par, traductor, resultado):
                    yield _suceso(tipo='progreso', pagina=numero, de=total,
                                  hechas=round(numero - 1 + parte, 4))
            except ErrorDeTraduccion as err:
                sin_traducir += 1
                yield _suceso(tipo='aviso', texto=f'la página {numero} se ha quedado sin traducir ({err})')
            yield _suceso(tipo='progreso', pagina=numero, de=total, hechas=numero)

    # Cerrar el documento tarda lo suyo en uno largo (hay que subconjuntar las
    # fuentes), asi que tambien se avisa: es el ultimo tramo de la barra.
    yield _suceso(tipo='guardando')
    try:
        salida = guardar(doc)
    except Exception as err:  # noqa: BLE001
        yield _suceso(tipo='error', texto=f'No se ha podido cerrar el documento: {err}')
        return
    finally:
        doc.close()

    yield _suceso(
        tipo='fin',
        pdf=base64.b64encode(salida).decode('ascii'),
        motor=traductor.motor,
        peticiones=traductor.peticiones,
        paginasSinTraducir=sin_traducir,
        segundos=round(time.time() - t0, 1),
        avisos=[a.texto for a in resultado.avisos],
    )


def parametros(request: Request) -> dict:
    q = request.query_params
    return {
        'origen': q.get('origen', 'auto'),
        'destino': q.get('destino', 'es'),
        'vale': q.get('vale', ''),
        'base': q.get('base', BASES[0]),
        'ocr': q.get('ocr', '') == '1',
    }


async def analizar(request: Request) -> JSONResponse:
    """
    Que documento es esto: paginas, idioma y si tiene capa de texto.

    Se llama nada mas elegir el fichero, antes de preguntar idiomas, porque
    de esto depende la siguiente pantalla (el 44 % de los PDF del cliente
    son escaneados y no pueden quedar iguales).
    """
    datos = await request.body()
    if not datos:
        return JSONResponse({'ok': False, 'error': 'No ha llegado ningún documento'}, 400)
    if len(datos) > MAX_BYTES:
        return JSONResponse({'ok': False, 'error': _pesa_demasiado(len(datos))}, 413)
    try:
        doc = pymupdf.open(stream=datos, filetype='pdf')
    except Exception:  # noqa: BLE001
        return JSONResponse({'ok': False, 'error': 'Solo se pueden subir ficheros PDF'}, 415)
    if doc.needs_pass:
        return JSONResponse({'ok': False, 'error': PROTEGIDO}, 422)

    informe = radiografia(doc, tamano=len(datos))
    doc.close()

    escaneado = informe['veredicto'] == 'escaneado'
    limite = MAX_PAGINAS_OCR if escaneado else MAX_PAGINAS
    idioma, confianza = detectar(informe.pop('muestra'))
    return JSONResponse({
        'ok': True,
        **informe,
        'escaneado': escaneado,
        'mixto': informe['veredicto'] == 'mixto',
        'idioma': idioma,
        'confianza': confianza,
        # Con poca confianza no se preselecciona idioma: la deteccion es una
        # sugerencia, no una imposicion.
        'seguro': confianza >= SEGURIDAD,
        'limite': limite,
        'demasiadasPaginas': informe['paginas'] > limite,
    })


async def traducir(request: Request):
    """Traduce el documento y va contando por donde va (NDJSON)."""
    par = parametros(request)
    if par['base'] not in BASES:
        return JSONResponse({'ok': False, 'error': 'Origen no permitido'}, 403)
    if not par['vale']:
        return JSONResponse({'ok': False, 'error': 'Falta el vale de traducción'}, 401)
    datos = await request.body()
    if not datos:
        return JSONResponse({'ok': False, 'error': 'No ha llegado ningún documento'}, 400)
    if len(datos) > MAX_BYTES:
        return JSONResponse({'ok': False, 'error': _pesa_demasiado(len(datos))}, 413)
    return StreamingResponse(trabajo(datos, par), media_type='application/x-ndjson')


async def despertar(request: Request) -> JSONResponse:
    """
    Para el ping que lanza /tools al abrirse: arranca el contenedor en frio
    (6,4 s) mientras el usuario todavia esta eligiendo el fichero.
    """
    return JSONResponse({'ok': True})


def crear() -> Starlette:
    return Starlette(routes=[
        Route('/analizar', analizar, methods=['POST']),
        Route('/traducir', traducir, methods=['POST']),
        Route('/despertar', despertar, methods=['GET']),
    ])
