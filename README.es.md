<p align="center">
  <img src="assets/banner.png" alt="Pulse Agent" width="100%">
</p>

# Pulse Agent ☤
<p align="center">
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/">Pulse Agent</a> | <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/">Pulse Desktop</a>
</p>
<p align="center">
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/"><img src="https://img.shields.io/badge/Docs-pulse--agent.anxiousresearchlab.com-FFD700?style=for-the-badge" alt="Documentación"></a>
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/blob/main/LICENSE"><img src="https://img.shields.io/badge/Licencia-MIT-green?style=for-the-badge" alt="Licencia: MIT"></a>
  <a href="https://github.com/Anxious-Research"><img src="https://img.shields.io/badge/Creado%20por-Anxious%20Research-blueviolet?style=for-the-badge" alt="Creado por Anxious Research Lab"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="English"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

**El agente de IA con mejora continua creado por [Anxious Research Lab](https://github.com/Anxious-Research).** Es el único agente con un bucle de aprendizaje integrado: crea habilidades a partir de la experiencia, las mejora durante el uso, se impulsa a sí mismo a persistir el conocimiento, busca en sus propias conversaciones pasadas y construye un modelo cada vez más profundo de quién eres a lo largo de las sesiones. Ejecútalo en un VPS de $5, un clúster de GPUs o infraestructura sin servidor que cuesta casi nada cuando está inactivo. No está atado a tu laptop — habla con él desde Telegram mientras trabaja en una VM en la nube.

Usa cualquier modelo que quieras — Anxious Portal, [OpenRouter](https://openrouter.ai) (más de 200 modelos), [NovitaAI](https://novita.ai), [NVIDIA NIM](https://build.nvidia.com) (Nemotron), [Xiaomi MiMo](https://platform.xiaomimimo.com), [z.ai/GLM](https://z.ai), [Kimi/Moonshot](https://platform.moonshot.ai), [MiniMax](https://www.minimax.io), [Hugging Face](https://huggingface.co), OpenAI, o tu propio endpoint. Cambia con `pulse model` — sin cambios de código, sin dependencias.

<table>
<tr><td><b>Una interfaz de terminal real</b></td><td>TUI completa con edición multilínea, autocompletado de comandos, historial de conversaciones, interrupción y redirección, y salida de herramientas en streaming.</td></tr>
<tr><td><b>Vive donde tú vives</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal y CLI — todo desde un único proceso gateway. Transcripción de notas de voz, continuidad de conversación entre plataformas.</td></tr>
<tr><td><b>Un bucle de aprendizaje cerrado</b></td><td>Memoria curada por el agente con recordatorios periódicos. Creación autónoma de habilidades tras tareas complejas. Las habilidades mejoran solas durante el uso. Búsqueda FTS5 de sesiones con resumención por LLM para recuperación entre sesiones. Modelado de usuario dialéctico <a href="https://github.com/plastic-labs/honcho">Honcho</a>. Compatible con el estándar abierto de <a href="https://agentskills.io">agentskills.io</a>.</td></tr>
<tr><td><b>Automatizaciones programadas</b></td><td>Planificador cron integrado con entrega a cualquier plataforma. Informes diarios, copias de seguridad nocturnas, auditorías semanales — todo en lenguaje natural, ejecutándose de forma autónoma.</td></tr>
<tr><td><b>Delega y paraleliza</b></td><td>Lanza subagentes aislados para flujos de trabajo paralelos. Escribe scripts de Python que llaman a herramientas vía RPC, convirtiendo pipelines de múltiples pasos en turnos de coste cero de contexto.</td></tr>
<tr><td><b>Funciona en cualquier lugar, no solo en tu laptop</b></td><td>Seis backends de terminal — local, Docker, SSH, Singularity, Modal y Daytona. Daytona y Modal ofrecen persistencia sin servidor — el entorno de tu agente hiberna cuando está inactivo y se activa bajo demanda, costando casi nada entre sesiones. Ejecútalo en un VPS de $5 o un clúster de GPUs.</td></tr>
<tr><td><b>Listo para investigación</b></td><td>Generación de trayectorias en lote, compresión de trayectorias para entrenar la próxima generación de modelos de llamadas a herramientas.</td></tr>
</table>

---

## Instalación rápida

### Linux, macOS, WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.sh | bash
```

### Windows (nativo, PowerShell)

> **Nota:** En Windows nativo, Pulse funciona sin WSL — la CLI, el gateway, la TUI y las herramientas funcionan de forma nativa. Si prefieres usar WSL2, el comando de Linux/macOS de arriba también funciona allí. ¿Encontraste un error? Por favor [crea un issue](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues).

Ejecuta esto en PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.ps1)
```

El instalador de código fuente usa PM para Python 3.14, Node.js, npm,
ripgrep, FFmpeg y las dependencias de Python. Si falta Git, descarga el archivo
verificado de Git for Windows en el almacén de Pulse, sin reemplazar el Git
del sistema. MSIX/App Installer es una distribución separada.

> **Android / Termux:** Hay un paquete APT en pruebas para dispositivos aarch64. Incluye Python, Node.js y la TUI. Sigue la guía de Termux, no el script de instalación para escritorio y servidor.
>
> **Windows:** Windows nativo es totalmente compatible — el comando de PowerShell de arriba instala todo. Si prefieres usar WSL2, el comando de Linux también funciona allí. La instalación nativa de Windows se encuentra en `%LOCALAPPDATA%\pulse`; WSL2 instala en `~/.pulse` como en Linux.

Después de la instalación:

```bash
source ~/.bashrc    # recargar shell (o: source ~/.zshrc)
pulse              # ¡empieza a chatear!
```

---

## Primeros pasos

```bash
pulse              # CLI interactiva — inicia una conversación
pulse model        # Elige tu proveedor y modelo LLM
pulse tools        # Configura qué herramientas están habilitadas
pulse config set   # Establece valores de configuración individuales
pulse gateway      # Inicia el gateway de mensajería (Telegram, Discord, etc.)
pulse setup        # Ejecuta el asistente de configuración completo
pulse claw migrate # Migra desde OpenClaw (si vienes de OpenClaw)
pulse update       # Actualiza a la última versión
pulse doctor       # Diagnostica cualquier problema
```

📖 **Documentación completa →**

---

## Evita la colección de claves API — Anxious Portal

Pulse funciona con cualquier proveedor que quieras — eso no cambiará. Pero si prefieres no recopilar cinco claves API separadas para el modelo, búsqueda web, generación de imágenes, TTS y un navegador en la nube, **Anxious Portal** las cubre todas bajo una sola suscripción:

- **Más de 300 modelos** — elige cualquiera con `/model <nombre>`
- **Tool Gateway** — búsqueda web, generación de imágenes (FAL), texto a voz (OpenAI), navegador en la nube (Browser Use), todo enrutado a través de tu suscripción. Sin cuentas adicionales.

Un comando desde una instalación nueva:

```bash
pulse setup --portal
```

Esto te autentica vía OAuth, establece Anxious como tu proveedor y activa el Tool Gateway. Comprueba qué está conectado en cualquier momento con `pulse portal info`. Detalles completos en la página de documentación del Tool Gateway.

Puedes seguir usando tus propias claves por herramienta cuando quieras — el gateway es por backend, no todo o nada.

---

## Referencia rápida: CLI vs Mensajería

Pulse tiene dos puntos de entrada: inicia la interfaz de terminal con `pulse`, o ejecuta el gateway y habla con él desde Telegram, Discord, Slack, WhatsApp, Signal o Email. Una vez en una conversación, muchos comandos de barra son compartidos entre ambas interfaces.

| Acción                              | CLI                                           | Plataformas de mensajería                                                         |
| ----------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------- |
| Empezar a chatear                   | `pulse`                                      | Ejecuta `pulse gateway setup` + `pulse gateway start`, luego envía un mensaje al bot |
| Nueva conversación                  | `/new` o `/reset`                             | `/new` o `/reset`                                                                 |
| Cambiar modelo                      | `/model [proveedor:modelo]`                   | `/model [proveedor:modelo]`                                                       |
| Establecer personalidad             | `/personality [nombre]`                       | `/personality [nombre]`                                                           |
| Reintentar o deshacer último turno  | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                 |
| Comprimir contexto / ver uso        | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                         |
| Explorar habilidades                | `/skills` o `/<nombre-habilidad>`             | `/<nombre-habilidad>`                                                             |
| Interrumpir trabajo actual          | `Ctrl+C` o enviar un nuevo mensaje            | `/stop` o enviar un nuevo mensaje                                                 |
| Estado específico de plataforma     | `/platforms`                                  | `/status`, `/sethome`                                                             |

Para las listas de comandos completas, consulta la guía de CLI y la guía del Gateway de Mensajería.

---

## Documentación

Toda la documentación está en **GitHub repo docs**:

| Sección                                                                                             | Contenido                                                    |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Inicio rápido              | Instalar → configurar → primera conversación en 2 minutos   |
| Uso de CLI                             | Comandos, atajos de teclado, personalidades, sesiones        |
| Configuración               | Archivo de configuración, proveedores, modelos, todas las opciones |
| Gateway de Mensajería           | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant   |
| Seguridad                        | Aprobación de comandos, emparejamiento por DM, aislamiento en contenedor |
| Herramientas y Toolsets   | Más de 40 herramientas, sistema de toolsets, backends de terminal |
| Sistema de Habilidades   | Memoria procedimental, Skills Hub, creación de habilidades   |
| Memoria                   | Memoria persistente, perfiles de usuario, mejores prácticas  |
| Integración MCP              | Conecta cualquier servidor MCP para capacidades extendidas   |
| Programación Cron           | Tareas programadas con entrega a plataforma                  |
| Archivos de Contexto | Contexto de proyecto que da forma a cada conversación      |
| Arquitectura            | Estructura del proyecto, bucle del agente, clases principales |
| Contribuir              | Configuración de desarrollo, proceso de PR, estilo de código |
| Referencia de CLI             | Todos los comandos y flags                                   |
| Variables de Entorno | Referencia completa de variables de entorno                  |

---

## Migración desde OpenClaw

Si vienes de OpenClaw, Pulse puede importar automáticamente tu configuración, memorias, habilidades y claves API.

**Durante la configuración inicial:** El asistente de configuración (`pulse setup`) detecta automáticamente `~/.openclaw` y ofrece migrar antes de que comience la configuración.

**En cualquier momento después de instalar:**

```bash
pulse claw migrate              # Migración interactiva (preset completo)
pulse claw migrate --dry-run    # Vista previa de qué se migraría
pulse claw migrate --preset user-data   # Migrar sin secretos
pulse claw migrate --overwrite  # Sobreescribir conflictos existentes
```

Qué se importa:

- **SOUL.md** — archivo de personalidad
- **Memorias** — entradas de MEMORY.md y USER.md
- **Habilidades** — habilidades creadas por el usuario → `~/.pulse/skills/openclaw-imports/`
- **Lista de comandos permitidos** — patrones de aprobación
- **Configuración de mensajería** — configuración de plataformas, usuarios permitidos, directorio de trabajo
- **Claves API** — secretos en lista de permitidos (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **Assets de TTS** — archivos de audio del espacio de trabajo
- **Instrucciones del espacio de trabajo** — AGENTS.md (con `--workspace-target`)

Consulta `pulse claw migrate --help` para todas las opciones, o usa la habilidad `openclaw-migration` para una migración guiada interactiva por el agente con vistas previas de dry-run.

---

## Contribuir

¡Las contribuciones son bienvenidas! Consulta la [Guía de Contribución](CONTRIBUTING.es.md) para la configuración del desarrollo, el estilo de código y el proceso de PR.

La configuración de PM, el entorno de pruebas con Python 3.14 y los comandos
de verificación están en [Development Setup](CONTRIBUTING.md#development-setup).

---

## Comunidad

- 💬 [Discord](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Servidor MCP de control de escritorio Linux para Pulse y otros hosts MCP, con árboles de accesibilidad AT-SPI, entrada Wayland/X11, capturas de pantalla y targeting de ventanas del compositor.
- 🔌 [PulseClaw](https://github.com/AaronWong1999/pulseclaw) — Puente WeChat comunitario: Ejecuta Pulse Agent y OpenClaw en la misma cuenta de WeChat.

---

## Licencia

MIT — ver [LICENSE](LICENSE).

Creado por [Anxious Research Lab](https://github.com/Anxious-Research).
