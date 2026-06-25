import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

// Sammy's lightweight i18n. The English source string IS the lookup key, so any string we
// haven't translated yet still renders (in English) instead of breaking — translations live
// only in the `es`/`zh` tables below. Use {name} placeholders for interpolation.

export type Lang = "en" | "es" | "zh";

export const LANG_STORAGE_KEY = "sammy-lang";

export const LANGUAGES: ReadonlyArray<{
  code: Lang;
  label: string; // English name
  nativeLabel: string; // shown in the picker
  voicePrefix: string; // BCP-47 prefix used to match speechSynthesis voices
  htmlLang: string;
}> = [
  { code: "en", label: "English", nativeLabel: "English", voicePrefix: "en", htmlLang: "en" },
  { code: "es", label: "Spanish", nativeLabel: "Español", voicePrefix: "es", htmlLang: "es" },
  { code: "zh", label: "Chinese", nativeLabel: "中文", voicePrefix: "zh", htmlLang: "zh" },
];

export const voicePrefixFor = (lang: Lang) =>
  LANGUAGES.find((entry) => entry.code === lang)?.voicePrefix ?? "en";

type Dict = Record<string, string>;

const es: Dict = {
  // Loading / login
  "Loading Sammy...": "Cargando Sammy...",
  "Enter the desktop password": "Introduce la contraseña del escritorio",
  Password: "Contraseña",
  "Checking...": "Comprobando...",
  "Log in": "Iniciar sesión",
  "Incorrect password": "Contraseña incorrecta",
  // Empty state
  "Hi, I'm Sammy": "Hola, soy Sammy",
  "Your local companion, here whenever you need me. Everything stays between us.":
    "Tu compañero local, aquí cuando me necesites. Todo queda entre nosotros.",
  "No model selected": "Ningún modelo seleccionado",
  // Sidebar / header / nav
  "New Chat": "Nuevo chat",
  "New chat": "Nuevo chat",
  Settings: "Configuración",
  "Show sidebar": "Mostrar barra lateral",
  "Hide sidebar": "Ocultar barra lateral",
  Chats: "Chats",
  "Close chats": "Cerrar chats",
  "No chats yet": "Aún no hay chats",
  Tools: "Herramientas",
  "Give Sammy the tools needed for the work at hand.": "Dale a Sammy las herramientas necesarias para el trabajo actual.",
  "Search tools and capabilities": "Buscar herramientas y capacidades",
  "Filter tools": "Filtrar herramientas",
  "All tools": "Todas las herramientas",
  "Added tools": "Herramientas añadidas",
  "Needs setup": "Necesita configuración",
  Added: "Añadida",
  Add: "Añadir",
  "Available to {agent}": "Disponible para {agent}",
  Manage: "Administrar",
  "No tools added to this agent yet.": "Aún no se han añadido herramientas a este agente.",
  "Built into Sammy": "Integradas en Sammy",
  "Business & Operations": "Negocios y operaciones",
  "Research & Development": "Investigación y desarrollo",
  "Files & Workspace": "Archivos y espacio de trabajo",
  Communication: "Comunicación",
  Utilities: "Utilidades",
  "No tools match this search.": "Ninguna herramienta coincide con esta búsqueda.",
  "Create your own tool": "Crea tu propia herramienta",
  "Describe the service and what Sammy should do. Sammy will verify, build, and connect the tool locally.":
    "Describe el servicio y lo que Sammy debe hacer. Sammy verificará, creará y conectará la herramienta localmente.",
  "Start building": "Empezar a crear",
  "Tool Build Mode": "Modo de creación de herramientas",
  "View full specifications": "Ver especificaciones completas",
  "Build a tool with Sammy": "Crea una herramienta con Sammy",
  "Describe the service and what you want Sammy to do. Sammy will verify the API, generate a constrained local tool, and enable it for {agent}.":
    "Describe el servicio y lo que quieres que haga Sammy. Sammy verificará la API, generará una herramienta local restringida y la habilitará para {agent}.",
  "What Sammy can build": "Lo que Sammy puede crear",
  "Declarative MCP tools for HTTP APIs with 1 to 20 operations.":
    "Herramientas MCP declarativas para API HTTP con entre 1 y 20 operaciones.",
  "GET, POST, PUT, PATCH, and DELETE operations with structured path, query, and body inputs.":
    "Operaciones GET, POST, PUT, PATCH y DELETE con entradas estructuradas de ruta, consulta y cuerpo.",
  "Public HTTPS services and explicitly approved private or local services.":
    "Servicios HTTPS públicos y servicios privados o locales aprobados explícitamente.",
  Authentication: "Autenticación",
  "No authentication, bearer tokens, API keys, and basic authentication are supported.":
    "Se admite acceso sin autenticación, tokens bearer, claves de API y autenticación básica.",
  "Credentials are stored encrypted and never written into the generated tool.":
    "Las credenciales se guardan cifradas y nunca se escriben en la herramienta generada.",
  "OAuth-only services require a manually reviewed adapter before Sammy can connect them.":
    "Los servicios que solo usan OAuth requieren un adaptador revisado manualmente antes de que Sammy pueda conectarlos.",
  "Safety boundaries": "Límites de seguridad",
  "Sammy verifies official API documentation and never invents endpoints or schemas.":
    "Sammy verifica la documentación oficial de la API y nunca inventa endpoints ni esquemas.",
  "Each tool is locked to its approved host, access level, and network scope. Redirects outside that host are blocked.":
    "Cada herramienta queda restringida a su host, nivel de acceso y alcance de red aprobados. Se bloquean las redirecciones fuera de ese host.",
  "Generated tools cannot contain model-written executable code or overwrite an existing tool.":
    "Las herramientas generadas no pueden contener código ejecutable escrito por el modelo ni sobrescribir una herramienta existente.",
  "Installation and use": "Instalación y uso",
  "Tools are validated, stored locally, and enabled automatically for the current agent.":
    "Las herramientas se validan, se guardan localmente y se habilitan automáticamente para el agente actual.",
  "Public read-only tools can finish in one task. Write access and private networks require explicit approval.":
    "Las herramientas públicas de solo lectura pueden completarse en una tarea. El acceso de escritura y las redes privadas requieren aprobación explícita.",
  "Tool calls use a 20 second timeout and responses are limited to 64 KB.":
    "Las llamadas de herramientas tienen un límite de 20 segundos y las respuestas están limitadas a 64 KB.",
  "What to describe": "Qué debes describir",
  "Include the service name, what Sammy should do, and an official API or documentation link if you have one.":
    "Incluye el nombre del servicio, lo que Sammy debe hacer y un enlace oficial a la API o su documentación si lo tienes.",
  "Building for {agent}": "Creando para {agent}",
  "Describe the tool you want Sammy to build...": "Describe la herramienta que quieres que Sammy cree...",
  "Other agents": "Otros agentes",
  Delete: "Eliminar",
  Pin: "Fijar",
  Unpin: "Desfijar",
  "Export conversation": "Exportar conversación",
  "Network access is active on {host}, but Sammy is not password protected yet. Only use this on a trusted private network.":
    "El acceso por red está activo en {host}, pero Sammy todavía no está protegido con contraseña. Úsalo solo en una red privada de confianza.",
  // Composer
  "Ask Sammy...": "Pregúntale a Sammy...",
  "Listening...": "Escuchando...",
  "Attach file": "Adjuntar archivo",
  "{n} attached": "{n} adjuntos",
  Thinking: "Pensando",
  Reasoning: "Razonamiento",
  Normal: "Normal",
  Stop: "Detener",
  Send: "Enviar",
  "Stop voice input": "Detener entrada de voz",
  "Voice input": "Entrada de voz",
  "Voice input is not supported": "La entrada de voz no es compatible",
  "Sammy is speaking": "Sammy está hablando",
  'Hands-free is on — say "Sammy …"': 'El modo manos libres está activo: di "Sammy …"',
  'Hands-free voice — say "Sammy …"': 'Voz manos libres: di "Sammy …"',
  "Active tools": "Herramientas activas",
  "Add tool": "Añadir herramienta",
  Back: "Atrás",
  "No active tools": "No hay herramientas activas",
  "All tools are active": "Todas las herramientas están activas",
  Reconnect: "Reconectar",
  Setup: "Configurar",
  "Reconnect tool": "Reconectar herramienta",
  "Add OAuth credentials": "Añadir credenciales de OAuth",
  // Generation ledger / statuses
  "Starting task": "Iniciando tarea",
  "Preparing the background job.": "Preparando el trabajo en segundo plano.",
  "Reconnecting to task": "Reconectando con la tarea",
  "Sammy is still working in the background.": "Sammy sigue trabajando en segundo plano.",
  "Rejoining active task": "Reincorporándose a la tarea activa",
  "Replaying Sammy's work from the background job.":
    "Reproduciendo el trabajo de Sammy desde el proceso en segundo plano.",
  Stopping: "Deteniendo",
  "Sammy will stop after any tool call already in progress returns.":
    "Sammy se detendrá cuando termine cualquier llamada a una herramienta que ya esté en curso.",
  "Step {n}": "Paso {n}",
  "Part {n}": "Parte {n}",
  "Sammy is still working. Stop the current task before switching conversations.":
    "Sammy sigue trabajando. Detén la tarea actual antes de cambiar de conversación.",
  "Sammy is still working. Stop the current task before starting another chat.":
    "Sammy sigue trabajando. Detén la tarea actual antes de iniciar otro chat.",
  "Stop the active task before deleting this conversation.":
    "Detén la tarea activa antes de eliminar esta conversación.",
  'Heard: "{text}"': 'Escuché: "{text}"',
  "Got it — working on it!": "¡Entendido, manos a la obra!",
  "On it!": "¡Enseguida!",
  "Sure, give me a sec.": "Claro, dame un segundo.",
  "Okay, let me look into that.": "Vale, déjame ver eso.",
  "Got it, one moment.": "Entendido, un momento.",
  "Alright, on it now.": "Muy bien, ya me pongo.",
  "Hi, I'm here. Go ahead.": "Hola, aquí estoy. Adelante.",
  "Hi, I'm on it.": "Hola, ya me pongo.",
  'Yes? Go ahead. No need to say "{name}" again.':
    '¿Sí? Adelante. No hace falta decir "{name}" otra vez.',
  "Heard it. Keep speaking or tap the mic to stop.":
    "Lo escuché. Sigue hablando o toca el micrófono para parar.",
  "Voice input is not supported in this browser.":
    "La entrada de voz no es compatible con este navegador.",
  "Voice input needs localhost or HTTPS.": "La entrada de voz necesita localhost o HTTPS.",
  "Sammy did not catch anything. Tap the mic and try again.":
    "Sammy no captó nada. Toca el micrófono e inténtalo de nuevo.",
  // Tool strip
  Using: "Usando",
  Used: "Usó",
  " {n} times": " {n} veces",
  // Reliability notices
  "Reply stopped": "Respuesta detenida",
  "Generation was stopped before Sammy finished.":
    "La generación se detuvo antes de que Sammy terminara.",
  "No final reply was produced": "No se produjo una respuesta final",
  "The model produced reasoning but did not return a final answer.":
    "El modelo razonó pero no devolvió una respuesta final.",
  "Reply ended with an error": "La respuesta terminó con un error",
  "Sammy could not produce a reply": "Sammy no pudo generar una respuesta",
  "The stream ended before Sammy could finish.":
    "La transmisión terminó antes de que Sammy pudiera acabar.",
  "Reply may be incomplete": "La respuesta puede estar incompleta",
  "Sammy did not receive a clean completion signal from the model, so the last response may be cut off.":
    "Sammy no recibió una señal de finalización limpia del modelo, por lo que la última respuesta puede estar cortada.",
  // Settings shell
  "Tune how Sammy works": "Ajusta cómo funciona Sammy",
  "Close settings": "Cerrar configuración",
  General: "General",
  "Model, appearance, security": "Modelo, apariencia, seguridad",
  "Model, appearance, language": "Modelo, apariencia, idioma",
  Security: "Seguridad",
  "Password & API keys": "Contraseña y claves de API",
  "Phone access": "Acceso desde el teléfono",
  "Let your phone reach Sammy on your network. Stays on across restarts.":
    "Permite que tu teléfono acceda a Sammy en tu red. Se mantiene activo tras reiniciar.",
  "Restarting Sammy… this takes a few seconds.": "Reiniciando Sammy… esto tarda unos segundos.",
  "Phone access is on.": "El acceso desde el teléfono está activado.",
  "Phone access is off.": "El acceso desde el teléfono está desactivado.",
  "On this Mac, tap Sammy's name in the top bar for the phone link.":
    "En este Mac, toca el nombre de Sammy en la barra superior para ver el enlace del teléfono.",
  Agents: "Agentes",
  "Personas and their tools": "Personalidades y sus herramientas",
  Memory: "Memoria",
  "What Sammy remembers": "Lo que Sammy recuerda",
  "Connected apps and keys": "Apps conectadas y claves",
  Docs: "Guías",
  "Setup guides & key links": "Guías de configuración y enlaces de claves",
  "Find my keys": "Encontrar mis claves",
  "Changes take effect once you save.": "Los cambios se aplican cuando guardas.",
  "Most changes save automatically.": "La mayoría de los cambios se guardan automáticamente.",
  Saved: "Guardado",
  Done: "Listo",
  "Save changes": "Guardar cambios",
  "Save agent": "Guardar agente",
  "Create agent": "Crear agente",
  // My Bestie
  "My Bestie": "Mi Amiguito",
  "Hi, I'm {name}": "Hola, soy {name}",
  "Build a tool with {name}": "Crea una herramienta con {name}",
  "Hi, I'm {name}. This is how I'll sound.": "Hola, soy {name}. Así es como sonaré.",
  "Choose who you're chatting with, or create your own.": "Elige con quién hablas o crea el tuyo.",
  "Connect Gemini to restyle your photos": "Conecta Gemini para reestilizar tus fotos",
  "Without it, your photo is used as-is. Tap to add a key in Settings.":
    "Sin él, tu foto se usa tal cual. Toca para añadir una clave en Configuración.",
  "Your original local companion.": "Tu compañero local original.",
  Active: "Activo",
  "No personality set.": "Sin personalidad definida.",
  "Chatting now": "Hablando ahora",
  "Use this bestie": "Usar este amiguito",
  "Create a bestie": "Crear un amiguito",
  "Edit bestie": "Editar amiguito",
  "Bestie avatar": "Avatar del amiguito",
  "Upload photo": "Subir foto",
  "Stylizing…": "Estilizando…",
  "Stylize with Gemini": "Estilizar con Gemini",
  "e.g. Mochi": "p. ej. Mochi",
  Personality: "Personalidad",
  "Describe how this bestie talks and behaves.": "Describe cómo habla y se comporta este amiguito.",
  "Saving…": "Guardando…",
  "Save bestie": "Guardar amiguito",
  "Create bestie": "Crear amiguito",
  "Stylized!": "¡Estilizado!",
  "Saved your photo. Connect Gemini in Settings to restyle it.":
    "Guardamos tu foto. Conecta Gemini en Configuración para reestilizarla.",
  "Bestie saved": "Amiguito guardado",
  "Bestie deleted": "Amiguito eliminado",
  "Delete {name}?": "¿Eliminar a {name}?",
  // Settings · Gemini
  Gemini: "Gemini",
  "Restyle your photos into besties. The key stays on your Mac.":
    "Reestiliza tus fotos como amiguitos. La clave se queda en tu Mac.",
  "Paste your Gemini API key": "Pega tu clave de API de Gemini",
  "Get a free key at aistudio.google.com.": "Consigue una clave gratis en aistudio.google.com.",
  // Mobile connection popup
  "Open on your phone": "Abre en tu teléfono",
  "On the same Wi-Fi as this Mac.": "En la misma Wi-Fi que este Mac.",
  Copy: "Copiar",
  or: "o",
  'Sammy isn\'t sharing on the network yet. Run "sammy lan" in Terminal, then reopen this.':
    'Sammy aún no está compartido en la red. Ejecuta "sammy lan" en la Terminal y vuelve a abrir esto.',
  "From any network": "Desde cualquier red",
  "Install Tailscale on this Mac and your phone (same account) to get a link that works on cellular and other Wi-Fi. It'll show up here automatically.":
    "Instala Tailscale en este Mac y en tu teléfono (con la misma cuenta) para obtener un enlace que funciona con datos móviles y otras redes Wi-Fi. Aparecerá aquí automáticamente.",
  "Secure (HTTPS) — works on cellular too.": "Seguro (HTTPS): también funciona con datos móviles.",
  'For a secure (https) link, enable HTTPS in the Tailscale admin, then run "sammy serve".':
    'Para un enlace seguro (https), activa HTTPS en el panel de Tailscale y luego ejecuta "sammy serve".',
  // First-run password gate
  "Protects Sammy — required before anyone (or your phone) can open it.":
    "Protege a Sammy: es obligatorio antes de que alguien (o tu teléfono) pueda abrirlo.",
  "New password": "Nueva contraseña",
  "Confirm password": "Confirmar contraseña",
  "Setting…": "Guardando…",
  "Use at least 4 characters.": "Usa al menos 4 caracteres.",
  "Passwords don't match.": "Las contraseñas no coinciden.",
  "Save key": "Guardar clave",
  "Set password": "Establecer contraseña",
  "Update password": "Actualizar contraseña",
  // Settings · General
  "Model & responses": "Modelo y respuestas",
  "Choose a default model and shape how Sammy replies.":
    "Elige un modelo predeterminado y define cómo responde Sammy.",
  "Default model": "Modelo predeterminado",
  "Auto select": "Selección automática",
  "New chats use this unless an agent sets its own model.":
    "Los chats nuevos usan este modelo salvo que un agente defina el suyo.",
  "Context window": "Ventana de contexto",
  "{n} tokens": "{n} tokens",
  "How much conversation Sammy keeps in mind at once.":
    "Cuánta conversación tiene Sammy presente a la vez.",
  "Max tokens": "Tokens máximos",
  "Longest reply Sammy will write.": "La respuesta más larga que escribirá Sammy.",
  Temperature: "Temperatura",
  "Lower is focused, higher is playful.": "Más bajo es enfocado, más alto es creativo.",
  Appearance: "Apariencia",
  "Pick the mood that suits your room.": "Elige el ambiente que combine con tu espacio.",
  Light: "Claro",
  "Warm daylight": "Luz cálida de día",
  Dark: "Oscuro",
  "Warm and dim": "Cálido y tenue",
  // Settings · Language (new)
  Language: "Idioma",
  "Choose the language Sammy speaks and shows.":
    "Elige el idioma que Sammy habla y muestra.",
  "The voice list adapts to this language.": "La lista de voces se adapta a este idioma.",
  English: "Inglés",
  Spanish: "Español",
  Chinese: "Chino",
  // Settings · Voice
  Voice: "Voz",
  "How Sammy sounds when reading replies aloud in hands-free mode.":
    "Cómo suena Sammy al leer las respuestas en voz alta en modo manos libres.",
  "Automatic (cute default)": "Automática (predeterminada y tierna)",
  "Automatic (premium default)": "Automática (premium por defecto)",
  "Open voice settings": "Abrir ajustes de voz",
  "Refresh": "Actualizar",
  "Opened macOS voice settings.": "Se abrieron los ajustes de voz de macOS.",
  "Only natural premium voices are shown. Add more in System Settings → Accessibility → Spoken Content.":
    "Solo se muestran voces premium naturales. Añade más en Ajustes del Sistema → Accesibilidad → Contenido hablado.",
  "No premium {language} voices installed yet. Premium voices are free from macOS and sound far more natural than the built-in ones.":
    "Aún no hay voces premium de {language} instaladas. Las voces premium son gratuitas en macOS y suenan mucho más naturales que las integradas.",
  "Open voice settings below (System Settings → Accessibility → Spoken Content → System Voice → Manage Voices).":
    "Abre los ajustes de voz abajo (Ajustes del Sistema → Accesibilidad → Contenido hablado → Voz del sistema → Gestionar voces).",
  "Download a voice marked “(Premium)” — e.g. Ava, Zoe, or Serena.":
    "Descarga una voz marcada como «(Premium)» — p. ej. Ava, Zoe o Serena.",
  "Come back and tap Refresh.": "Vuelve y toca Actualizar.",
  "Automatic (best available)": "Automática (la mejor disponible)",
  Premium: "Premium",
  Siri: "Siri",
  "Other voices": "Otras voces",
  "Premium and Siri voices are listed first — they sound the most natural.":
    "Las voces Premium y Siri aparecen primero — son las que suenan más naturales.",
  "Want a more natural voice? Premium voices are free from macOS — download one, then tap Refresh.":
    "¿Quieres una voz más natural? Las voces Premium son gratuitas en macOS — descarga una y toca Actualizar.",
  "Only warm, cute female voices are shown. Add more in System Settings → Accessibility → Spoken Content.":
    "Solo se muestran voces femeninas cálidas y tiernas. Añade más en Ajustes del sistema → Accesibilidad → Contenido hablado.",
  "No {language} voices are installed yet. Add one in System Settings → Accessibility → Spoken Content.":
    "Aún no hay voces en {language} instaladas. Añade una en Ajustes del sistema → Accesibilidad → Contenido hablado.",
  "Speaking speed": "Velocidad de habla",
  "Test voice": "Probar voz",
  "Hi, I'm Sammy. This is how I'll sound.": "Hola, soy Sammy. Así es como sonaré.",
  "Spoken replies aren't supported in this browser.":
    "Las respuestas habladas no son compatibles con este navegador.",
  // ElevenLabs
  "ElevenLabs voices": "Voces de ElevenLabs",
  "Premium cloud voices via your ElevenLabs account.":
    "Voces premium en la nube con tu cuenta de ElevenLabs.",
  "Use ElevenLabs voices": "Usar voces de ElevenLabs",
  "API key": "Clave API",
  "Paste your ElevenLabs API key": "Pega tu clave API de ElevenLabs",
  "Connected — leave blank to keep current key":
    "Conectado: déjalo en blanco para mantener la clave actual",
  "Not connected": "Sin conectar",
  "The key stays on your Mac, used only by the local Sammy backend — it never reaches the browser.":
    "La clave se queda en tu Mac y solo la usa el backend local de Sammy; nunca llega al navegador.",
  "Loading voices…": "Cargando voces…",
  "No voices found in your ElevenLabs account.": "No se encontraron voces en tu cuenta de ElevenLabs.",
  "Paste a voice ID": "Pega un ID de voz",
  "Couldn't list your voices — paste a voice ID from your ElevenLabs dashboard instead.":
    "No se pudieron listar tus voces; pega un ID de voz desde tu panel de ElevenLabs.",
  "Default voice": "Voz predeterminada",
  "Voice playback failed. {detail}": "Falló la reproducción de voz. {detail}",
  // Voice authentication (Picovoice Eagle)
  "Only respond to my voice": "Responder solo a mi voz",
  "On-device speaker check (Picovoice Eagle) — ignores other people's voices.":
    "Verificación de hablante en el dispositivo (Picovoice Eagle): ignora las voces de otras personas.",
  "Picovoice AccessKey": "AccessKey de Picovoice",
  "Voice enrolled": "Voz registrada",
  "Not enrolled": "Sin registrar",
  "Paste your free Picovoice AccessKey": "Pega tu AccessKey gratuita de Picovoice",
  "Runs fully on-device. Get a free key at console.picovoice.ai.":
    "Funciona totalmente en el dispositivo. Consigue una clave gratis en console.picovoice.ai.",
  "Enrolling… {n}%": "Registrando… {n}%",
  "Re-enroll my voice": "Volver a registrar mi voz",
  "Enroll my voice": "Registrar mi voz",
  "Speak naturally for ~20 seconds.": "Habla con naturalidad durante unos 20 segundos.",
  // Settings · Login password
  "Login password": "Contraseña de acceso",
  "Ask for a password before opening Sammy.":
    "Pedir una contraseña antes de abrir Sammy.",
  On: "Activado",
  Off: "Desactivado",
  "Leave blank to keep current password": "Déjalo en blanco para mantener la contraseña actual",
  "Set a password": "Establecer una contraseña",
  "Remove password": "Quitar contraseña",
  // Settings · Agents
  Agent: "Agente",
  Name: "Nombre",
  Icon: "Icono",
  "Upload image": "Subir imagen",
  "Uploading...": "Subiendo...",
  "Use auto icon": "Usar icono automático",
  Model: "Modelo",
  "Use default": "Usar predeterminado",
  "System prompt": "Instrucción del sistema",
  "Allowed tools": "Herramientas permitidas",
  "{n} enabled": "{n} activados",
  All: "Todos",
  "No tools are on": "No hay herramientas activadas",
  "No tools are off": "No hay herramientas desactivadas",
  "Choose an image file": "Elige un archivo de imagen",
  "Icon uploaded": "Icono subido",
  // Settings · Tools
  "Save credentials": "Guardar credenciales",
  "Reconnect OAuth": "Reconectar OAuth",
  "Connect OAuth": "Conectar OAuth",
  Select: "Seleccionar",
  "Configure {name}": "Configurar {name}",
  adapter: "adaptador",
  "Active: {n}": "Activo: {n}",
  "Not active": "Inactivo",
  Callable: "Invocable",
  "Context only": "Solo contexto",
  "No auth": "Sin autenticación",
  Connected: "Conectado",
  "Needs reconnect": "Necesita reconexión",
  "Needs auth": "Necesita autenticación",
  // Memory
  "Local memory": "Memoria local",
  "Stored in SQLite on this Mac. Soul entries require manual edits.":
    "Almacenada en SQLite en este Mac. Las entradas de alma requieren ediciones manuales.",
  "{n} active": "{n} activas",
  "Post-turn review": "Revisión tras cada turno",
  auto: "auto",
  ask: "preguntar",
  "Recall past chats": "Recordar chats anteriores",
  User: "Usuario",
  Soul: "Alma",
  "Add a shared identity or behavior rule":
    "Añade una identidad compartida o una regla de comportamiento",
  "Add a durable fact or preference": "Añade un dato o preferencia duradera",
  "Add memory": "Añadir memoria",
  active: "activas",
  pending: "pendientes",
  archived: "archivadas",
  "Filter memory": "Filtrar memoria",
  "Loading memory...": "Cargando memoria...",
  "No {filter} memories": "No hay memorias {filter}",
  "{n}% confidence": "{n}% de confianza",
  Local: "Local",
  "recalled {n}x": "recordada {n} veces",
  "Edit memory": "Editar memoria",
  "Save edit": "Guardar edición",
  "Approve memory": "Aprobar memoria",
  "Archive memory": "Archivar memoria",
  "Delete memory": "Eliminar memoria",
  "Memory scope": "Ámbito de la memoria",
  "Memory added locally": "Memoria añadida localmente",
  "Memory approved": "Memoria aprobada",
  "Memory archived": "Memoria archivada",
  "Memory corrected": "Memoria corregida",
  "Memory deleted": "Memoria eliminada",
  // Agent picker
  "Send with agent": "Enviar con agente",
  "New chat agent": "Agente para el nuevo chat",
  "{n} available": "{n} disponibles",
  Close: "Cerrar",
  "Close agent picker": "Cerrar selector de agentes",
  "1 tool": "1 herramienta",
  "{n} tools": "{n} herramientas",
  "1 tool active": "1 herramienta activa",
  "{n} tools active": "{n} herramientas activas",
  // Misc
  off: "desactivado",
  'Saving "{file}" memory': 'Guardando la memoria "{file}"',
  memory: "memoria",
  "Copy prompt": "Copiar petición",
  "Copy message": "Copiar mensaje",
  "Regenerate from this message": "Regenerar desde este mensaje",
  "Copy code": "Copiar código",
  'Backend unavailable. Run "sammy restart" in Terminal. {detail}':
    'El backend no está disponible. Ejecuta "sammy restart" en la Terminal. {detail}',
};

