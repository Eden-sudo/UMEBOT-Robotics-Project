package com.example.umebotros2

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.umebotros2.backend.BackendConnector
import com.example.umebotros2.backend.ChatMessage
import com.example.umebotros2.backend.ConnectionStatus
import com.example.umebotros2.gamepad.GamepadInfo
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import org.json.JSONException
import org.json.JSONObject
import java.text.ParseException
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Actúa como el cerebro principal para la interfaz de usuario (UI), siguiendo el patrón de arquitectura MVVM.
 *
 * Este ViewModel es el intermediario entre la capa de Vista (Activity/Fragment) y la capa de datos/red.
 * Sus responsabilidades principales son:
 * - Gestionar y exponer el estado completo de la UI (`ChatUiState`) de forma reactiva a través de un `StateFlow`.
 * - Orquestar la comunicación con el backend del robot a través del `BackendConnector`.
 * - Procesar los eventos de la UI (ej. envío de mensajes, cambios de configuración).
 * - Recibir y enrutar los mensajes provenientes del backend para actualizar el estado de la UI.
 * - Integrar la lógica de otros módulos, como el `GamepadManager`.
 *
 * @param application La instancia de la aplicación, necesaria para el `AndroidViewModel` y para
 * inicializar componentes que requieren un contexto, como el `BackendConnector`.
 */
class ChatViewModel(application: Application) : AndroidViewModel(application) {
    private val TAG = "ChatViewModel"

    // Instancia del conector que maneja toda la comunicación de red.
    private val backendConnector = BackendConnector(application, viewModelScope)

    /**
     * El flujo de estado que contiene la única fuente de verdad para toda la UI.
     * Las Vistas (Activity/Fragment) observan este flujo para redibujarse cuando el estado cambia.
     */
    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    companion object {
        const val CONFIG_ITEM_AUDIO_SOURCE = "audio_source"
        const val CONFIG_ITEM_AI_PERSONALITY = "ai_personality"
        const val CONFIG_ITEM_AI_MODEL_BACKEND = "ai_model_backend"
        const val AUDIO_SOURCE_VALUE_ROBOT = "robot_microphone"
        const val AUDIO_SOURCE_VALUE_LOCAL = "local_microphone"
        const val SENDER_USER = "Usuario"
        const val SENDER_USER_VOICE = "Usuario (Voz)"
        const val SOURCE_GUI_STT_AUTO = "gui_stt_auto"
        const val SOURCE_GUI_TYPED = "gui_typed"
    }

    init {
        Log.d(TAG, "ChatViewModel inicializado.")
        observeBackendStatusAndMessages()
        backendConnector.start() // Inicia el proceso de conexión al backend.
    }

    /**
     * Parsea un timestamp en formato ISO 8601 (UTC) a un valor Long en milisegundos.
     * Es robusto y maneja formatos con y sin milisegundos.
     *
     * @param timestampStr La cadena de texto del timestamp.
     * @return El timestamp en milisegundos, o el tiempo actual si el parseo falla.
     */
    private fun parseTimestamp(timestampStr: String?): Long {
        if (timestampStr.isNullOrBlank()) return System.currentTimeMillis()
        val normalizedTimestampStr = timestampStr
            .replace("+00:00Z", "Z", ignoreCase = true)
            .replace("+00:00", "Z", ignoreCase = true)

        val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        val sdfNoMillis = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }

