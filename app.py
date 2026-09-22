import os
import json
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types

# ----------------------------------------------------------------------------
# Configuracion de la pagina
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Estimador de Copago y Cobertura",
    page_icon="🏥",
    layout="centered",
)

DATA_DIR = Path(__file__).parent / "data"
MODEL = "gemini-2.5-flash"

PROVINCIAS_PANAMA = [
    "Bocas del Toro",
    "Chiriqui",
    "Cocle",
    "Colon",
    "Darien",
    "Herrera",
    "Los Santos",
    "Panama",
    "Panama Oeste",
    "Veraguas",
]

TIPOS_COBERTURA = ["Sin seguro", "Seguro privado", "Seguro publico (CSS/MINSA)"]

ICONOS_ESPECIALIDAD = {
    "Medicina General": "🩺",
    "Cardiologia": "❤️",
    "Dermatologia": "🧴",
    "Traumatologia": "🦴",
    "Pediatria": "👶",
    "Ginecologia": "🤰",
    "Emergencia": "🚑",
}

# Modelo generico de copago para "Seguro privado" (no esta atado a una
# aseguradora especifica, es un promedio simulado de mercado en Panama).
COPAGOS_PRIVADO = {
    "Medicina General": 10,
    "Cardiologia": 25,
    "Dermatologia": 20,
    "Traumatologia": 30,
    "Pediatria": 15,
    "Ginecologia": 25,
    "Emergencia": 40,
}
COSTOS_REFERENCIA_PRIVADO = {
    "Medicina General": 50,
    "Cardiologia": 120,
    "Dermatologia": 70,
    "Traumatologia": 100,
    "Pediatria": 60,
    "Ginecologia": 90,
    "Emergencia": 160,
}

ESPECIALIDADES = list(COPAGOS_PRIVADO.keys())


