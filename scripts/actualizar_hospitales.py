"""Regenera data/hospitales_panama.json usando la API de TinyFish (Search + Fetch)
para traer una fuente web real sobre hospitales publicos/privados de Panama por
provincia, y estructurarla en el formato que consume app.py.

No requiere el endpoint "Agent" de TinyFish (que consume creditos): Search y Fetch
son gratuitos. Uso:

    python scripts/actualizar_hospitales.py

Requiere TINYFISH_API_KEY en .streamlit/secrets.toml o como variable de entorno.
"""
import json
import os
import re
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
SECRETS = ROOT / ".streamlit" / "secrets.toml"
OUT = ROOT / "data" / "hospitales_panama.json"

FUENTE_URL = "https://panama.viajenda.com/articulo/listado-de-hospitales-y-centros-de-salud-en-panama"

ESPECIALIDADES_PUBLICO = [
    "Medicina General", "Cardiologia", "Dermatologia", "Traumatologia",
    "Pediatria", "Ginecologia", "Emergencia",
]
COSTOS_BASE_PRIVADO = {
    "Medicina General": 50, "Cardiologia": 120, "Dermatologia": 70,
    "Traumatologia": 100, "Pediatria": 60, "Ginecologia": 90, "Emergencia": 160,
}

MAPA_PROVINCIAS = {
    "Panama": "Panama",
    "Panama Oeste": "Panama Oeste",
    "Chiriqui": "Chiriqui",
    "Bocas del Toro": "Bocas del Toro",
    "Veraguas": "Veraguas",
    "Los Santos": "Los Santos",
    "Herrera": "Herrera",
    "Cocle": "Cocle",
    "Colon": "Colon",
    "Darien": "Darien",
}


def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.strip()


def cargar_api_key() -> str:
    key = os.environ.get("TINYFISH_API_KEY")
    if key:
        return key
    if SECRETS.exists():
        for linea in SECRETS.read_text(encoding="utf-8").splitlines():
            if linea.startswith("TINYFISH_API_KEY"):
                return linea.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(
        "No se encontro TINYFISH_API_KEY (ni en el entorno ni en .streamlit/secrets.toml)."
    )


def obtener_texto_fuente(api_key: str) -> str:
    r = requests.post(
        "https://api.fetch.tinyfish.ai",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={"urls": [FUENTE_URL]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["results"][0]["text"]


def parsear(texto: str) -> dict:
    texto = "\n" + texto
    # el documento mezcla encabezados de 3 y 4 almohadillas (### / ####) de forma
    # inconsistente entre provincias; se aceptan ambos por igual.
    bloques = re.split(r"\n#{3,4} ", texto)

    resultado = {v: {"publicos": [], "privados": []} for v in set(MAPA_PROVINCIAS.values())}
    provincia_actual = None

    for bloque in bloques:
        lineas = bloque.strip().splitlines()
        if not lineas:
            continue
        encabezado = lineas[0].strip()

        m = re.match(r"Provincia de (.+)", encabezado)
        if m:
            provincia_actual = MAPA_PROVINCIAS.get(normalizar(m.group(1)))
            continue

        if provincia_actual is None:
            continue

        norm = normalizar(encabezado).lower()
        if "blicos" in norm:
            tipo = "publicos"
        elif "privados" in norm:
            tipo = "privados"
        else:
            continue  # "Otros centros de salud", etc. -> se ignoran

        for linea in lineas[1:]:
            linea = linea.strip()
            if not linea.startswith("*"):
                continue
            item = linea.lstrip("*").strip()
            mm = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", item)
            if mm:
                nombre, ubicacion = mm.group(1).strip(), mm.group(2).strip()
            else:
                nombre, ubicacion = item, ""
            if ubicacion and ubicacion in nombre:
                ubicacion = ""
            nombre_final = f"{nombre} ({ubicacion})" if ubicacion else nombre

            if tipo == "publicos":
                resultado[provincia_actual]["publicos"].append(
                    {"nombre": nombre_final, "especialidades": ESPECIALIDADES_PUBLICO}
                )
            else:
                seed = sum(ord(c) for c in nombre)
                especialidades = {
                    esp: round(costo * (0.9 + (seed % 21) / 100), 0)
                    for esp, costo in COSTOS_BASE_PRIVADO.items()
                }
                resultado[provincia_actual]["privados"].append(
                    {"nombre": nombre_final, "especialidades": especialidades}
                )

    return resultado


def main():
    api_key = cargar_api_key()
    texto = obtener_texto_fuente(api_key)
    resultado = parsear(texto)

    for prov, datos in resultado.items():
        print(f"{prov}: {len(datos['publicos'])} publicos, {len(datos['privados'])} privados")

    OUT.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGuardado en {OUT}")


if __name__ == "__main__":
    main()
