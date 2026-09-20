"""
Cliente de traduccion.

Este servicio **no habla con ningun modelo de lenguaje**. Pide las traducciones
a /api/tools/mt de la propia web, firmando con el vale que el Worker emitio al
empezar el trabajo. Asi la clave de Google no sale nunca de Cloudflare, el
Worker lleva la cuenta del gasto diario y se puede cambiar de motor sin tocar
este contenedor.

El contrato es una peticion por pagina:

    POST /api/tools/mt
    {"vale": "...", "origen": "tr", "destino": "es",
     "textos": {"3-1": "...", "3-2": "..."},
     "imagen": "<base64 png>"}          # solo en el camino de escaneados

    200 {"ok": true, "traducciones": {"3-1": "...", ...}, "motor": "gemini-..."}
"""

from __future__ import annotations

import time

import httpx

#: Un fallo del traductor no debe tumbar el trabajo entero: se reintenta y, si
#: no hay manera, la pagina se queda en su idioma original y se avisa.
INTENTOS = 3
ESPERA = 1.5


class ErrorDeTraduccion(RuntimeError):
    pass


class Traductor:
    def __init__(self, base: str, vale: str, cliente: httpx.Client | None = None) -> None:
        self.url = f'{base.rstrip("/")}/api/tools/mt'
        self.vale = vale
        self.http = cliente or httpx.Client(timeout=120.0)
        self.motor: str | None = None
        self.peticiones = 0

    def traducir(self, textos: dict[str, str], origen: str, destino: str,
                 imagen: str | None = None) -> dict[str, str]:
        """Traduce los textos de UNA pagina. Devuelve {id: traduccion}."""
        if not textos:
            return {}
        cuerpo: dict[str, object] = {
            'vale': self.vale,
            'origen': origen,
            'destino': destino,
            'textos': textos,
        }
        if imagen:
            cuerpo['imagen'] = imagen

        ultimo = ''
        for intento in range(INTENTOS):
            try:
                r = self.http.post(self.url, json=cuerpo)
                if r.status_code == 200:
                    datos = r.json()
                    self.motor = datos.get('motor') or self.motor
                    self.peticiones += 1
                    salida = datos.get('traducciones') or {}
                    # El modelo puede inventarse ids o perder alguno: lo que no
                    # vuelva se queda como estaba, que es mejor que un hueco.
                    return {k: v for k, v in salida.items() if k in textos and isinstance(v, str)}
                # 429 y 5xx: merece la pena reintentar; el resto, no.
                ultimo = f'{r.status_code} {r.text[:200]}'
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            except httpx.HTTPError as err:  # noqa: PERF203
                ultimo = str(err)
            time.sleep(ESPERA * (intento + 1))
        raise ErrorDeTraduccion(ultimo or 'sin respuesta del traductor')
