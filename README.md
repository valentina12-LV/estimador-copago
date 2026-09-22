# 🏥 Estimador Agéntico de Copago y Cobertura

Proyecto para el **hackIAthon** — Reto 3: *Estimador Agéntico de Copago y Cobertura para el Paciente*.

## ¿Qué hace?

El paciente describe un síntoma en lenguaje natural, indica su tipo de cobertura (sin
seguro / privado / público) y su provincia en Panamá. El agente:

1. **Clasifica el síntoma de forma estructurada** (tool use / function calling con
   **Gemini**): especialidad médica, nivel de urgencia y el razonamiento detrás de la
   decisión — no es texto libre parseado a mano, es una salida estructurada validada
   contra un esquema.
2. **Evalúa urgencia clínica primero**: si detecta señales de posible emergencia (dolor
   de pecho, dificultad respiratoria, sangrado abundante, pérdida de consciencia),
   prioriza indicarle al paciente que acuda de inmediato a emergencias, por encima de
   cualquier optimización de costo.
3. **Calcula el costo estimado según el tipo de cobertura**: sin seguro (pago directo en
   privados u opción pública gratuita/subsidiada), seguro privado (copago + excedente
   sobre un modelo genérico de mercado) o seguro público CSS/MINSA (cubierto).
4. **Compara hospitales reales de la provincia elegida** y recomienda primero la opción
   más económica según la cobertura seleccionada.
5. **Verifica cada hospital con IA en cada consulta**: Gemini evalúa, con su
   conocimiento general, qué tan confiable es que ese nombre corresponda a una
   institución real de esa provincia (🟢🟡🔴⚪), sin inventar reemplazos.
6. **Redacta una respuesta clara y empática**, y expone su razonamiento en un panel de
   "¿Cómo llegó a esta conclusión?" para trazabilidad.
7. **Nunca deja al usuario sin respuesta**: si la API de Gemini no está disponible o
   falla, cae automáticamente a un clasificador de respaldo por palabras clave.

### Origen de los datos

- `data/hospitales_panama.json` contiene hospitales públicos (MINSA/CSS) y privados
  reales por cada una de las 10 provincias de Panamá, obtenidos mediante búsqueda web
  con la **API de TinyFish** (Search + Fetch) sobre fuentes públicas. Los **montos de
  copago son simulados** (mock), ya que no hay tarifario público confiable — esto se
  indica siempre en la interfaz.
- Como la fuente web puede tener imprecisiones (p. ej. un hospital mal ubicado de
  provincia), cada consulta pasa por una **verificación adicional con Gemini** que
  marca el nivel de confianza de cada institución listada.

## Arquitectura

```
Usuario (sintoma + cobertura + provincia)
      │
      ▼
Streamlit UI (app.py)
      │
      ├── Gemini (gemini-2.5-flash) con tool use
      │     ├── tool "clasificar_consulta" → {especialidad, urgencia, razonamiento}
      │     │     └── si falla/no hay API key → clasificador de respaldo por reglas
      │     └── tool "verificar_hospitales" → confianza por hospital listado
      │
      ├── Si urgencia == "alta" → alerta de emergencia (no se optimiza costo)
      │
      ├── Logica de negocio en Python → calcula copago segun cobertura y compara
      │     hospitales reales de la provincia (data/hospitales_panama.json)
      │
      └── Filtro interactivo publico/privado + tag "Mejor opcion" en la UI
      │
      ▼
Resultado: especialidad + urgencia + razonamiento + hospitales verificados y
comparados, filtrables por tipo, con la mejor opcion segun tu cobertura primero
```

Es una app **agéntica** porque no es solo un chatbot de una sola vuelta: recibe un
objetivo (ayudar al paciente a decidir con seguridad y economía), usa un modelo de
lenguaje con salida estructurada para razonar sobre datos no estructurados (el
síntoma), verifica con IA la calidad de los datos que va a mostrar, aplica una
jerarquía de decisión (seguridad del paciente antes que costo) y combina todo con
reglas de negocio deterministas para producir una acción concreta, sin intervención
humana ni resultados frágiles ante fallos de la API.

## Cómo se alinea con los criterios de evaluación

- **Capacidad de análisis**: separa señales de urgencia clínica de la optimización de
  costo, y no oculta su razonamiento.
- **Criterio técnico**: usa tool use (salida estructurada) en vez de parseo de texto
  libre; tiene manejo de errores y un fallback funcional sin IA; incluye disclaimer
  médico apropiado; verifica con IA la fiabilidad de datos obtenidos por scraping.
- **Ejecución con herramientas de IA**: agente desplegado, funcional de punta a punta,
  con un flujo de decisión de varios pasos (clasificar → calcular → verificar →
  redactar) en vez de un solo prompt.

## Cómo correrlo localmente

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edita .streamlit/secrets.toml y pega tu API key de Gemini (y de TinyFish si la usas)

streamlit run app.py
```

## Cómo desplegarlo (Streamlit Community Cloud, gratis)

1. Sube este proyecto a un repositorio de GitHub (ver guía de buenas prácticas más
   abajo — **nunca subas `.streamlit/secrets.toml` con tus keys reales**).
2. Entra a https://share.streamlit.io con tu cuenta de GitHub.
3. Clic en **New app**, selecciona el repo, la rama y `app.py` como archivo principal.
4. En **Advanced settings > Secrets**, pega:
   ```
   GEMINI_API_KEY = "..."
   TINYFISH_API_KEY = "..."
   ```
5. Clic en **Deploy**. En 1-2 minutos obtienes una URL pública tipo
   `https://tu-app.streamlit.app`.