# ----------------------------------------------------------------------------
# Estilos (tarjetas, tags de publico/privado, etc.)
# ----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .tag {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        margin-right: 6px;
    }
    .tag-publico { background: rgba(37, 99, 235, 0.15); color: #2563eb; }
    .tag-privado { background: rgba(124, 58, 237, 0.15); color: #7c3aed; }
    .tag-mejor { background: rgba(22, 163, 74, 0.15); color: #16a34a; }
    .hosp-nombre { font-size: 16px; font-weight: 600; }
    .hosp-nota { font-size: 12.5px; opacity: 0.75; }
    .hosp-precio { font-size: 20px; font-weight: 700; text-align: right; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Carga de datos "mock" de hospitales por provincia (obtenidos via busqueda
# web con TinyFish, complementados con verificacion de Gemini por consulta)
# ----------------------------------------------------------------------------
@st.cache_data
def cargar_hospitales():
    with open(DATA_DIR / "hospitales_panama.json", encoding="utf-8") as f:
        return json.load(f)


HOSPITALES_PANAMA = cargar_hospitales()


# ----------------------------------------------------------------------------
# Cliente de Gemini (el "cerebro" del agente)
# ----------------------------------------------------------------------------
def get_client():
    api_key = None
    try:
        api_key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        pass
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


client = get_client()


# ----------------------------------------------------------------------------
# Clasificador de respaldo (sin IA) - garantiza que la app nunca se caiga
# ----------------------------------------------------------------------------
PALABRAS_CLAVE = {
    "Emergencia": [
        "no respira", "no puedo respirar", "inconsciente", "sangrado abundante",
        "sangrando mucho", "convulsion", "convulsiones",
    ],
    "Cardiologia": ["pecho", "corazon", "palpitacion", "falta de aire", "respirar"],
    "Traumatologia": [
        "hueso", "fractura", "golpe", "esguince", "rodilla", "espalda",
        "articulacion", "tobillo", "torci", "tuerc",
    ],
    "Dermatologia": ["piel", "mancha", "alergia", "sarpullido", "picazon", "erupcion"],
    "Pediatria": ["mi hijo", "mi hija", "bebe", "nino", "niña"],
    "Ginecologia": ["menstrual", "embarazo", "ginecolog"],
}
URGENCIAS_ALTAS = [
    "no respira",
    "no puedo respirar",
    "dificultad para respirar",
    "falta de aire",
    "falta el aire",
    "inconscien",
    "convuls",
    "sangr",  # cubre sangrado / sangrando / sangre
    "dolor de pecho",
    "dolor en el pecho",
]


def clasificar_fallback(sintoma: str):
    texto = sintoma.lower()
    urgencia = "alta" if any(p in texto for p in URGENCIAS_ALTAS) else "baja"
    for especialidad, palabras in PALABRAS_CLAVE.items():
        if any(p in texto for p in palabras):
            return especialidad, urgencia
    return "Medicina General", urgencia


# ----------------------------------------------------------------------------
# Herramienta (function calling) que el agente usa para devolver una decision
# estructurada sobre el sintoma del paciente.
# ----------------------------------------------------------------------------
DECLARACION_CLASIFICAR = types.FunctionDeclaration(
    name="clasificar_consulta",
    description=(
        "Clasifica el sintoma de un paciente en una especialidad medica de la lista "
        "disponible y determina el nivel de urgencia clinica."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "especialidad": types.Schema(
                type="STRING",
                enum=ESPECIALIDADES,
                description="Especialidad medica mas adecuada para el sintoma.",
            ),
            "urgencia": types.Schema(
                type="STRING",
                enum=["baja", "media", "alta"],
                description=(
                    "alta = posible riesgo vital inminente (ej. dolor de pecho agudo, "
                    "dificultad respiratoria severa, sangrado abundante, perdida de consciencia)."
                ),
            ),
            "razonamiento": types.Schema(
                type="STRING",
                description="Explicacion breve (una linea) de por que se eligio esta clasificacion.",
            ),
        },
        required=["especialidad", "urgencia", "razonamiento"],
    ),
)

TOOL_CLASIFICAR = types.Tool(function_declarations=[DECLARACION_CLASIFICAR])


def clasificar_con_agente(sintoma: str):
    """El agente decide especialidad + urgencia de forma estructurada (function calling).

    Si el modelo no esta disponible o falla, cae a un clasificador por palabras
    clave para que la app nunca deje al usuario sin respuesta.
    """
    if client is not None:
        try:
            respuesta = client.models.generate_content(
                model=MODEL,
                contents=f'Sintoma del paciente: "{sintoma}"',
                config=types.GenerateContentConfig(
                    tools=[TOOL_CLASIFICAR],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode="ANY",
                            allowed_function_names=["clasificar_consulta"],
                        )
                    ),
                ),
            )
            for parte in respuesta.candidates[0].content.parts:
                if parte.function_call and parte.function_call.name == "clasificar_consulta":
                    datos = dict(parte.function_call.args)
                    return datos["especialidad"], datos["urgencia"], datos.get("razonamiento")
        except Exception:
            pass  # cae al fallback silenciosamente, la demo no se detiene

    especialidad, urgencia = clasificar_fallback(sintoma)
    return especialidad, urgencia, "Clasificado por reglas de respaldo (sin IA disponible)."


# ----------------------------------------------------------------------------
# Herramienta de verificacion: Gemini revisa (con su conocimiento general) si
# los hospitales del JSON simulado son instituciones reales de esa provincia.
# No reemplaza nombres ni inventa alternativas: solo marca un nivel de
# confianza para que el paciente sepa que el dato es simulado y debe
# confirmarlo con su aseguradora / el hospital antes de acudir.
# ----------------------------------------------------------------------------
DECLARACION_VERIFICAR = types.FunctionDeclaration(
    name="verificar_hospitales",
    description=(
        "Evalua, con base en conocimiento general, si cada hospital de la lista "
        "es una institucion real ubicada en la provincia indicada de Panama."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "resultados": types.Schema(
                type="ARRAY",
                items=types.Schema(
                    type="OBJECT",
                    properties={
                        "nombre": types.Schema(type="STRING"),
                        "confianza": types.Schema(
                            type="STRING",
                            enum=["alta", "media", "baja"],
                            description="Que tan seguro estas de que el nombre corresponde a una institucion real en esa provincia.",
                        ),
                        "nota": types.Schema(
                            type="STRING",
                            description="Comentario breve, ej. si el nombre parece incorrecto o generico.",
                        ),
                    },
                    required=["nombre", "confianza"],
                ),
            )
        },
        required=["resultados"],
    ),
)

TOOL_VERIFICAR = types.Tool(function_declarations=[DECLARACION_VERIFICAR])


def verificar_hospitales(nombres: list[str], provincia: str) -> dict:
    """Devuelve {nombre: {confianza, nota}} usando Gemini como verificador.

    Si no hay cliente o la llamada falla, devuelve confianza "sin_verificar"
    para cada nombre, sin bloquear el resto de la app.
    """
    if not nombres:
        return {}

    if client is None:
        return {n: {"confianza": "sin_verificar", "nota": "IA no disponible."} for n in nombres}

    try:
        respuesta = client.models.generate_content(
            model=MODEL,
            contents=(
                f"Provincia de Panama: {provincia}. "
                f"Hospitales/clinicas a verificar: {', '.join(nombres)}. "
                "Indica tu confianza en que cada uno existe realmente en esa provincia."
            ),
            config=types.GenerateContentConfig(
                tools=[TOOL_VERIFICAR],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=["verificar_hospitales"],
                    )
                ),
            ),
        )
        for parte in respuesta.candidates[0].content.parts:
            if parte.function_call and parte.function_call.name == "verificar_hospitales":
                datos = dict(parte.function_call.args)
                salida = {}
                for r in datos.get("resultados", []):
                    salida[r["nombre"]] = {
                        "confianza": r.get("confianza", "media"),
                        "nota": r.get("nota", ""),
                    }
                # cualquier nombre que el modelo no haya cubierto queda sin verificar
                for n in nombres:
                    salida.setdefault(n, {"confianza": "sin_verificar", "nota": ""})
                return salida
    except Exception:
        pass

    return {n: {"confianza": "sin_verificar", "nota": "No se pudo verificar."} for n in nombres}


