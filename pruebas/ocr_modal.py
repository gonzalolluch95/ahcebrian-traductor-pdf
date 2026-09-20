"""
Prueba el camino del escaneado (ocr.py) dentro de Modal, sin traducir nada.

Tesseract solo esta en la imagen del contenedor, asi que esta prueba no se puede
lanzar en el ordenador. El "traductor" de aqui devuelve el propio texto del OCR
en mayusculas: asi se ve de un vistazo QUE ha leido Tesseract y como quedan los
parches, que es lo que hay que mirar en un escaneado.

    cd traductor && python -m modal run pruebas/ocr_modal.py --ruta "..\\..\\..\\Base\\Egglin\\ISO 9001 Turkish .- 28.03.2025 15.32_page-0002.pdf"

Deja el PDF y un PNG por pagina en traductor/pruebas/out/.
"""

import pathlib
import sys

import modal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import imagen  # noqa: E402

# `add_local_python_source('app')`: el contenedor vuelve a importar ESTE fichero,
# que en su primera linea hace `from app import imagen`. Sin subir tambien app.py
# el contenedor arranca en bucle con ModuleNotFoundError: No module named 'app'.
app = modal.App('ahcebrian-traductor-pruebas', image=imagen.add_local_python_source('app'))
OUT = pathlib.Path(__file__).parent / 'out'


@app.function(cpu=2, memory=4096, region='eu', timeout=900)
def ocr_sin_traducir(datos: bytes, origen: str) -> bytes:
    import pymupdf

    from ocr import traducir_escaneada

    class Falso:
        """Devuelve lo que el OCR leyo, en mayusculas y con una marca delante."""

        motor = 'falso'
        peticiones = 0

        def traducir(self, textos, origen, destino, imagen=None):  # noqa: ANN001, ARG002
            return {k: '· ' + v.upper() for k, v in textos.items()}

    doc = pymupdf.open(stream=datos, filetype='pdf')
    for numero, pagina in enumerate(doc, start=1):
        # Es un generador: cuenta por donde va y al final devuelve los avisos.
        trabajo = traducir_escaneada(pagina, numero, Falso(), origen, 'es')
        while True:
            try:
                next(trabajo)
            except StopIteration as fin:
                avisos = fin.value or []
                break
        for a in avisos:
            print('aviso:', a.texto)
    return doc.tobytes(garbage=3, deflate=True)


@app.local_entrypoint()
def main(ruta: str, origen: str = 'tr'):
    import time

    import pymupdf

    origen_pdf = pathlib.Path(ruta)
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    salida = ocr_sin_traducir.remote(origen_pdf.read_bytes(), origen)
    nombre = origen_pdf.stem[:40].replace(' ', '_')
    destino = OUT / f'{nombre}-ocr.pdf'
    destino.write_bytes(salida)
    doc = pymupdf.open(destino)
    for numero, pagina in enumerate(doc, start=1):
        pagina.get_pixmap(dpi=110).save(OUT / f'{nombre}-{numero}-ocr.png')
    print(f'{len(salida) / 1024:.0f} KB, {time.time() - t0:.1f} s -> {destino}')
