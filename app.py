"""
Servicio de traduccion de PDF de ahcebrian.com, en Modal.

Tres entradas, todas con autenticacion de proxy de Modal (cabeceras Modal-Key y
Modal-Secret): la URL, aunque se filtre, no sirve de nada sin ellas. Solo el
Worker de Cloudflare las tiene.

    POST /analizar?...   -> cuantas paginas, en que idioma, y si es un escaneado
    POST /traducir?...   -> sucesos de progreso y, al final, el PDF traducido
    GET  /despertar      -> el ping que arranca el contenedor en frio

El PDF viaja en el cuerpo, en crudo, y los parametros en la direccion: asi el
Worker puede pasar el fichero tal cual, sin montar un multipart.

**Aqui no se guarda nada.** El documento solo existe en la memoria del
contenedor, que Modal destruye al terminar. No hay disco, ni base de datos, ni
registro del contenido.

Este fichero es solo el envoltorio de Modal: la aplicacion esta en servicio.py y
el motor en motor.py y ocr.py, que no saben nada de Modal y se pueden probar en
un ordenador cualquiera.

Publicar:  cd traductor && python -m modal deploy app.py

Licencia: AGPL-3.0, por PyMuPDF (ver LICENSE y README.md de esta carpeta).
"""

import modal

#: Las fuentes con las que se vuelve a escribir el texto traducido.
#:
#: No son un adorno: sin ellas el documento traducido sale con otra letra, las
#: palabras miden otra cosa y los parrafos se parten por otro sitio. Las tres
#: primeras son clones **metricamente compatibles** —miden exactamente lo mismo,
#: letra por letra— de las fuentes con las que estan hechos los PDF que llegan:
#:
#:   liberation  = Arial, Times New Roman y Courier New
#:   carlito     = Calibri          (el Word moderno)
#:   caladea     = Cambria
#:   dejavu      = lo que sale de LibreOffice, y el respaldo para todo lo demas
#:
#: Ver traductor/fuentes.py, que es quien las busca y decide.
FUENTES = ('fonts-liberation', 'fonts-crosextra-carlito', 'fonts-crosextra-caladea',
           'fonts-dejavu-core', 'fonts-dejavu-extra')

imagen = (
    modal.Image.debian_slim(python_version='3.12')
    .apt_install('tesseract-ocr', 'tesseract-ocr-tur', 'tesseract-ocr-spa',
                 'tesseract-ocr-eng', 'tesseract-ocr-chi-sim', 'tesseract-ocr-jpn',
                 'tesseract-ocr-por', 'tesseract-ocr-deu', 'tesseract-ocr-fra',
                 *FUENTES)
    .pip_install('pymupdf==1.27.2', 'lingua-language-detector==2.2.0',
                 'pytesseract==0.3.13', 'pillow==11.3.0', 'httpx==0.28.1',
                 'fastapi[standard]==0.115.12')
    .add_local_python_source('servicio', 'analisis', 'motor', 'mt', 'ocr', 'idiomas',
                             'fuentes')
)

app = modal.App('ahcebrian-traductor', image=imagen)


@app.function(cpu=1, memory=2048, region='eu', timeout=1800)
@modal.asgi_app(requires_proxy_auth=True)
def web():
    """La aplicacion, montada dentro del contenedor (ver servicio.py)."""
    from servicio import crear

    return crear()