def redactar_recomendacion(sintoma, especialidad, cobertura, mejor, urgencia):
    """El agente redacta una explicacion clara y empatica para el paciente."""
    if urgencia == "alta":
        return (
            "Tu descripcion sugiere una posible emergencia. No priorices el costo: "
            f"dirigete de inmediato a la sala de emergencias mas cercana de tu red "
            "o llama a la linea de emergencias (911 en Panama)."
        )

    if mejor is None:
        return (
            f"Segun tu sintoma, te recomendamos la especialidad {especialidad}, pero no "
            "encontramos opciones para tu provincia con los datos simulados actuales."
        )

    base = (
        f"Segun tu sintoma, te recomendamos la especialidad {especialidad}. "
        f"Con cobertura '{cobertura}', la mejor opcion es {mejor['hospital']}, "
        f"con un costo estimado de ${mejor['costo_paciente']:.2f}."
    )

    if client is None:
        return base

    try:
        prompt = (
            "Eres un agente de bienestar en Panama. Escribe una respuesta breve "
            "(maximo 4 lineas), clara y empatica para el paciente, en espanol, explicando: "
            f"1) que su sintoma ('{sintoma}') sugiere la especialidad {especialidad}, "
            f"2) que su cobertura es '{cobertura}', "
            f"3) que la mejor opcion es {mejor['hospital']} con un costo "
            f"estimado de ${mejor['costo_paciente']:.2f}. No uses markdown."
        )
        respuesta = client.models.generate_content(model=MODEL, contents=prompt)
        return respuesta.text.strip()
    except Exception:
        return base


