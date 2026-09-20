"""
Deteccion del idioma de un documento, con lingua-py (licencia Apache-2.0).

Se usa para *sugerir* el idioma de origen, nunca para decidir por el usuario:
la pantalla enseña lo detectado y deja cambiarlo. Con menos de 200 caracteres
de muestra no se sugiere nada, porque a esa longitud se confunde.
"""

from __future__ import annotations

from functools import lru_cache

#: Los idiomas entre los que se elige. Mas idiomas = mas memoria y mas
#: confusiones; estos son los del cliente (Turquia, Espana, Portugal, China,
#: Japon) mas los cuatro europeos que aparecen en sus documentos.
CODIGOS = ('es', 'en', 'pt', 'tr', 'fr', 'de', 'it', 'nl', 'zh', 'ja', 'ru', 'ar')

#: Debajo de esto no se sugiere idioma: la muestra es demasiado corta.
MINIMO = 200
CONFIANZA_MINIMA = 0.5


@lru_cache(maxsize=1)
def _detector():
    from lingua import Language, LanguageDetectorBuilder

    nombres = {
        'es': Language.SPANISH, 'en': Language.ENGLISH, 'pt': Language.PORTUGUESE,
        'tr': Language.TURKISH, 'fr': Language.FRENCH, 'de': Language.GERMAN,
        'it': Language.ITALIAN, 'nl': Language.DUTCH, 'zh': Language.CHINESE,
        'ja': Language.JAPANESE, 'ru': Language.RUSSIAN, 'ar': Language.ARABIC,
    }
    idiomas = [nombres[c] for c in CODIGOS]
    # `low_accuracy_mode`: solo hacen falta modelos de una letra, que ocupan una
    # fraccion de memoria. Para un documento entero sobra de largo.
    return LanguageDetectorBuilder.from_languages(*idiomas).with_low_accuracy_mode().build()


def detectar(texto: str) -> tuple[str | None, float]:
    """Devuelve (codigo ISO, confianza de 0 a 1) o (None, 0) si no esta claro."""
    limpio = ' '.join(texto.split())
    if len(limpio) < MINIMO:
        return None, 0.0
    valores = _detector().compute_language_confidence_values(limpio[:4000])
    if not valores:
        return None, 0.0
    mejor = valores[0]
    codigo = mejor.language.iso_code_639_1.name.lower()
    if mejor.value < CONFIANZA_MINIMA:
        return None, round(mejor.value, 2)
    return codigo, round(mejor.value, 2)
