"""
Que fuentes hay DENTRO del contenedor, y con cual se escribiria cada cosa.

Es la comprobacion que no se puede hacer desde aqui: `fuentes.py` busca las
fuentes en las carpetas del sistema, y las del sistema de este ordenador no
son las de Debian. Si el `apt_install` de las fuentes se cayera del `app.py`,
todo seguiria funcionando —el motor tiene su camino de respaldo— pero los
documentos volverian a salir con otra letra sin que nadie se entere.

    cd traductor
    python -m modal run pruebas/fuentes_modal.py

Tiene que decir que las tres familias importantes (Arial, Times y Calibri) se
resuelven a un fichero de verdad, y que el turco y el español se pueden
escribir con ellas.
"""

import pathlib
import sys

import modal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import imagen  # noqa: E402

# `add_local_python_source('app')`: el contenedor vuelve a importar ESTE fichero,
# que arriba hace `from app import imagen`. Sin subir tambien app.py, arranca en
# bucle con ModuleNotFoundError: No module named 'app'. La misma trampa que en
# ocr_modal.py, y cuesta lo mismo cada vez que se olvida.
app = modal.App('ahcebrian-traductor-pruebas', image=imagen.add_local_python_source('app'))


@app.function(cpu=1, memory=1024, region='eu', timeout=120)
def mirar() -> str:
    from fuentes import taller

    lineas = []
    familias = pathlib.Path('/usr/share/fonts')
    cuantas = len(list(familias.rglob('*.ttf'))) + len(list(familias.rglob('*.otf')))
    lineas.append(f'ficheros de fuente en el contenedor: {cuantas}')

    # Lo que de verdad importa: con que se escribiria cada fuente de los PDF
    # que llegan, y si esa fuente tiene las letras del idioma de destino.
    muestras = (
        ('ArialMT', 'sans-serif', False, False),
        ('Arial-BoldMT', 'sans-serif', True, False),
        ('TimesNewRomanPSMT', 'serif', False, False),
        ('Calibri', 'sans-serif', False, False),
        ('Calibri-Bold', 'sans-serif', True, False),
        ('Cambria', 'serif', False, False),
        ('AAAAAA+DejaVuSerif', 'serif', False, False),
        ('CourierNewPSMT', 'monospace', False, False),
        ('UnaFuenteQueNoExiste', 'sans-serif', False, False),
    )
    textos = {
        'español': 'Ñandú con acentuación: ¿pequeño? ¡sí!',
        'turco': 'Yürürlüğe koyduğunu İstanbul şehrinde',
        'portugués': 'Ação, coração e informação',
        'chino': '本文件为测试用途',
    }
    for fuente, generica, negrita, cursiva in muestras:
        cara = taller.cara(fuente, generica, negrita, cursiva, textos['español'])
        nombre = pathlib.Path(cara.ruta).name if cara else 'NINGUNA (elige MuPDF)'
        lineas.append(f'  {fuente:24} {"negrita" if negrita else "       "} -> {nombre}')

    lineas.append('cobertura por idioma (con Arial):')
    for idioma, texto in textos.items():
        cara = taller.cara('ArialMT', 'sans-serif', False, False, texto)
        lineas.append(f'  {idioma:12} {"sí" if cara else "no (se deja elegir a MuPDF)"}')
    return '\n'.join(lineas)


@app.local_entrypoint()
def main() -> None:
    print(mirar.remote())
