package com.example.umebotros2

import com.example.umebotros2.backend.ChatMessage
import com.example.umebotros2.backend.ConnectionStatus
import com.example.umebotros2.gamepad.GamepadInfo

/**
 * Representa el estado completo e inmutable de la interfaz de usuario (UI) en un momento dado.
 *
 * Esta `data class` actúa como la "única fuente de verdad" para la vista. El `ChatViewModel`
 * mantiene y actualiza una instancia de esta clase, y la UI simplemente observa los cambios
 * para redibujarse a sí misma. Al ser inmutable, cualquier cambio resulta en una nueva instancia,
 * lo que garantiza un flujo de datos predecible y unidireccional.
 *
 * @property connectionStatus El estado actual del ciclo de vida de la conexión con el backend.
 * @property serviceName El nombre del servicio de red del backend que ha sido resuelto.
 * @property inputEnabled Indica si los controles de entrada del usuario (chat, botones) deben estar habilitados.
 * @property isDiscovering `true` si se está buscando activamente un servicio de backend en la red.
 * @property isConnecting `true` si se está en proceso de conectar, resolver o reconectar.
 * @property canConnect `true` si se ha resuelto un servicio y el usuario podría iniciar una conexión manual.
 * @property messages La lista actual de mensajes a mostrar en el historial del chat.
 * @property isChatHistoryVisible Controla la visibilidad del panel del historial de chat.
 * @property transientUserMessage Un evento para mostrar mensajes temporales al usuario (ej. en un Snackbar).
 * Está envuelto en la clase `Event` para asegurar un consumo único.
 * @property currentAudioSourceIsRobot El estado actual de la fuente de audio. `true` para el micrófono del robot,
 * `false` para el local, `null` si es desconocido.
 * @property isAudioConfigPending `true` si se ha enviado un cambio de configuración de audio y se espera confirmación.
 * @property currentAiPersonality La clave de la personalidad de la IA actualmente activa en el backend.
 * @property currentAiModelBackend La clave del modelo de IA (backend) actualmente activo (ej. local o nube).
 * @property availableAiPersonalities La lista de personalidades de IA disponibles, recibida desde el backend.
 * @property availableAiBackends La lista de modelos de IA (backends) disponibles, recibida desde el backend.
 * @property isAiConfigPending `true` si se ha enviado un cambio de configuración de IA y se espera confirmación.
 * @property sttPreviewText El texto parcial o final del sistema de reconocimiento de voz (STT) para mostrar
 * como vista previa en el campo de entrada.
 * @property isSttPreviewActive `true` si la vista previa del STT debe mostrarse como un estado "en progreso".
 * @property selectedGamepad La información del gamepad actualmente activo y seleccionado. `null` si no hay ninguno.
 */
data class ChatUiState(
    // Estado de la conexión con el backend
    val connectionStatus: ConnectionStatus = ConnectionStatus.IDLE,
    val serviceName: String? = null,
    val inputEnabled: Boolean = false,
    val isDiscovering: Boolean = false,
    val isConnecting: Boolean = false,
    val canConnect: Boolean = false,

    // Mensajes y UI del chat
    val messages: List<ChatMessage> = emptyList(),
    val isChatHistoryVisible: Boolean = true,
    val transientUserMessage: Event<String>? = null,

    // Configuración de audio
    val currentAudioSourceIsRobot: Boolean? = null,
    val isAudioConfigPending: Boolean = false,

    // Configuración de IA
    val currentAiPersonality: String? = null,
    val currentAiModelBackend: String? = null,
    val availableAiPersonalities: List<String> = emptyList(),
    val availableAiBackends: List<String> = emptyList(),
    val isAiConfigPending: Boolean = false,

    // Estado de Speech-to-Text (STT)
    val sttPreviewText: String? = null,
    val isSttPreviewActive: Boolean = false,

    // Estado del Gamepad
    val selectedGamepad: GamepadInfo? = null
)
