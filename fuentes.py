"""
Con que fuente se vuelve a escribir un texto traducido.

El motor borra el texto original y lo escribe otra vez con `insert_htmlbox`,
que no sabe nada del PDF: escribe con la fuente que le diga el CSS. Hasta aqui
se le decia `sans-serif` o `serif` a secas, y el resultado era que un documento
en Arial salia en Helvetica y uno en Calibri en algo que no era Calibri. Con
texto identico, el original y la traduccion no se parecian: cambiaba la letra,
la anchura de cada palabra y, con ella, por donde partian las lineas.

Aqui se arregla en dos pasos:

 1. **La fuente incrustada no vale.** Se podria sacar del propio PDF con
    `extract_font`, pero casi siempre viene subconjuntada y SIN tabla de
    caracteres: solo tiene los glifos que el documento usaba, numerados, y no
    hay forma de pedirle una «n» por su codigo. Comprobado con los PDF del
    cliente: `valid_codepoints()` devuelve cero. Ese camino esta cerrado.

 2. **Sustitucion metricamente compatible.** Liberation Sans mide exactamente
    lo mismo que Arial, letra por letra; Liberation Serif, lo mismo que Times
    New Roman; Carlito, que Calibri; Caladea, que Cambria. No son parecidas:
    son intercambiables, y por eso las lineas parten por el mismo sitio. Es lo
    que hace LibreOffice cuando abre un .docx sin las fuentes de Microsoft.

Si la fuente que sale no tiene alguno de los caracteres del texto traducido
(turco, chino, japones, arabe...), aqui se devuelve `None` y el motor vuelve al
comportamiento de siempre, que para esos alfabetos ya funcionaba: dejar que
`insert_htmlbox` elija una fuente que tenga los glifos. Mas vale una letra
distinta que un cuadrado vacio.

No depende de nada salvo PyMuPDF, y funciona igual en el contenedor (fuentes de
Debian) que en un Windows o un Mac cualquiera, porque busca por nombre de
fichero en las carpetas de fuentes del sistema.
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass

import pymupdf

#: Donde busca el sistema sus fuentes. La primera que existe manda.
_CARPETAS = (
    '/usr/share/fonts',
    '/usr/local/share/fonts',
    os.path.expanduser('~/.fonts'),
    os.path.expanduser('~/.local/share/fonts'),
    'C:/Windows/Fonts',
    '/Library/Fonts',
    '/System/Library/Fonts',
    os.path.expanduser('~/Library/Fonts'),
)

#: Las cuatro variantes de una familia, en el orden en que se nombran aqui.
REGULAR, NEGRITA, CURSIVA, NEGRITA_CURSIVA = range(4)

#: Juegos de ficheros por familia, del mas fiel al menos.
#:
#: El primero de cada lista es siempre el clon metricamente compatible, que es
#: el que hay en el contenedor; el segundo, la fuente de verdad, que es la que
#: hay en un Windows. Asi el resultado es el mismo en los dos sitios.
_JUEGOS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    'arial': (
        ('liberationsans-regular', 'liberationsans-bold', 'liberationsans-italic', 'liberationsans-bolditalic'),
        ('arimo-regular', 'arimo-bold', 'arimo-italic', 'arimo-bolditalic'),
        ('arial', 'arialbd', 'ariali', 'arialbi'),
        ('dejavusans', 'dejavusans-bold', 'dejavusans-oblique', 'dejavusans-boldoblique'),
    ),
    'times': (
        ('liberationserif-regular', 'liberationserif-bold', 'liberationserif-italic', 'liberationserif-bolditalic'),
        ('tinos-regular', 'tinos-bold', 'tinos-italic', 'tinos-bolditalic'),
        ('times', 'timesbd', 'timesi', 'timesbi'),
        ('dejavuserif', 'dejavuserif-bold', 'dejavuserif-italic', 'dejavuserif-bolditalic'),
    ),
    'courier': (
        ('liberationmono-regular', 'liberationmono-bold', 'liberationmono-italic', 'liberationmono-bolditalic'),
        ('cousine-regular', 'cousine-bold', 'cousine-italic', 'cousine-bolditalic'),
        ('cour', 'courbd', 'couri', 'courbi'),
        ('dejavusansmono', 'dejavusansmono-bold', 'dejavusansmono-oblique', 'dejavusansmono-boldoblique'),
    ),
    'calibri': (
        ('carlito-regular', 'carlito-bold', 'carlito-italic', 'carlito-bolditalic'),
        ('calibri', 'calibrib', 'calibrii', 'calibriz'),
    ),
    'cambria': (
        ('caladea-regular', 'caladea-bold', 'caladea-italic', 'caladea-bolditalic'),
        ('cambria', 'cambriab', 'cambriai', 'cambriaz'),
    ),
    'georgia': (
        ('gelasio-regular', 'gelasio-bold', 'gelasio-italic', 'gelasio-bolditalic'),
        ('georgia', 'georgiab', 'georgiai', 'georgiaz'),
    ),
    'verdana': (
        ('dejavusans', 'dejavusans-bold', 'dejavusans-oblique', 'dejavusans-boldoblique'),
        ('verdana', 'verdanab', 'verdanai', 'verdanaz'),
    ),
    'tahoma': (
        ('dejavusans', 'dejavusans-bold', 'dejavusans-oblique', 'dejavusans-boldoblique'),
        ('tahoma', 'tahomabd', 'tahoma', 'tahomabd'),
    ),
    'dejavusans': (
        ('dejavusans', 'dejavusans-bold', 'dejavusans-oblique', 'dejavusans-boldoblique'),
    ),
    'dejavuserif': (
        ('dejavuserif', 'dejavuserif-bold', 'dejavuserif-italic', 'dejavuserif-bolditalic'),
    ),
    'noto-sans': (
        ('notosans-regular', 'notosans-bold', 'notosans-italic', 'notosans-bolditalic'),
    ),
    'noto-serif': (
        ('notoserif-regular', 'notoserif-bold', 'notoserif-italic', 'notoserif-bolditalic'),
    ),
}

#: A que familia se parece cada nombre que aparece en un PDF. Se mira por orden,
#: asi que lo largo va antes que lo corto ('dejavusansmono' antes que 'dejavusans').
_PARECIDOS: tuple[tuple[str, str], ...] = (
    ('dejavusansmono', 'courier'),
    ('dejavusans', 'dejavusans'),
    ('dejavuserif', 'dejavuserif'),
    ('timesnewroman', 'times'),
    ('couriernew', 'courier'),
    ('liberationsans', 'arial'),
    ('liberationserif', 'times'),
    ('liberationmono', 'courier'),
    ('bookantiqua', 'georgia'),
    ('notosansmono', 'courier'),
    ('notoserif', 'noto-serif'),
    ('notosans', 'noto-sans'),
    ('trebuchet', 'verdana'),
    ('helvetica', 'arial'),
    ('segoeui', 'arial'),
    ('arimo', 'arial'),
    ('arial', 'arial'),
    ('tinos', 'times'),
    ('times', 'times'),
    ('roman', 'times'),
    ('cousine', 'courier'),
    ('courier', 'courier'),
    ('consolas', 'courier'),
    ('menlo', 'courier'),
    ('monaco', 'courier'),
    ('carlito', 'calibri'),
    ('calibri', 'calibri'),
    ('caladea', 'cambria'),
    ('cambria', 'cambria'),
    ('gelasio', 'georgia'),
    ('georgia', 'georgia'),
    ('verdana', 'verdana'),
    ('tahoma', 'tahoma'),
    ('garamond', 'times'),
    ('palatino', 'times'),
    ('century', 'times'),
    ('minion', 'times'),
    ('baskerville', 'times'),
    ('caslon', 'times'),
    ('schoolbook', 'times'),
    ('cambay', 'times'),
    ('roboto', 'arial'),
    ('opensans', 'arial'),
    ('lato', 'arial'),
    ('montserrat', 'arial'),
    ('poppins', 'arial'),
    ('nunito', 'arial'),
    ('sourcesans', 'arial'),
    ('futura', 'arial'),
    ('gill', 'arial'),
    ('myriad', 'arial'),
    ('univers', 'arial'),
    ('frutiger', 'arial'),
    ('avenir', 'arial'),
    ('franklin', 'arial'),
    ('candara', 'arial'),
    ('corbel', 'arial'),
    ('calluna', 'times'),
)

#: Cuando el nombre no dice nada, se va por la familia generica que dedujo el
#: motor a partir de las banderas de la fuente.
_GENERICAS = {'sans-serif': 'arial', 'serif': 'times', 'monospace': 'courier'}

#: Prefijo de subconjunto que Word y LibreOffice le ponen a las fuentes
#: incrustadas: 'AAAAAA+DejaVuSerif-Bold'.
_SUBCONJUNTO = re.compile(r'^[A-Z]{6}\+')
_SOBRA = re.compile(r'[^a-z0-9]+')


def _normalizar(nombre: str) -> str:
    """'AAAAAA+DejaVuSerif-Bold' -> 'dejavuserifbold'."""
    return _SOBRA.sub('', _SUBCONJUNTO.sub('', nombre or '').lower())


@dataclass(frozen=True)
class Cara:
    """Una fuente concreta, ya lista para nombrarla en el CSS."""

    #: El nombre con el que se la llama en el CSS ('c3').
    nombre: str
    ruta: str
    #: Si el fichero no era el de la variante pedida y hay que fingir la
    #: negrita o la cursiva con CSS.
    sintetica: bool


class _Indice:
    """Las fuentes del sistema, por nombre de fichero sin extension."""

    def __init__(self) -> None:
        self._mapa: dict[str, str] | None = None

    def ruta(self, nombre: str) -> str | None:
        if self._mapa is None:
            self._mapa = self._construir()
        return self._mapa.get(nombre)

    @staticmethod
    def _construir() -> dict[str, str]:
        mapa: dict[str, str] = {}
        for carpeta in _CARPETAS:
            raiz = pathlib.Path(carpeta)
            if not raiz.is_dir():
                continue
            try:
                ficheros = list(raiz.rglob('*'))
            except OSError:
                continue
            for fichero in ficheros:
                # Los .ttc llevan varias fuentes dentro y PyMuPDF no siempre
                # sabe abrirlos: se quedan fuera a proposito.
                if fichero.suffix.lower() not in ('.ttf', '.otf'):
                    continue
                mapa.setdefault(fichero.stem.lower(), str(fichero))
        return mapa


class Fuentes:
    """
    El taller: dice con que fuente escribir cada fragmento y guarda los
    ficheros que hagan falta para que `insert_htmlbox` pueda usarlos.

    Vive todo el proceso: los contenedores de Modal se reutilizan y el indice
    de fuentes del sistema no cambia de un documento a otro. Lo unico que crece
    es el archivo, y solo con las fuentes que de verdad se han usado.
    """

    def __init__(self) -> None:
        self._indice = _Indice()
        self._archivo = pymupdf.Archive()
        self._caras: dict[tuple[str, int], Cara | None] = {}
        self._registradas: dict[str, str] = {}
        self._css: list[str] = []
        self._glifos: dict[str, tuple[pymupdf.Font | None, set[int], set[int]]] = {}

    # --- Lo que usa el motor --------------------------------------------------

    def cara(self, fuente: str, generica: str, negrita: bool, cursiva: bool,
             texto: str) -> Cara | None:
        """
        La fuente con la que escribir `texto`, o None si no hay ninguna que
        valga y es mejor dejar que MuPDF elija.
        """
        elegida = self._resolver(fuente, generica, negrita, cursiva)
        if elegida is None or not self._cubre(elegida.ruta, texto):
            return None
        return elegida

    @property
    def css(self) -> str:
        """Las declaraciones `@font-face` de todo lo que se ha usado."""
        return '\n'.join(self._css)

    @property
    def archivo(self) -> pymupdf.Archive:
        return self._archivo

    # --- Por dentro -----------------------------------------------------------

    def _resolver(self, fuente: str, generica: str, negrita: bool, cursiva: bool) -> Cara | None:
        variante = (NEGRITA if negrita else 0) + (CURSIVA if cursiva else 0)
        clave = (_normalizar(fuente), variante)
        if clave not in self._caras:
            self._caras[clave] = self._buscar(clave[0], generica, variante)
        return self._caras[clave]

    def _buscar(self, normalizado: str, generica: str, variante: int) -> Cara | None:
        familia = None
        for trozo, destino in _PARECIDOS:
            if trozo in normalizado:
                familia = destino
                break
        if familia is None:
            familia = _GENERICAS.get(generica, 'arial')

        # Primero la familia que toca; si de esa no hay nada instalado (pasa con
        # DejaVu fuera de Linux), la generica de su clase, que siempre esta. Una
        # fuente parecida y elegida aqui es mejor que la que elija MuPDF por su
        # cuenta, porque de esta se conocen las medidas.
        generica = _GENERICAS.get(generica, 'arial')
        for clave in dict.fromkeys((familia, generica)):
            for juego in _JUEGOS.get(clave, ()):
                ruta = self._indice.ruta(juego[variante])
                if ruta:
                    return self._registrar(ruta, sintetica=False)
                # La variante no esta (Tahoma no tiene cursiva, por ejemplo): se
                # usa la redonda y se le pide a CSS que finja lo que falte.
                ruta = self._indice.ruta(juego[REGULAR])
                if ruta:
                    return self._registrar(ruta, sintetica=variante != REGULAR)
        return None

    def _registrar(self, ruta: str, sintetica: bool) -> Cara | None:
        nombre = self._registradas.get(ruta)
        if nombre is None:
            try:
                datos = pathlib.Path(ruta).read_bytes()
            except OSError:
                return None
            nombre = f'c{len(self._registradas)}'
            self._registradas[ruta] = nombre
            self._archivo.add(datos, f'{nombre}.ttf')
            self._css.append(f'@font-face {{font-family: {nombre}; src: url({nombre}.ttf);}}')
        return Cara(nombre=nombre, ruta=ruta, sintetica=sintetica)

    def _cubre(self, ruta: str, texto: str) -> bool:
        """
        Si esta fuente tiene todos los caracteres del texto.

        Es la unica comprobacion que impide el desastre silencioso: una fuente
        sin el glifo no avisa, escribe un hueco. Los caracteres ya vistos se
        recuerdan, que una pagina repite los mismos doscientos.
        """
        if ruta not in self._glifos:
            try:
                fuente = pymupdf.Font(fontfile=ruta)
            except Exception:  # noqa: BLE001 - una fuente rota no tumba un trabajo
                fuente = None
            self._glifos[ruta] = (fuente, set(), set())
        fuente, buenos, malos = self._glifos[ruta]
        if fuente is None:
            return False
        for caracter in texto:
            punto = ord(caracter)
            if punto in buenos or caracter.isspace() or punto < 0x21:
                continue
            if punto in malos:
                return False
            if fuente.has_glyph(punto):
                buenos.add(punto)
            else:
                malos.add(punto)
                return False
        return True


#: Una sola instancia por proceso: el indice de fuentes del sistema se construye
#: una vez y el archivo solo guarda lo que se ha llegado a usar.
taller = Fuentes()