        return try {
            try {
                sdf.parse(normalizedTimestampStr)?.time
            } catch (e: ParseException) {
                sdfNoMillis.parse(normalizedTimestampStr)?.time
            } ?: System.currentTimeMillis()
        } catch (e: Exception) {
            Log.e(TAG, "Error inesperado parseando timestamp: '$timestampStr'", e)
            System.currentTimeMillis()
        }
    }

    /**
     * Inicia la observación de los flujos del `BackendConnector`.
     * Recolecta cambios en el estado de la conexión y los mensajes JSON entrantes
     * para actualizar el estado de la UI correspondientemente.
     */
    private fun observeBackendStatusAndMessages() {
        // Observa cambios en el estado de la conexión.
        viewModelScope.launch {
            backendConnector.statusFlow.collect { status ->
                Log.i(TAG, "Estado BackendConnector: $status")
                val serviceNameFromBackend = backendConnector.currentResolvedService?.serviceName

                _uiState.update {
                    val isConnected = status == ConnectionStatus.CONNECTED
                    it.copy(
                        connectionStatus = status,
                        inputEnabled = isConnected,
                        isDiscovering = status == ConnectionStatus.DISCOVERING,
                        isConnecting = status == ConnectionStatus.CONNECTING || status == ConnectionStatus.RECONNECTING || status == ConnectionStatus.RESOLVED || status == ConnectionStatus.SERVICE_FOUND,
                        canConnect = status == ConnectionStatus.RESOLVED && !isConnected,
                        serviceName = serviceNameFromBackend ?: if (status == ConnectionStatus.RESOLVED) "Servicio Resuelto" else null,
                        // Resetear configuraciones si se pierde la conexión.
                        currentAudioSourceIsRobot = if (!isConnected && status != ConnectionStatus.RECONNECTING) null else it.currentAudioSourceIsRobot,
                        isAudioConfigPending = if (!isConnected && status != ConnectionStatus.RECONNECTING) false else it.isAudioConfigPending,
                        currentAiPersonality = if (!isConnected && status != ConnectionStatus.RECONNECTING) null else it.currentAiPersonality,
                        currentAiModelBackend = if (!isConnected && status != ConnectionStatus.RECONNECTING) null else it.currentAiModelBackend,
                        availableAiPersonalities = if (!isConnected && status != ConnectionStatus.RECONNECTING) emptyList() else it.availableAiPersonalities,
                        availableAiBackends = if (!isConnected && status != ConnectionStatus.RECONNECTING) emptyList() else it.availableAiBackends,
                        isAiConfigPending = if (!isConnected && status != ConnectionStatus.RECONNECTING) false else it.isAiConfigPending
                    )
                }
                handleConnectionStatusChangeForUiMessages(status, serviceNameFromBackend)
            }
        }

        // Observa mensajes JSON entrantes del backend.
        viewModelScope.launch {
            backendConnector.receivedJsonFlow.collect { json ->
                processBackendJson(json)
            }
        }
    }

    /**
     * El enrutador central para todos los mensajes recibidos del backend.
     * Determina el tipo de mensaje y lo delega a la función de procesamiento correspondiente.
     *
     * @param json El `JSONObject` recibido del WebSocket.
     */
    private fun processBackendJson(json: JSONObject) {
        val type = json.optString("type", "unknown").lowercase(Locale.ROOT)
        val timestamp = parseTimestamp(json.optString("timestamp", null))
        val payload = json.optJSONObject("payload") ?: JSONObject()

        when (type) {
            "input" -> { // Mensaje de usuario retransmitido
                val text = payload.optString("text", "")
                if (text.isNotBlank()) addMessageToChatHistory(payload.optString("sender", SENDER_USER), text, timestamp, payload.optString("source", "backend_input"))
            }
            "output" -> { // Respuesta del robot/IA
                val text = payload.optString("text", "")
                if (text.isNotBlank()) addMessageToChatHistory(payload.optString("sender", "Umebot"), text, timestamp)
            }
            "system" -> { // Mensaje del sistema
                val text = payload.optString("text", "")
                if (text.isNotBlank()) showTransientSystemMessage(text)
            }
            "currentconfiguration" -> processCurrentConfiguration(payload)
            "config_confirmation" -> processConfigConfirmation(payload)
            "partial_stt_result" -> processPartialSttResult(payload)
            "internal_error", "error_parsing" -> {
                val errorMsg = json.optString("error_message", "Error desconocido del servidor.")
                showTransientSystemMessage("Error Backend: $errorMsg")
            }
            else -> Log.w(TAG, "Tipo de JSON desconocido: '$type'")
        }
    }

    /** Procesa la configuración actual enviada por el backend al conectar. */
    private fun processCurrentConfiguration(payload: JSONObject) {
        val settings = payload.optJSONObject("settings") ?: JSONObject()
        _uiState.update {
            it.copy(
                currentAudioSourceIsRobot = settings.optString(CONFIG_ITEM_AUDIO_SOURCE).equals(AUDIO_SOURCE_VALUE_ROBOT, ignoreCase = true),
                currentAiPersonality = settings.optString(CONFIG_ITEM_AI_PERSONALITY).ifBlank { null },
                currentAiModelBackend = settings.optString(CONFIG_ITEM_AI_MODEL_BACKEND).ifBlank { null },
                availableAiPersonalities = settings.optJSONArray("available_personalities")?.let { arr -> List(arr.length()) { i -> arr.getString(i) } } ?: emptyList(),
                availableAiBackends = settings.optJSONArray("available_ai_backends")?.let { arr -> List(arr.length()) { i -> arr.getString(i) } } ?: emptyList(),
                isAudioConfigPending = false,
                isAiConfigPending = false
            )
        }
        showTransientSystemMessage("Configuración actual recibida del backend.")
    }

    /** Procesa la confirmación de un cambio de configuración enviado desde la UI. */
    private fun processConfigConfirmation(payload: JSONObject) {
        val configItem = payload.optString("config_item")
        val success = payload.optBoolean("success", false)
        val currentValue = payload.optString("current_value")
        val message = payload.optString("message_to_display", if (success) "Configuración '$configItem' actualizada a '$currentValue'." else "Fallo al actualizar '$configItem'.")

        _uiState.update { current ->
            when (configItem) {
                CONFIG_ITEM_AUDIO_SOURCE -> current.copy(isAudioConfigPending = false, currentAudioSourceIsRobot = if (success) currentValue.equals(AUDIO_SOURCE_VALUE_ROBOT, ignoreCase = true) else current.currentAudioSourceIsRobot)
                CONFIG_ITEM_AI_PERSONALITY -> current.copy(isAiConfigPending = false, currentAiPersonality = if (success) currentValue.ifBlank { null } else current.currentAiPersonality)
                CONFIG_ITEM_AI_MODEL_BACKEND -> current.copy(isAiConfigPending = false, currentAiModelBackend = if (success) currentValue.ifBlank { null } else current.currentAiModelBackend)
                else -> current
            }
        }
        showTransientSystemMessage(message)
    }

    /** Procesa un resultado parcial del sistema de reconocimiento de voz (STT) del backend. */
    private fun processPartialSttResult(payload: JSONObject) {
        if (!_uiState.value.inputEnabled) return

        val partialText = payload.optString("text", "")
        val isFinal = payload.optBoolean("is_final", false)

        _uiState.update { it.copy(sttPreviewText = partialText, isSttPreviewActive = !isFinal) }

        if (isFinal && partialText.isNotBlank() && !_uiState.value.isSttPreviewActive) {
            sendUserChatInput(partialText, isFromStt = true)
        }
    }


    /** Muestra mensajes informativos en el historial o como notificaciones según el estado de la conexión. */
    private fun handleConnectionStatusChangeForUiMessages(status: ConnectionStatus, serviceName: String?) {
        val messageText: String? = when (status) {
            ConnectionStatus.CONNECTED -> "Conectado a ${serviceName ?: "Servicio"}."
            ConnectionStatus.DISCONNECTED -> "Desconectado. Buscando de nuevo..."
            ConnectionStatus.CONNECTING -> "Conectando a ${serviceName ?: "servicio"}..."
            ConnectionStatus.DISCOVERING -> "Buscando servidor en la red..."
            ConnectionStatus.RESOLVED -> "Servidor ${serviceName ?: ""} encontrado. Conectando..."
            ConnectionStatus.SERVICE_FOUND -> "Servicio hallado, obteniendo detalles..."
            ConnectionStatus.RECONNECTING -> "Reconectando a ${serviceName ?: "servicio anterior"}..."
            ConnectionStatus.IDLE -> null
        }
        messageText?.let {
            val isTransient = status in listOf(ConnectionStatus.CONNECTING, ConnectionStatus.DISCOVERING, ConnectionStatus.RESOLVED, ConnectionStatus.SERVICE_FOUND, ConnectionStatus.RECONNECTING)
            if (isTransient) {
                showTransientSystemMessage(it)
            } else {
                val lastMessage = _uiState.value.messages.lastOrNull()
                if (!(lastMessage?.sender == "Sistema" && lastMessage.text == it)) {
                    addMessageToChatHistory("Sistema", it, System.currentTimeMillis(), "system_status")
                }
            }
        }
    }

    /** Añade un nuevo mensaje al historial de chat en el UiState, evitando duplicados rápidos. */
    private fun addMessageToChatHistory(sender: String, text: String, timestamp: Long, source: String? = null) {
        val chatMessage = ChatMessage(sender, text, timestamp, source)
        _uiState.update { currentState ->
            val isUserMessage = sender == SENDER_USER || sender == SENDER_USER_VOICE
            val isGuiSource = source == SOURCE_GUI_TYPED || source == SOURCE_GUI_STT_AUTO
            if (isUserMessage && isGuiSource) {
                val recentUserMessage = currentState.messages.findLast { it.sender == sender && it.source == source }
                if (recentUserMessage != null && recentUserMessage.text == text && (timestamp - recentUserMessage.timestamp < 2000)) {
                    Log.w(TAG, "Mensaje de usuario duplicado detectado y evitado: '$text'")
                    return@update currentState
                }
            }
            val updatedMessages = (currentState.messages + chatMessage).sortedBy { it.timestamp }.takeLast(100)
            currentState.copy(messages = updatedMessages)
        }
    }

    /** Emite un evento para mostrar un mensaje transitorio en la UI (ej. Snackbar). */
    private fun showTransientSystemMessage(message: String) {
        _uiState.update { it.copy(transientUserMessage = Event(message)) }
    }

    /**
     * Llamado por la UI para enviar un mensaje de chat del usuario.
     * Añade el mensaje al historial local inmediatamente y lo envía al backend.
     * @param text El texto del mensaje.
     * @param isFromStt `true` si el mensaje proviene del sistema de reconocimiento de voz.
     */
    fun sendUserChatInput(text: String, isFromStt: Boolean = false) {
        if (text.isBlank()) return
        if (_uiState.value.connectionStatus != ConnectionStatus.CONNECTED) {
            showTransientSystemMessage("No conectado. No se puede enviar mensaje.")
            return
        }

        val sender = if (isFromStt) SENDER_USER_VOICE else SENDER_USER
        val source = if (isFromStt) SOURCE_GUI_STT_AUTO else SOURCE_GUI_TYPED

        addMessageToChatHistory(sender, text, System.currentTimeMillis(), source)
        _uiState.update { it.copy(sttPreviewText = null, isSttPreviewActive = false) }

        try {
            val jsonRequest = JSONObject().apply {
                put("type", "input")
                put("payload", JSONObject().apply {
                    put("text", text)
                    put("source", source)
                })
            }
            backendConnector.sendJsonString(jsonRequest.toString())
        } catch (e: JSONException) {
            Log.e(TAG, "Error creando JSON para 'input': ${e.message}", e)
            showTransientSystemMessage("Error interno al enviar mensaje.")
        }
    }

    /** Llamado por la UI cuando el usuario interactúa con el campo de texto de entrada. */
    fun userInputInteracted(currentTextInEditText: String) {
        if ((_uiState.value.isSttPreviewActive || _uiState.value.sttPreviewText != null) &&
            _uiState.value.sttPreviewText != currentTextInEditText) {
            _uiState.update {
                it.copy(sttPreviewText = currentTextInEditText, isSttPreviewActive = false)
            }
        }
    }

    /** Limpia el texto de preview del STT en el estado de la UI. */
    fun clearCurrentSttPreview() {
        _uiState.update { it.copy(sttPreviewText = null, isSttPreviewActive = false) }
    }

    //region API de Gamepad
    /**
     * Punto de entrada para enviar comandos de gamepad.
     * Esta función está diseñada para ser usada como callback por el `GamepadManager`.
     * @param commandJson El comando ya formateado como `JSONObject`.
     */
    fun sendGamepadCommand(commandJson: JSONObject) {
        if (_uiState.value.connectionStatus != ConnectionStatus.CONNECTED) return
        backendConnector.sendJsonString(commandJson.toString())
    }

    /**
     * Actualiza el `UiState` con la información del gamepad que está actualmente activo.
     * @param gamepadInfo El `GamepadInfo` del gamepad activo, o `null` si no hay ninguno.
     */
    fun updateActiveGamepadInUiState(gamepadInfo: GamepadInfo?) {
        if (_uiState.value.selectedGamepad != gamepadInfo) {
            _uiState.update { it.copy(selectedGamepad = gamepadInfo) }
            Log.i(TAG, "ViewModel actualizado con gamepad: ${gamepadInfo?.name ?: "Ninguno"}")
        }
    }
    //endregion

    //region API de Configuración
    /** Llamado por la UI para cambiar la fuente de audio (micrófono del robot o local). */
    fun onAudioSourceSwitchChanged(wantsRobotMic: Boolean) {
        val currentState = _uiState.value
        if (currentState.connectionStatus != ConnectionStatus.CONNECTED) { showTransientSystemMessage("No conectado."); return }
        if (currentState.isAudioConfigPending) { showTransientSystemMessage("Esperando confirmación de config. audio..."); return }
        if (wantsRobotMic == currentState.currentAudioSourceIsRobot) return

        _uiState.update { it.copy(isAudioConfigPending = true) }
        sendConfiguration(CONFIG_ITEM_AUDIO_SOURCE, if (wantsRobotMic) AUDIO_SOURCE_VALUE_ROBOT else AUDIO_SOURCE_VALUE_LOCAL)
    }

    /** Llamado por la UI para cambiar la personalidad de la IA. */
    fun onAiPersonalitySelected(personalityKey: String) {
        val currentState = _uiState.value
        if (currentState.connectionStatus != ConnectionStatus.CONNECTED) { showTransientSystemMessage("No conectado."); return }
        if (currentState.isAiConfigPending) { showTransientSystemMessage("Esperando confirmación de config. IA..."); return }
        if (personalityKey == currentState.currentAiPersonality) return

        _uiState.update { it.copy(isAiConfigPending = true) }
        sendConfiguration(CONFIG_ITEM_AI_PERSONALITY, personalityKey)
    }

    /** Llamado por la UI para cambiar el modelo de IA de backend (local vs. nube). */
    fun onAiModelSelected(modelKey: String) {
        val currentState = _uiState.value
        if (currentState.connectionStatus != ConnectionStatus.CONNECTED) { showTransientSystemMessage("No conectado."); return }
        if (currentState.isAiConfigPending) { showTransientSystemMessage("Esperando confirmación de config. IA..."); return }
        if (modelKey == currentState.currentAiModelBackend) return

        _uiState.update { it.copy(isAiConfigPending = true) }
        sendConfiguration(CONFIG_ITEM_AI_MODEL_BACKEND, modelKey)
    }

    /** Actualiza la visibilidad del historial de chat en el `UiState`. */
    fun onChatHistoryVisibilityChanged(isVisible: Boolean) {
        _uiState.update { it.copy(isChatHistoryVisible = isVisible) }
    }

    /**
     * Construye y envía un mensaje de configuración genérico al backend.
     * @param configItem La clave de la configuración a cambiar (ej. "audio_source").
     * @param value El nuevo valor para la configuración.
     */
    private fun sendConfiguration(configItem: String, value: Any) {
        try {
            val jsonConfig = JSONObject().apply {
                put("type", "config")
                put("payload", JSONObject().apply {
                    put("config_item", configItem)
                    put("value", value)
                })
            }
            backendConnector.sendJsonString(jsonConfig.toString())
        } catch (e: JSONException) {
            Log.e(TAG, "Error creando JSON para 'config' ($configItem): ${e.message}", e)
            _uiState.update { current ->
                when(configItem) {
                    CONFIG_ITEM_AUDIO_SOURCE -> current.copy(isAudioConfigPending = false)
                    CONFIG_ITEM_AI_PERSONALITY, CONFIG_ITEM_AI_MODEL_BACKEND -> current.copy(isAiConfigPending = false)
                    else -> current
                }
            }
            showTransientSystemMessage("Error al preparar configuración.")
        }
    }
    //endregion

    //region Control del Backend
    /** Solicita al `BackendConnector` que inicie el proceso de conexión. */
    fun requestBackendStart() {
        backendConnector.start()
    }

    /** Solicita al `BackendConnector` que detenga la conexión. */
    fun requestBackendStop() {
        backendConnector.stop()
    }
    //endregion

    /**
     * Se invoca cuando el ViewModel está a punto de ser destruido.
     * Es el lugar adecuado para limpiar recursos, como el `BackendConnector`.
     */
    override fun onCleared() {
        super.onCleared()
        Log.i(TAG, "ChatViewModel limpiado (onCleared).")
        backendConnector.cleanup()
    }
}