const zh: Dict = {
  // Loading / login
  "Loading Sammy...": "正在加载 Sammy…",
  "Enter the desktop password": "请输入桌面密码",
  Password: "密码",
  "Checking...": "正在检查…",
  "Log in": "登录",
  "Incorrect password": "密码错误",
  // Empty state
  "Hi, I'm Sammy": "你好，我是 Sammy",
  "Your local companion, here whenever you need me. Everything stays between us.":
    "你的本地伙伴，随时都在这里陪你。一切都只在你我之间。",
  "No model selected": "未选择模型",
  // Sidebar / header / nav
  "New Chat": "新对话",
  "New chat": "新对话",
  Settings: "设置",
  "Show sidebar": "显示侧栏",
  "Hide sidebar": "隐藏侧栏",
  Chats: "对话",
  "Close chats": "关闭对话",
  "No chats yet": "还没有对话",
  Tools: "工具",
  "Give Sammy the tools needed for the work at hand.": "为 Sammy 配置当前工作所需的工具。",
  "Search tools and capabilities": "搜索工具和能力",
  "Filter tools": "筛选工具",
  "All tools": "全部工具",
  "Added tools": "已添加的工具",
  "Needs setup": "需要设置",
  Added: "已添加",
  Add: "添加",
  "Available to {agent}": "{agent} 可用",
  Manage: "管理",
  "No tools added to this agent yet.": "此智能体尚未添加工具。",
  "Built into Sammy": "Sammy 内置",
  "Business & Operations": "业务与运营",
  "Research & Development": "研究与开发",
  "Files & Workspace": "文件与工作区",
  Communication: "沟通",
  Utilities: "实用工具",
  "No tools match this search.": "没有符合搜索条件的工具。",
  "Create your own tool": "创建自定义工具",
  "Describe the service and what Sammy should do. Sammy will verify, build, and connect the tool locally.":
    "描述服务以及希望 Sammy 完成的操作。Sammy 会验证、构建并在本地连接该工具。",
  "Start building": "开始构建",
  "Tool Build Mode": "工具构建模式",
  "View full specifications": "查看完整规范",
  "Build a tool with Sammy": "和 Sammy 一起构建工具",
  "Describe the service and what you want Sammy to do. Sammy will verify the API, generate a constrained local tool, and enable it for {agent}.":
    "描述服务以及你希望 Sammy 完成的操作。Sammy 会验证 API，生成受约束的本地工具，并为 {agent} 自动启用。",
  "What Sammy can build": "Sammy 可以构建的工具",
  "Declarative MCP tools for HTTP APIs with 1 to 20 operations.":
    "面向 HTTP API 的声明式 MCP 工具，每个工具可包含 1 到 20 个操作。",
  "GET, POST, PUT, PATCH, and DELETE operations with structured path, query, and body inputs.":
    "支持 GET、POST、PUT、PATCH 和 DELETE 操作，并使用结构化的路径、查询和请求体输入。",
  "Public HTTPS services and explicitly approved private or local services.":
    "支持公共 HTTPS 服务，以及经过明确批准的私有或本地服务。",
  Authentication: "身份验证",
  "No authentication, bearer tokens, API keys, and basic authentication are supported.":
    "支持无身份验证、Bearer Token、API 密钥和基本身份验证。",
  "Credentials are stored encrypted and never written into the generated tool.":
    "凭据会加密保存，绝不会写入生成的工具。",
  "OAuth-only services require a manually reviewed adapter before Sammy can connect them.":
    "仅支持 OAuth 的服务需要经过人工审核的适配器，Sammy 才能连接。",
  "Safety boundaries": "安全边界",
  "Sammy verifies official API documentation and never invents endpoints or schemas.":
    "Sammy 会核对官方 API 文档，绝不会虚构端点或数据结构。",
  "Each tool is locked to its approved host, access level, and network scope. Redirects outside that host are blocked.":
    "每个工具都会被限制在已批准的主机、访问级别和网络范围内。跳转到该主机之外的重定向会被阻止。",
  "Generated tools cannot contain model-written executable code or overwrite an existing tool.":
    "生成的工具不能包含模型编写的可执行代码，也不能覆盖现有工具。",
  "Installation and use": "安装与使用",
  "Tools are validated, stored locally, and enabled automatically for the current agent.":
    "工具会经过验证、保存在本地，并自动为当前智能体启用。",
  "Public read-only tools can finish in one task. Write access and private networks require explicit approval.":
    "公共只读工具可以在一次任务中完成。写入权限和私有网络需要明确批准。",
  "Tool calls use a 20 second timeout and responses are limited to 64 KB.":
    "工具调用的超时时间为 20 秒，响应大小限制为 64 KB。",
  "What to describe": "需要描述的内容",
  "Include the service name, what Sammy should do, and an official API or documentation link if you have one.":
    "请说明服务名称、希望 Sammy 完成的操作，并在有条件时提供官方 API 或文档链接。",
  "Building for {agent}": "正在为 {agent} 构建",
  "Describe the tool you want Sammy to build...": "描述你希望 Sammy 构建的工具...",
  "Other agents": "其他智能体",
  Delete: "删除",
  Pin: "置顶",
  Unpin: "取消置顶",
  "Export conversation": "导出对话",
  "Network access is active on {host}, but Sammy is not password protected yet. Only use this on a trusted private network.":
    "已在 {host} 上启用网络访问，但 Sammy 尚未设置密码保护。请仅在受信任的私有网络中使用。",
  // Composer
  "Ask Sammy...": "问问 Sammy…",
  "Listening...": "正在聆听…",
  "Attach file": "添加附件",
  "{n} attached": "已添加 {n} 个",
  Thinking: "思考中",
  Reasoning: "推理",
  Normal: "普通",
  Stop: "停止",
  Send: "发送",
  "Stop voice input": "停止语音输入",
  "Voice input": "语音输入",
  "Voice input is not supported": "不支持语音输入",
  "Sammy is speaking": "Sammy 正在说话",
  'Hands-free is on — say "Sammy …"': "免提模式已开启——说“Sammy …”",
  'Hands-free voice — say "Sammy …"': "免提语音——说“Sammy …”",
  "Active tools": "已启用的工具",
  "Add tool": "添加工具",
  Back: "返回",
  "No active tools": "没有已启用的工具",
  "All tools are active": "所有工具均已启用",
  Reconnect: "重新连接",
  Setup: "设置",
  "Reconnect tool": "重新连接工具",
  "Add OAuth credentials": "添加 OAuth 凭据",
  // Generation ledger / statuses
  "Starting task": "正在开始任务",
  "Preparing the background job.": "正在准备后台任务。",
  "Reconnecting to task": "正在重新连接任务",
  "Sammy is still working in the background.": "Sammy 仍在后台工作。",
  "Rejoining active task": "正在重新加入进行中的任务",
  "Replaying Sammy's work from the background job.": "正在从后台任务回放 Sammy 的工作。",
  Stopping: "正在停止",
  "Sammy will stop after any tool call already in progress returns.":
    "Sammy 会在当前正在进行的工具调用返回后停止。",
  "Step {n}": "第 {n} 步",
  "Part {n}": "第 {n} 部分",
  "Sammy is still working. Stop the current task before switching conversations.":
    "Sammy 仍在工作。切换对话前请先停止当前任务。",
  "Sammy is still working. Stop the current task before starting another chat.":
    "Sammy 仍在工作。开始新对话前请先停止当前任务。",
  "Stop the active task before deleting this conversation.":
    "删除此对话前请先停止进行中的任务。",
  'Heard: "{text}"': "听到：“{text}”",
  "Got it — working on it!": "好的，这就去做！",
  "On it!": "马上！",
  "Sure, give me a sec.": "好的，稍等一下。",
  "Okay, let me look into that.": "好的，我来看看。",
  "Got it, one moment.": "明白了，请稍等。",
  "Alright, on it now.": "好，我现在就办。",
  "Hi, I'm here. Go ahead.": "你好，我在。请说。",
  "Hi, I'm on it.": "你好，我这就去做。",
  'Yes? Go ahead. No need to say "{name}" again.':
    "在呢，请说。不用再说“{name}”。",
  "Heard it. Keep speaking or tap the mic to stop.":
    "听到了。继续说，或点按麦克风停止。",
  "Voice input is not supported in this browser.": "此浏览器不支持语音输入。",
  "Voice input needs localhost or HTTPS.": "语音输入需要 localhost 或 HTTPS。",
  "Sammy did not catch anything. Tap the mic and try again.":
    "Sammy 没有听清。点按麦克风再试一次。",
  // Tool strip
  Using: "正在使用",
  Used: "已使用",
  " {n} times": " {n} 次",
  // Reliability notices
  "Reply stopped": "回复已停止",
  "Generation was stopped before Sammy finished.": "在 Sammy 完成之前生成被停止。",
  "No final reply was produced": "未生成最终回复",
  "The model produced reasoning but did not return a final answer.":
    "模型进行了推理，但没有给出最终答案。",
  "Reply ended with an error": "回复因错误而结束",
  "Sammy could not produce a reply": "Sammy 无法生成回复",
  "The stream ended before Sammy could finish.": "数据流在 Sammy 完成之前就结束了。",
  "Reply may be incomplete": "回复可能不完整",
  "Sammy did not receive a clean completion signal from the model, so the last response may be cut off.":
    "Sammy 没有从模型收到干净的完成信号，因此最后的回复可能被截断。",
  // Settings shell
  "Tune how Sammy works": "调整 Sammy 的工作方式",
  "Close settings": "关闭设置",
  General: "通用",
  "Model, appearance, security": "模型、外观、安全",
  "Model, appearance, language": "模型、外观、语言",
  Security: "安全",
  "Password & API keys": "密码和 API 密钥",
  "Phone access": "手机访问",
  "Let your phone reach Sammy on your network. Stays on across restarts.":
    "让你的手机在网络上访问 Sammy。重启后依然保持开启。",
  "Restarting Sammy… this takes a few seconds.": "正在重启 Sammy……需要几秒钟。",
  "Phone access is on.": "手机访问已开启。",
  "Phone access is off.": "手机访问已关闭。",
  "On this Mac, tap Sammy's name in the top bar for the phone link.":
    "在这台 Mac 上，点按顶部栏中 Sammy 的名字即可获取手机链接。",
  Agents: "智能体",
  "Personas and their tools": "角色及其工具",
  Memory: "记忆",
  "What Sammy remembers": "Sammy 记住的内容",
  "Connected apps and keys": "已连接的应用和密钥",
  Docs: "文档",
  "Setup guides & key links": "设置指南和密钥链接",
  "Find my keys": "查找我的密钥",
  "Changes take effect once you save.": "保存后更改即生效。",
  "Most changes save automatically.": "大多数更改会自动保存。",
  Saved: "已保存",
  Done: "完成",
  "Save changes": "保存更改",
  "Save agent": "保存智能体",
  "Create agent": "创建智能体",
  // My Bestie
  "My Bestie": "我的伙伴",
  "Hi, I'm {name}": "你好，我是 {name}",
  "Build a tool with {name}": "和 {name} 一起构建工具",
  "Hi, I'm {name}. This is how I'll sound.": "你好，我是 {name}。我听起来就是这样。",
  "Choose who you're chatting with, or create your own.": "选择和谁聊天，或创建你自己的伙伴。",
  "Connect Gemini to restyle your photos": "连接 Gemini 来重新风格化你的照片",
  "Without it, your photo is used as-is. Tap to add a key in Settings.":
    "没有它，你的照片会按原样使用。点击在设置中添加密钥。",
  "Your original local companion.": "你最初的本地伙伴。",
  Active: "使用中",
  "No personality set.": "未设置性格。",
  "Chatting now": "正在聊天",
  "Use this bestie": "使用这个伙伴",
  "Create a bestie": "创建伙伴",
  "Edit bestie": "编辑伙伴",
  "Bestie avatar": "伙伴头像",
  "Upload photo": "上传照片",
  "Stylizing…": "风格化中…",
  "Stylize with Gemini": "用 Gemini 风格化",
  "e.g. Mochi": "例如 Mochi",
  Personality: "性格",
  "Describe how this bestie talks and behaves.": "描述这个伙伴说话和行为的方式。",
  "Saving…": "保存中…",
  "Save bestie": "保存伙伴",
  "Create bestie": "创建伙伴",
  "Stylized!": "已风格化！",
  "Saved your photo. Connect Gemini in Settings to restyle it.":
    "已保存你的照片。在设置中连接 Gemini 以重新风格化。",
  "Bestie saved": "伙伴已保存",
  "Bestie deleted": "伙伴已删除",
  "Delete {name}?": "删除 {name}？",
  // Settings · Gemini
  Gemini: "Gemini",
  "Restyle your photos into besties. The key stays on your Mac.":
    "把你的照片风格化成伙伴。密钥只保存在你的 Mac 上。",
  "Paste your Gemini API key": "粘贴你的 Gemini API 密钥",
  "Get a free key at aistudio.google.com.": "在 aistudio.google.com 获取免费密钥。",
  // Mobile connection popup
  "Open on your phone": "在手机上打开",
  "On the same Wi-Fi as this Mac.": "与这台 Mac 连接同一个 Wi-Fi。",
  Copy: "复制",
  or: "或",
  'Sammy isn\'t sharing on the network yet. Run "sammy lan" in Terminal, then reopen this.':
    'Sammy 还没有在网络上共享。在终端运行 "sammy lan"，然后重新打开。',
  "From any network": "从任意网络",
  "Install Tailscale on this Mac and your phone (same account) to get a link that works on cellular and other Wi-Fi. It'll show up here automatically.":
    "在这台 Mac 和你的手机上安装 Tailscale（使用同一账户），即可获得在移动数据和其他 Wi-Fi 下都能用的链接。它会自动显示在这里。",
  "Secure (HTTPS) — works on cellular too.": "安全（HTTPS）——移动数据下也能用。",
  'For a secure (https) link, enable HTTPS in the Tailscale admin, then run "sammy serve".':
    '若需要安全（https）链接，请在 Tailscale 管理后台启用 HTTPS，然后运行 "sammy serve"。',
  // First-run password gate
  "Protects Sammy — required before anyone (or your phone) can open it.":
    "保护 Sammy——在任何人（或你的手机）打开它之前都必须设置。",
  "New password": "新密码",
  "Confirm password": "确认密码",
  "Setting…": "保存中…",
  "Use at least 4 characters.": "至少使用 4 个字符。",
  "Passwords don't match.": "两次输入的密码不一致。",
  "Save key": "保存密钥",
  "Set password": "设置密码",
  "Update password": "更新密码",
  // Settings · General
  "Model & responses": "模型与回复",
  "Choose a default model and shape how Sammy replies.":
    "选择默认模型并调整 Sammy 的回复方式。",
  "Default model": "默认模型",
  "Auto select": "自动选择",
  "New chats use this unless an agent sets its own model.":
    "新对话默认使用此模型，除非智能体设置了自己的模型。",
  "Context window": "上下文窗口",
  "{n} tokens": "{n} 个令牌",
  "How much conversation Sammy keeps in mind at once.": "Sammy 一次能记住多少对话内容。",
  "Max tokens": "最大令牌数",
  "Longest reply Sammy will write.": "Sammy 单次回复的最大长度。",
  Temperature: "温度",
  "Lower is focused, higher is playful.": "数值越低越专注，越高越活泼。",
  Appearance: "外观",
  "Pick the mood that suits your room.": "选择适合你房间氛围的主题。",
  Light: "浅色",
  "Warm daylight": "温暖的日光",
  Dark: "深色",
  "Warm and dim": "温暖而柔和",
  // Settings · Language (new)
  Language: "语言",
  "Choose the language Sammy speaks and shows.": "选择 Sammy 使用和显示的语言。",
  "The voice list adapts to this language.": "语音列表会随此语言变化。",
  English: "英语",
  Spanish: "西班牙语",
  Chinese: "中文",
  // Settings · Voice
  Voice: "语音",
  "How Sammy sounds when reading replies aloud in hands-free mode.":
    "Sammy 在免提模式下朗读回复时的声音。",
  "Automatic (cute default)": "自动（可爱默认）",
  "Automatic (premium default)": "自动（高级默认）",
  "Open voice settings": "打开语音设置",
  "Refresh": "刷新",
  "Opened macOS voice settings.": "已打开 macOS 语音设置。",
  "Only natural premium voices are shown. Add more in System Settings → Accessibility → Spoken Content.":
    "仅显示自然的高级语音。在「系统设置 → 辅助功能 → 朗读内容」中添加更多。",
  "No premium {language} voices installed yet. Premium voices are free from macOS and sound far more natural than the built-in ones.":
    "尚未安装高级 {language} 语音。高级语音在 macOS 上免费，听起来比内置语音自然得多。",
  "Open voice settings below (System Settings → Accessibility → Spoken Content → System Voice → Manage Voices).":
    "在下方打开语音设置（系统设置 → 辅助功能 → 朗读内容 → 系统语音 → 管理语音）。",
  "Download a voice marked “(Premium)” — e.g. Ava, Zoe, or Serena.":
    "下载标记为「(Premium)」的语音——例如 Ava、Zoe 或 Serena。",
  "Come back and tap Refresh.": "返回并点按「刷新」。",
  "Automatic (best available)": "自动（最佳可用）",
  Premium: "高级",
  Siri: "Siri",
  "Other voices": "其他语音",
  "Premium and Siri voices are listed first — they sound the most natural.":
    "高级和 Siri 语音排在最前——它们听起来最自然。",
  "Want a more natural voice? Premium voices are free from macOS — download one, then tap Refresh.":
    "想要更自然的语音？高级语音在 macOS 上免费——下载一个，然后点按「刷新」。",
  "Only warm, cute female voices are shown. Add more in System Settings → Accessibility → Spoken Content.":
    "仅显示温暖可爱的女声。可在 系统设置 → 辅助功能 → 朗读内容 中添加更多。",
  "No {language} voices are installed yet. Add one in System Settings → Accessibility → Spoken Content.":
    "尚未安装{language}语音。可在 系统设置 → 辅助功能 → 朗读内容 中添加。",
  "Speaking speed": "语速",
  "Test voice": "试听语音",
  "Hi, I'm Sammy. This is how I'll sound.": "你好，我是 Sammy。我听起来就是这样。",
  "Spoken replies aren't supported in this browser.": "此浏览器不支持语音朗读。",
  // ElevenLabs
  "ElevenLabs voices": "ElevenLabs 语音",
  "Premium cloud voices via your ElevenLabs account.": "通过你的 ElevenLabs 账户使用高级云端语音。",
  "Use ElevenLabs voices": "使用 ElevenLabs 语音",
  "API key": "API 密钥",
  "Paste your ElevenLabs API key": "粘贴你的 ElevenLabs API 密钥",
  "Connected — leave blank to keep current key": "已连接——留空以保留当前密钥",
  "Not connected": "未连接",
  "The key stays on your Mac, used only by the local Sammy backend — it never reaches the browser.":
    "密钥保存在你的 Mac 上，仅由本地 Sammy 后端使用——绝不会进入浏览器。",
  "Loading voices…": "正在加载语音…",
  "No voices found in your ElevenLabs account.": "在你的 ElevenLabs 账户中未找到语音。",
  "Paste a voice ID": "粘贴语音 ID",
  "Couldn't list your voices — paste a voice ID from your ElevenLabs dashboard instead.":
    "无法列出你的语音——请从 ElevenLabs 控制台粘贴一个语音 ID。",
  "Default voice": "默认语音",
  "Voice playback failed. {detail}": "语音播放失败。{detail}",
  // Voice authentication (Picovoice Eagle)
  "Only respond to my voice": "仅响应我的声音",
  "On-device speaker check (Picovoice Eagle) — ignores other people's voices.":
    "在本地进行说话人校验（Picovoice Eagle）——忽略其他人的声音。",
  "Picovoice AccessKey": "Picovoice AccessKey",
  "Voice enrolled": "已录入声音",
  "Not enrolled": "未录入",
  "Paste your free Picovoice AccessKey": "粘贴你的免费 Picovoice AccessKey",
  "Runs fully on-device. Get a free key at console.picovoice.ai.":
    "完全在本地运行。可在 console.picovoice.ai 获取免费密钥。",
  "Enrolling… {n}%": "录入中… {n}%",
  "Re-enroll my voice": "重新录入我的声音",
  "Enroll my voice": "录入我的声音",
  "Speak naturally for ~20 seconds.": "请自然地说话约 20 秒。",
  // Settings · Login password
  "Login password": "登录密码",
  "Ask for a password before opening Sammy.": "打开 Sammy 前先要求输入密码。",
  On: "开",
  Off: "关",
  "Leave blank to keep current password": "留空以保留当前密码",
  "Set a password": "设置密码",
  "Remove password": "移除密码",
  // Settings · Agents
  Agent: "智能体",
  Name: "名称",
  Icon: "图标",
  "Upload image": "上传图片",
  "Uploading...": "正在上传…",
  "Use auto icon": "使用自动图标",
  Model: "模型",
  "Use default": "使用默认",
  "System prompt": "系统提示词",
  "Allowed tools": "允许的工具",
  "{n} enabled": "已启用 {n} 个",
  All: "全部",
  "No tools are on": "没有已启用的工具",
  "No tools are off": "没有已关闭的工具",
  "Choose an image file": "请选择图片文件",
  "Icon uploaded": "图标已上传",
  // Settings · Tools
  "Save credentials": "保存凭据",
  "Reconnect OAuth": "重新连接 OAuth",
  "Connect OAuth": "连接 OAuth",
  Select: "选择",
  "Configure {name}": "配置 {name}",
  adapter: "适配器",
  "Active: {n}": "已启用：{n}",
  "Not active": "未启用",
  Callable: "可调用",
  "Context only": "仅上下文",
  "No auth": "无需授权",
  Connected: "已连接",
  "Needs reconnect": "需要重新连接",
  "Needs auth": "需要授权",
  // Memory
  "Local memory": "本地记忆",
  "Stored in SQLite on this Mac. Soul entries require manual edits.":
    "存储在此 Mac 的 SQLite 中。灵魂条目需手动编辑。",
  "{n} active": "{n} 条有效",
  "Post-turn review": "回合后审阅",
  auto: "自动",
  ask: "询问",
  "Recall past chats": "回忆过往对话",
  User: "用户",
  Soul: "灵魂",
  "Add a shared identity or behavior rule": "添加共享身份或行为规则",
  "Add a durable fact or preference": "添加持久的事实或偏好",
  "Add memory": "添加记忆",
  active: "有效",
  pending: "待定",
  archived: "已归档",
  "Filter memory": "筛选记忆",
  "Loading memory...": "正在加载记忆…",
  "No {filter} memories": "没有{filter}的记忆",
  "{n}% confidence": "{n}% 置信度",
  Local: "本地",
  "recalled {n}x": "已回忆 {n} 次",
  "Edit memory": "编辑记忆",
  "Save edit": "保存编辑",
  "Approve memory": "批准记忆",
  "Archive memory": "归档记忆",
  "Delete memory": "删除记忆",
  "Memory scope": "记忆范围",
  "Memory added locally": "已在本地添加记忆",
  "Memory approved": "记忆已批准",
  "Memory archived": "记忆已归档",
  "Memory corrected": "记忆已更正",
  "Memory deleted": "记忆已删除",
  // Agent picker
  "Send with agent": "用智能体发送",
  "New chat agent": "新对话的智能体",
  "{n} available": "{n} 个可用",
  Close: "关闭",
  "Close agent picker": "关闭智能体选择器",
  "1 tool": "1 个工具",
  "{n} tools": "{n} 个工具",
  "1 tool active": "1 个工具已启用",
  "{n} tools active": "{n} 个工具已启用",
  // Misc
  off: "关闭",
  'Saving "{file}" memory': "正在保存“{file}”记忆",
  memory: "记忆",
  "Copy prompt": "复制提示",
  "Copy message": "复制消息",
  "Regenerate from this message": "从此消息重新生成",
  "Copy code": "复制代码",
  'Backend unavailable. Run "sammy restart" in Terminal. {detail}':
    "后端不可用。请在终端运行 “sammy restart”。{detail}",
};

const TABLES: Record<Lang, Dict> = { en: {}, es, zh };

export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  let out = TABLES[lang]?.[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      out = out.split(`{${name}}`).join(String(value));
    }
  }
  return out;
}

export function readStoredLang(): Lang {
  if (typeof window === "undefined") return "en";
  const stored = window.localStorage.getItem(LANG_STORAGE_KEY);
  return stored === "es" || stored === "zh" || stored === "en" ? stored : "en";
}

type LangContextValue = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
};

const LangContext = createContext<LangContextValue>({
  lang: "en",
  setLang: () => undefined,
  t: (key) => key,
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang);

  useEffect(() => {
    document.documentElement.lang = LANGUAGES.find((entry) => entry.code === lang)?.htmlLang ?? "en";
  }, [lang]);

  const setLang = (next: Lang) => {
    setLangState(next);
    if (typeof window !== "undefined") window.localStorage.setItem(LANG_STORAGE_KEY, next);
  };

  const t = (key: string, vars?: Record<string, string | number>) => translate(lang, key, vars);

  return <LangContext.Provider value={{ lang, setLang, t }}>{children}</LangContext.Provider>;
}

export const useT = () => useContext(LangContext);