# ----------------------------------------------------------------------------
# Logica de negocio: costo estimado por hospital segun provincia y cobertura.
# Cada opcion incluye "categoria" (publico/privado) por separado de la nota
# descriptiva, para poder taggear y filtrar en la interfaz.
# ----------------------------------------------------------------------------
def calcular_opciones(especialidad: str, provincia: str, cobertura: str):
    datos_provincia = HOSPITALES_PANAMA.get(provincia)
    if not datos_provincia:
        return []

    opciones = []

    if cobertura == "Seguro publico (CSS/MINSA)":
        for h in datos_provincia.get("publicos", []):
            if especialidad in h["especialidades"]:
                opciones.append(
                    {
                        "hospital": h["nombre"],
                        "categoria": "publico",
                        "nota": "Cubierto por CSS / MINSA",
                        "precio_hospital": 0.0,
                        "costo_paciente": 0.0,
                    }
                )

    elif cobertura == "Seguro privado":
        copago = COPAGOS_PRIVADO.get(especialidad, 0)
        costo_referencia = COSTOS_REFERENCIA_PRIVADO.get(especialidad, 0)
        for h in datos_provincia.get("privados", []):
            if especialidad in h["especialidades"]:
                precio = h["especialidades"][especialidad]
                excedente = max(0, precio - costo_referencia)
                opciones.append(
                    {
                        "hospital": h["nombre"],
                        "categoria": "privado",
                        "nota": "Red privada — tu seguro cubre parte del costo",
                        "precio_hospital": precio,
                        "costo_paciente": copago + excedente,
                    }
                )

    else:  # Sin seguro: cash price en privados + opcion publica gratuita/subsidiada
        for h in datos_provincia.get("publicos", []):
            if especialidad in h["especialidades"]:
                opciones.append(
                    {
                        "hospital": h["nombre"],
                        "categoria": "publico",
                        "nota": "Gratuito / subsidiado — puede tener espera",
                        "precio_hospital": 0.0,
                        "costo_paciente": 0.0,
                    }
                )
        for h in datos_provincia.get("privados", []):
            if especialidad in h["especialidades"]:
                precio = h["especialidades"][especialidad]
                opciones.append(
                    {
                        "hospital": h["nombre"],
                        "categoria": "privado",
                        "nota": "Pago directo (sin seguro)",
                        "precio_hospital": precio,
                        "costo_paciente": precio,
                    }
                )

    # Al ordenar por costo, si la cobertura elegida da beneficio (publico
    # gratuito o privado con copago reducido) esa opcion queda de primera.
    opciones.sort(key=lambda o: o["costo_paciente"])
    return opciones


# ----------------------------------------------------------------------------
# Interfaz
# ----------------------------------------------------------------------------
st.title("🏥 Estimador de Copago y Cobertura")
st.caption(
    "Agente que sugiere la especialidad medica segun tu sintoma, evalua la urgencia "
    "y estima cuanto pagarias segun tu cobertura y ubicacion en Panama."
)
st.info(
    "Esta herramienta ofrece una estimacion automatizada y no sustituye el diagnostico "
    "de un profesional de salud. Ante una emergencia real, llama al 911 o acude de "
    "inmediato al hospital mas cercano.",
    icon="ℹ️",
)

if client is None:
    st.warning(
        "No se encontro GEMINI_API_KEY. La app funciona en modo demo con una regla "
        "basica y sin verificacion de hospitales por IA. Configura la clave en "
        "Settings > Secrets para activar el agente completo.",
        icon="⚠️",
    )

with st.form("consulta"):
    c1, c2 = st.columns(2)
    cobertura = c1.selectbox("💳 Tipo de cobertura", TIPOS_COBERTURA)
    pais = c2.selectbox("🌎 Pais", ["Panama", "Otro pais"])
    provincia = None
    if pais == "Panama":
        provincia = st.selectbox("📍 Provincia", PROVINCIAS_PANAMA)
    sintoma = st.text_area(
        "🗣️ Describe tu sintoma",
        placeholder="Ej: tengo dolor en el pecho y me falta el aire",
    )
    enviar = st.form_submit_button("Consultar al agente", use_container_width=True)

if enviar:
    if not sintoma.strip():
        st.error("Por favor describe tu sintoma.")
        st.session_state.pop("resultado", None)
    elif pais != "Panama":
        st.warning(
            "Por ahora la comparacion detallada de hospitales solo esta disponible "
            "para Panama. Prueba seleccionando 'Panama' y una provincia."
        )
        st.session_state.pop("resultado", None)
    else:
        with st.spinner("El agente esta analizando tu caso..."):
            especialidad, urgencia, razonamiento = clasificar_con_agente(sintoma)
            opciones = calcular_opciones(especialidad, provincia, cobertura)

        verificacion = {}
        mensaje = None
        if opciones:
            with st.spinner("Verificando hospitales con IA..."):
                verificacion = verificar_hospitales(
                    [o["hospital"] for o in opciones], provincia
                )
            with st.spinner("Redactando tu recomendacion..."):
                mensaje = redactar_recomendacion(
                    sintoma, especialidad, cobertura, opciones[0], urgencia
                )

        # Se guarda todo en session_state para que el filtro publico/privado
        # (abajo) no dispare de nuevo las llamadas a Gemini en cada click.
        st.session_state["resultado"] = {
            "especialidad": especialidad,
            "urgencia": urgencia,
            "razonamiento": razonamiento,
            "opciones": opciones,
            "verificacion": verificacion,
            "mensaje": mensaje,
            "provincia": provincia,
            "cobertura": cobertura,
        }
        st.session_state["filtro_tipo"] = "Todas"

# ----------------------------------------------------------------------------
# Resultados (leidos desde session_state, no desde variables locales, para
# sobrevivir al rerun que dispara el filtro publico/privado)
# ----------------------------------------------------------------------------
resultado = st.session_state.get("resultado")

if resultado:
    especialidad = resultado["especialidad"]
    urgencia = resultado["urgencia"]
    opciones = resultado["opciones"]
    verificacion = resultado["verificacion"]
    provincia = resultado["provincia"]
    cobertura = resultado["cobertura"]

    if urgencia == "alta":
        st.error(
            "🚨 Posible emergencia detectada. Dirigete de inmediato a la sala de "
            "emergencias mas cercana o llama al 911.",
            icon="🚨",
        )

    if not opciones:
        st.error(
            f"No encontramos opciones en {provincia} que atiendan {especialidad} "
            "con esta cobertura en los datos disponibles. Contacta a tu aseguradora."
        )
    else:
        if resultado["mensaje"]:
            st.success(resultado["mensaje"])

        icono = ICONOS_ESPECIALIDAD.get(especialidad, "🩺")
        col1, col2 = st.columns(2)
        col1.metric("Especialidad sugerida", f"{icono} {especialidad}")
        col2.metric("Nivel de urgencia", urgencia.capitalize())

        with st.expander("¿Como llego el agente a esta conclusion?"):
            st.write(resultado["razonamiento"] or "Sin razonamiento adicional disponible.")

        st.subheader(f"Opciones en {provincia}")

        n_publico = sum(1 for o in opciones if o["categoria"] == "publico")
        n_privado = sum(1 for o in opciones if o["categoria"] == "privado")

        filtro = st.segmented_control(
            "Filtrar por tipo",
            options=["Todas", f"🏥 Publico ({n_publico})", f"🏢 Privado ({n_privado})"],
            key="filtro_tipo",
        )

        if filtro and filtro.startswith("🏥"):
            visibles = [o for o in opciones if o["categoria"] == "publico"]
        elif filtro and filtro.startswith("🏢"):
            visibles = [o for o in opciones if o["categoria"] == "privado"]
        else:
            visibles = opciones

        if not visibles:
            st.info("No hay opciones de este tipo para tu consulta.")

        BADGE_CONFIANZA = {
            "alta": "🟢 Verificado por IA",
            "media": "🟡 Confianza media",
            "baja": "🔴 Revisar con tu aseguradora",
            "sin_verificar": "⚪ No verificado",
        }

        for i, o in enumerate(visibles):
            v = verificacion.get(o["hospital"], {"confianza": "sin_verificar", "nota": ""})
            tag_tipo = (
                '<span class="tag tag-publico">🏥 Publico</span>'
                if o["categoria"] == "publico"
                else '<span class="tag tag-privado">🏢 Privado</span>'
            )
            tag_mejor = (
                '<span class="tag tag-mejor">⭐ Mejor opcion</span>' if i == 0 else ""
            )
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(
                        f'<div class="hosp-nombre">{o["hospital"]}</div>'
                        f'<div style="margin-top:4px;">{tag_tipo}{tag_mejor}</div>'
                        f'<div class="hosp-nota">{o["nota"]}</div>',
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(
                        f'<div class="hosp-precio">${o["costo_paciente"]:.2f}</div>',
                        unsafe_allow_html=True,
                    )
                st.caption(BADGE_CONFIANZA.get(v["confianza"], "⚪ No verificado"))
                if v.get("nota"):
                    st.caption(f"ℹ️ {v['nota']}")

st.divider()
st.caption(
    "Datos de hospitales por provincia obtenidos por busqueda web (TinyFish) y "
    "verificados por IA en cada consulta; montos de copago son simulados para "
    "efectos de la demo del hackIAthon. Verifica siempre con tu aseguradora antes "
    "de acudir."
)
