package com.example.umebotros2.backend

import android.annotation.SuppressLint
import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import okhttp3.*
import org.json.JSONException
import org.json.JSONObject
import okio.ByteString

/**
 * Orquesta la comunicación completa con el backend del robot Umebot.
 *
 * Su responsabilidad principal es establecer y mantener una conexión bidireccional
 * robusta. Para lograrlo, implementa un proceso en dos fases:
 * 1.  **Descubrimiento (NSD):** Busca activamente en la red local un servicio
 * anunciado por el backend de Python, utilizando Network Service Discovery (NSD).
 * Esto elimina la necesidad de configurar manualmente la IP del servidor.
 * 2.  **Conexión (WebSocket):** Una vez que el servicio es encontrado y su IP resuelta,
 * establece una conexión WebSocket para la comunicación en tiempo real.
 *
 * La clase está diseñada para ser resiliente, con mecanismos automáticos de
 * reconexión tanto para el WebSocket como para el proceso de descubrimiento NSD
 * en caso de fallos o pérdida de conexión.
 *
 * Expone flujos (StateFlow y SharedFlow) para que las capas superiores de la
 * aplicación (ViewModels) puedan observar el estado de la conexión y reaccionar
 * a los mensajes entrantes del backend de forma reactiva.
 *
 * @param context El contexto de la aplicación, usado para obtener el NsdManager.
 * @param scope Un CoroutineScope externo para gestionar el ciclo de vida de las
 * operaciones asíncronas. Se recomienda que este scope utilice un
 * `SupervisorJob` para que los fallos aislados no cancelen todo el conector.
 */
class BackendConnector(
    context: Context,
    private val scope: CoroutineScope
) {
    private val applicationContext = context.applicationContext
    private val nsdManager: NsdManager? = applicationContext.getSystemService(NsdManager::class.java)
    private val TAG = "BackendConnector"

    private val SERVICE_TYPE = "_umebotlogics._tcp."
    private val SERVICE_NAME_BASE = "UmebotLogicsWebSocket"
    private val WEBSOCKET_PATH = "/ws_bidirectional"

    companion object {
        private const val MAX_DIRECT_WEBSOCKET_RECONNECT_ATTEMPTS = 3
        private const val INITIAL_RECONNECT_DELAY_MS = 2000L
        private const val MAX_RECONNECT_DELAY_MS = 15000L
        private const val RECONNECT_DELAY_MULTIPLIER = 2.0
    }

    private var directWebSocketReconnectAttempts = 0
    private var reconnectWebSocketJob: Job? = null

    /**
     * Flujo que emite el estado actual de la conexión.
     * Es un [StateFlow] para que los observadores siempre reciban el último estado al suscribirse.
     * @see ConnectionStatus
     */
    private val _statusFlow = MutableStateFlow(ConnectionStatus.IDLE)
    val statusFlow: StateFlow<ConnectionStatus> = _statusFlow.asStateFlow()

    /**
     * Flujo que emite los mensajes JSON recibidos del backend.
     * Es un [SharedFlow] para distribuir cada mensaje a todos los observadores activos.
     * No tiene repetición (replay=0) para que los nuevos suscriptores no reciban mensajes antiguos.
     */
    private val _receivedJsonFlow = MutableSharedFlow<JSONObject>(replay = 0, extraBufferCapacity = 32)
    val receivedJsonFlow: SharedFlow<JSONObject> = _receivedJsonFlow.asSharedFlow()

    private var nsdDiscoveryListener: NsdManager.DiscoveryListener? = null
    private var nsdResolveListener: NsdManager.ResolveListener? = null

    private var currentResolvedService: NsdServiceInfo? = null
    private var isNsdDiscoveryActive = false

    private val okHttpClient: OkHttpClient = OkHttpClient.Builder().build()
    private var webSocket: WebSocket? = null
    private var activeWebSocketListener: AppWebSocketListener? = null

    init {
        Log.i(TAG, "BackendConnector inicializado.")
        if (nsdManager == null) Log.e(TAG, "NsdManager no disponible. Descubrimiento NSD no funcionará.")
    }

    /**
     * Inicia el proceso completo de conexión.
     * Si el conector está inactivo o desconectado, comienza por el descubrimiento de servicios (NSD).
     * Ignora la llamada si ya está en un proceso activo de conexión o reconexión.
     */
    fun start() {
        Log.i(TAG, "start() llamado. Estado actual: ${_statusFlow.value}")
        if (_statusFlow.value == ConnectionStatus.IDLE || _statusFlow.value == ConnectionStatus.DISCONNECTED) {
            cleanupPreviousSession(keepServiceInfo = false)
            startNsdDiscovery()
        } else {
            Log.w(TAG, "start() ignorado: conector ya en un estado activo (${_statusFlow.value}).")
        }
    }

    /**
     * Detiene todas las operaciones de red, limpia los recursos y resetea el estado a IDLE.
     * Cancela cualquier proceso de descubrimiento, cierra la conexión WebSocket y detiene los reintentos.
     */
    fun stop() {
        Log.i(TAG, "stop() llamado.")
        cleanupPreviousSession(keepServiceInfo = false)
        _statusFlow.value = ConnectionStatus.IDLE
    }

    /**
     * Limpia los recursos de una sesión anterior (NSD, WebSocket) para preparar una nueva.
     *
     * @param keepServiceInfo Si es `true`, mantiene la información del último servicio resuelto
     * para un posible intento de reconexión rápido. Si es `false`, la descarta.
     */
    private fun cleanupPreviousSession(keepServiceInfo: Boolean) {
        stopNsdDiscovery()
        closeWebSocketConnection("Limpieza de sesión solicitada.")
        reconnectWebSocketJob?.cancel("Nueva limpieza de sesión.")
        reconnectWebSocketJob = null
        if (!keepServiceInfo) {
            currentResolvedService = null
        }
        directWebSocketReconnectAttempts = 0
    }

    /**
     * Envía un mensaje en formato de String JSON al backend a través del WebSocket.
     * La operación falla silenciosamente si no hay una conexión activa.
     * Si el envío falla mientras se está conectado, inicia el proceso de manejo de fallos.
     *
     * @param jsonString El mensaje a enviar, formateado como una cadena JSON.
     */
    fun sendJsonString(jsonString: String) {
        val currentWebSocket = webSocket
        if (_statusFlow.value != ConnectionStatus.CONNECTED || currentWebSocket == null) {
            Log.w(TAG, "No conectado o WebSocket nulo. No se puede enviar: $jsonString (Estado: ${_statusFlow.value})")
            if (_statusFlow.value !in listOf(ConnectionStatus.RECONNECTING, ConnectionStatus.CONNECTING, ConnectionStatus.IDLE, ConnectionStatus.DISCONNECTED)) {
                scope.launch { handleWebSocketFailure("Intento de envío sin conexión activa.") }
            }
            return
        }
        if (currentWebSocket.send(jsonString)) {
            // Envío exitoso
        } else {
            Log.e(TAG, "Fallo al encolar JSON para envío (send devolvió false).")
            scope.launch { handleWebSocketFailure("WebSocket.send() devolvió false.") }
        }
    }

    /**
     * Inicia el descubrimiento de servicios de red (NSD) para encontrar el backend.
     */
    @SuppressLint("MissingPermission")
    private fun startNsdDiscovery() {
        if (nsdManager == null) {
            Log.e(TAG, "NSD no disponible. No se puede iniciar descubrimiento.")
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            return
        }
        if (isNsdDiscoveryActive) {
            Log.d(TAG, "Descubrimiento NSD ya está activo. No reiniciar.")
            return
        }
        if (_statusFlow.value == ConnectionStatus.CONNECTED || _statusFlow.value == ConnectionStatus.RESOLVED || _statusFlow.value == ConnectionStatus.CONNECTING) {
            Log.i(TAG, "Conectado o en proceso, no iniciar nuevo descubrimiento NSD a menos que falle.")
            return
        }

        Log.i(TAG, "Iniciando descubrimiento NSD para tipo: $SERVICE_TYPE")
        cleanupPreviousSession(keepServiceInfo = false)
        _statusFlow.value = ConnectionStatus.DISCOVERING
        isNsdDiscoveryActive = true

        nsdDiscoveryListener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                Log.d(TAG, "NSD: Descubrimiento iniciado ($regType).")
            }

            override fun onServiceFound(service: NsdServiceInfo) {
                Log.i(TAG, "NSD: Servicio encontrado - ${service.serviceName}, Tipo: ${service.serviceType}")
                if (service.serviceType.contains(SERVICE_TYPE.removeSuffix(".")) &&
                    (service.serviceName.startsWith(SERVICE_NAME_BASE) || service.serviceName == SERVICE_NAME_BASE) &&
                    nsdResolveListener == null &&
                    _statusFlow.value == ConnectionStatus.DISCOVERING) {
                    Log.i(TAG, "NSD: Resolviendo servicio compatible '${service.serviceName}'...")
                    _statusFlow.value = ConnectionStatus.SERVICE_FOUND
                    resolveNsdService(service)
                }
            }

            override fun onServiceLost(service: NsdServiceInfo) {
                Log.w(TAG, "NSD: Servicio perdido - ${service.serviceName}")
                if (currentResolvedService?.serviceName == service.serviceName) {
                    Log.w(TAG, "NSD: ¡Servicio activo/objetivo '${service.serviceName}' perdido!")
                    currentResolvedService = null
                    closeWebSocketConnection("Servicio NSD activo perdido.")
                    if (_statusFlow.value !in listOf(ConnectionStatus.IDLE, ConnectionStatus.CONNECTING, ConnectionStatus.RECONNECTING)) {
                        _statusFlow.value = ConnectionStatus.DISCONNECTED
                        startNsdDiscovery()
                    }
                }
            }

            override fun onDiscoveryStopped(serviceType: String) {
                Log.d(TAG, "NSD: Descubrimiento detenido ($serviceType).")
                isNsdDiscoveryActive = false
                val previousListener = nsdDiscoveryListener
                nsdDiscoveryListener = null
                if (previousListener === this && _statusFlow.value == ConnectionStatus.DISCOVERING) {
                    Log.w(TAG, "NSD: Descubrimiento detenido inesperadamente. Reintentando tras pausa.")
                    scope.launch { delay(INITIAL_RECONNECT_DELAY_MS); if (scope.isActive) startNsdDiscovery() }
                }
            }
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.e(TAG, "NSD: Error al iniciar descubrimiento ($serviceType): $errorCode")
                isNsdDiscoveryActive = false
                nsdDiscoveryListener = null
                _statusFlow.value = ConnectionStatus.DISCONNECTED
                scope.launch { delay(INITIAL_RECONNECT_DELAY_MS); if (scope.isActive) startNsdDiscovery() }
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.e(TAG, "NSD: Error al detener descubrimiento ($serviceType): $errorCode")
                isNsdDiscoveryActive = false
                nsdDiscoveryListener = null
            }
        }
        try {
            nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, nsdDiscoveryListener)
        } catch (e: Exception) {
            Log.e(TAG, "NSD: Excepción en nsdManager.discoverServices: ${e.message}", e)
            isNsdDiscoveryActive = false
            nsdDiscoveryListener = null
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            scope.launch { delay(INITIAL_RECONNECT_DELAY_MS); if (scope.isActive) startNsdDiscovery() }
        }
    }

    /**
     * Detiene el proceso de descubrimiento de servicios NSD activo.
     */
    @SuppressLint("MissingPermission")
    private fun stopNsdDiscovery() {
        if (nsdManager == null || !isNsdDiscoveryActive) return

        Log.d(TAG, "Deteniendo descubrimiento NSD...")
        isNsdDiscoveryActive = false
        if (nsdDiscoveryListener != null) {
            try {
                nsdManager.stopServiceDiscovery(nsdDiscoveryListener)
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "NSD: Listener de descubrimiento ya desregistrado o inválido: ${e.message}")
            } catch (e: Exception) {
                Log.e(TAG, "NSD: Excepción en nsdManager.stopServiceDiscovery: ${e.message}", e)
            }
        }
        nsdDiscoveryListener = null

        if (nsdResolveListener != null) {
            Log.d(TAG, "Limpiando nsdResolveListener durante parada de descubrimiento.")
            nsdResolveListener = null
        }
    }

    /**
     * Resuelve un servicio NSD encontrado para obtener su dirección IP y puerto.
     *
     * @param serviceToResolve El [NsdServiceInfo] del servicio a resolver.
     */
    private fun resolveNsdService(serviceToResolve: NsdServiceInfo) {
        if (nsdManager == null) { Log.e(TAG, "NSD no disponible, no se puede resolver."); return }
        if (nsdResolveListener != null) {
            Log.w(TAG, "NSD: Intento de resolver mientras otra resolución está en curso. Ignorando.")
            return
        }
        Log.d(TAG, "NSD: Preparando para resolver: ${serviceToResolve.serviceName}")

        val newListener = object : NsdManager.ResolveListener {
            override fun onServiceResolved(resolvedInfo: NsdServiceInfo) {
                if (nsdResolveListener !== this) {
                    Log.w(TAG, "NSD: onServiceResolved de un listener obsoleto para ${resolvedInfo.serviceName}")
                    return
                }
                Log.i(TAG, "NSD: Servicio resuelto: ${resolvedInfo.serviceName} -> IP: ${resolvedInfo.host?.hostAddress}, Puerto: ${resolvedInfo.port}")
                nsdResolveListener = null

                if (resolvedInfo.host != null && resolvedInfo.port > 0) {
                    currentResolvedService = resolvedInfo
                    _statusFlow.value = ConnectionStatus.RESOLVED
                    stopNsdDiscovery()
                    directWebSocketReconnectAttempts = 0
                    connectWebSocket()
                } else {
                    Log.e(TAG, "NSD: Resuelto sin host/puerto válido. Volviendo a descubrir.")
                    _statusFlow.value = ConnectionStatus.DISCOVERING
                    startNsdDiscovery()
                }
            }

            override fun onResolveFailed(failedServiceInfo: NsdServiceInfo, errorCode: Int) {
                if (nsdResolveListener !== this) {
                    Log.w(TAG, "NSD: onResolveFailed de un listener obsoleto para ${failedServiceInfo.serviceName}")
                    return
                }
                Log.e(TAG, "NSD: Error al resolver '${failedServiceInfo.serviceName}': $errorCode")
                nsdResolveListener = null
                _statusFlow.value = ConnectionStatus.DISCOVERING
                if (!isNsdDiscoveryActive) {
                    startNsdDiscovery()
                }
            }
        }
        nsdResolveListener = newListener
        try {
            nsdManager.resolveService(serviceToResolve, newListener)
        } catch (e: Exception) {
            Log.e(TAG, "NSD: Excepción en nsdManager.resolveService: ${e.message}", e)
            if (nsdResolveListener === newListener) nsdResolveListener = null
            _statusFlow.value = ConnectionStatus.DISCOVERING
        }
    }

    /**
     * Inicia la conexión WebSocket utilizando la información del servicio resuelto.
     *
     * @param isRetry Indica si esta conexión es un reintento o el primer intento.
     */
    private fun connectWebSocket(isRetry: Boolean = false) {
        val serviceToConnect = currentResolvedService
        val hostAddress = serviceToConnect?.host?.hostAddress
        val port = serviceToConnect?.port

        if (hostAddress == null || port == null || port <= 0) {
            Log.e(TAG, "WS: Datos de servicio inválidos para conectar (Host: $hostAddress, Puerto: $port). Iniciando redescubrimiento NSD.")
            currentResolvedService = null
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            startNsdDiscovery()
            return
        }

        val currentWsUrl = webSocket?.request()?.url
        if (!isRetry && (_statusFlow.value == ConnectionStatus.CONNECTING || _statusFlow.value == ConnectionStatus.CONNECTED)) {
            if (currentWsUrl?.host == hostAddress && currentWsUrl.port == port) {
                Log.i(TAG, "WS: Ya conectado o conectando a $hostAddress:$port. No se toma acción.")
                return
            } else {
                Log.i(TAG, "WS: Petición de conexión a nuevo servicio ($hostAddress:$port) mientras otro podría estar activo/conectando. Cerrando anterior.")
                closeWebSocketConnection("Cambiando a nuevo servicio.")
            }
        }

        val newListener = AppWebSocketListener()
        activeWebSocketListener = newListener

        val url = "ws://$hostAddress:$port$WEBSOCKET_PATH"
        val attemptCountInfo = if (isRetry) "(Reintento ${directWebSocketReconnectAttempts + 1}/$MAX_DIRECT_WEBSOCKET_RECONNECT_ATTEMPTS)" else "(Intento inicial)"
        Log.i(TAG, "WS: Conectando a ${serviceToConnect.serviceName} en $url $attemptCountInfo")
        _statusFlow.value = if (isRetry) ConnectionStatus.RECONNECTING else ConnectionStatus.CONNECTING

        val request = Request.Builder().url(url).build()
        webSocket = okHttpClient.newWebSocket(request, newListener)
    }

    /**
     * Cierra la conexión WebSocket activa.
     *
     * @param reason Una descripción del motivo del cierre.
     */
    private fun closeWebSocketConnection(reason: String) {
        val wsToClose = webSocket
        if (wsToClose != null) {
            Log.i(TAG, "WS: Cerrando conexión WebSocket. Razón: $reason")
            activeWebSocketListener = null
            webSocket = null
            try {
                wsToClose.close(1000, "Cierre normal: $reason")
            } catch (e: Exception) {
                Log.w(TAG, "WS: Excepción al cerrar WebSocket: ${e.message}")
            }
        }
    }

    /**
     * Maneja un fallo en la conexión WebSocket, decidiendo si debe reintentar la conexión
     * al servicio actual o iniciar un redescubrimiento completo.
     *
     * @param reason Descripción del fallo.
     */
    private suspend fun handleWebSocketFailure(reason: String) {
        Log.w(TAG, "WS: Manejando fallo/desconexión. Razón: $reason. Estado actual: ${_statusFlow.value}")

        if (_statusFlow.value == ConnectionStatus.CONNECTED ||
            _statusFlow.value == ConnectionStatus.CONNECTING ||
            _statusFlow.value == ConnectionStatus.RECONNECTING) {
            attemptReconnectionToCurrentService()
        } else if (_statusFlow.value != ConnectionStatus.IDLE) {
            Log.i(TAG, "WS: Fallo en estado no conectado (${_statusFlow.value}). Volviendo a descubrir.")
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            currentResolvedService = null
            startNsdDiscovery()
        } else {
            Log.d(TAG, "WS: Fallo ignorado, estado actual es IDLE o ya manejado.")
        }
    }

    /**
     * Intenta reconectar al último servicio resuelto exitosamente, aplicando una
     * estrategia de backoff exponencial. Si se superan los reintentos, abandona
     * el servicio y vuelve a la fase de descubrimiento NSD.
     */
    private fun attemptReconnectionToCurrentService() {
        reconnectWebSocketJob?.cancel("Nuevo intento de reconexión al servicio actual.")
        reconnectWebSocketJob = null

        val serviceToRetry = currentResolvedService
        if (serviceToRetry == null || serviceToRetry.host?.hostAddress == null || serviceToRetry.port <= 0) {
            Log.w(TAG, "WS: No se puede reintentar, información del servicio resuelto es inválida/nula. Reiniciando descubrimiento NSD.")
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            startNsdDiscovery()
            return
        }

        if (directWebSocketReconnectAttempts < MAX_DIRECT_WEBSOCKET_RECONNECT_ATTEMPTS) {
            directWebSocketReconnectAttempts++
            var delayMs = INITIAL_RECONNECT_DELAY_MS
            repeat(directWebSocketReconnectAttempts - 1) { delayMs = (delayMs * RECONNECT_DELAY_MULTIPLIER).toLong() }
            delayMs = delayMs.coerceAtMost(MAX_RECONNECT_DELAY_MS)

            Log.i(TAG, "WS: Reintento ${directWebSocketReconnectAttempts}/$MAX_DIRECT_WEBSOCKET_RECONNECT_ATTEMPTS a ${serviceToRetry.serviceName} en ${delayMs}ms.")
            _statusFlow.value = ConnectionStatus.RECONNECTING

            reconnectWebSocketJob = scope.launch {
                delay(delayMs)
                if (isActive && _statusFlow.value == ConnectionStatus.RECONNECTING && currentResolvedService === serviceToRetry) {
                    Log.i(TAG, "WS: Ejecutando reintento de conexión a ${serviceToRetry.serviceName}.")
                    connectWebSocket(isRetry = true)
                } else {
                    Log.w(TAG, "WS: Reintento abortado. Job no activo, estado cambiado, o servicio objetivo diferente.")
                    if (_statusFlow.value != ConnectionStatus.CONNECTED && _statusFlow.value != ConnectionStatus.CONNECTING) {
                        _statusFlow.value = ConnectionStatus.DISCONNECTED
                    }
                }
            }
        } else {
            Log.e(TAG, "WS: Máximo de reintentos directos a ${serviceToRetry.serviceName} alcanzado. Olvidando servicio y reiniciando NSD.")
            currentResolvedService = null
            _statusFlow.value = ConnectionStatus.DISCONNECTED
            startNsdDiscovery()
        }
    }

    /**
     * Listener interno de OkHttp para gestionar los eventos del ciclo de vida del WebSocket.
     * Se asegura de que solo el listener activo procese los eventos para evitar
     * condiciones de carrera cuando se crean nuevas conexiones rápidamente.
     */
    private inner class AppWebSocketListener : WebSocketListener() {
        override fun onOpen(ws: WebSocket, response: Response) {
            if (activeWebSocketListener !== this) {
                Log.w(TAG, "WS: onOpen de listener obsoleto. URL: ${response.request.url}")
                ws.close(1001, "Listener obsoleto en onOpen")
                return
            }
            Log.i(TAG, "WS: Conexión ABIERTA: ${response.request.url}")
            directWebSocketReconnectAttempts = 0
            reconnectWebSocketJob?.cancel("Conexión WS exitosa.")
            reconnectWebSocketJob = null
            scope.launch(Dispatchers.Main.immediate) { _statusFlow.value = ConnectionStatus.CONNECTED }
        }

        override fun onMessage(ws: WebSocket, text: String) {
            if (activeWebSocketListener !== this) return
            scope.launch { processReceivedJson(text) }
        }

        override fun onMessage(ws: WebSocket, bytes: ByteString) {
            if (activeWebSocketListener !== this) return
        }

        override fun onClosing(ws: WebSocket, code: Int, reason: String) {
            if (activeWebSocketListener === this || code == 1000 || code == 1001) {
                Log.i(TAG, "WS: Conexión CERRANDO: Código=$code, Razón='$reason', URL: ${ws.request().url}")
            } else {
                Log.w(TAG, "WS: onClosing de listener obsoleto. Código=$code, Razón='$reason', URL: ${ws.request().url}")
            }
        }

        override fun onClosed(ws: WebSocket, code: Int, reason: String) {
            Log.w(TAG, "WS: Conexión CERRADA: Código=$code, Razón='$reason', URL: ${ws.request().url}")
            if (activeWebSocketListener === this) {
                activeWebSocketListener = null
                if (this@BackendConnector.webSocket === ws) {
                    this@BackendConnector.webSocket = null
                }
                scope.launch { handleWebSocketFailure("WebSocket cerrado (código $code)") }
            } else {
                Log.d(TAG, "WS: onClosed de un listener obsoleto.")
            }
        }

        override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
            val failReason = response?.message?.ifEmpty { t.message } ?: t.message ?: "Error desconocido"
            Log.e(TAG, "WS: Fallo de conexión: $failReason, URL: ${ws.request().url}", t)
            if (activeWebSocketListener === this) {
                activeWebSocketListener = null
                if (this@BackendConnector.webSocket === ws) {
                    this@BackendConnector.webSocket = null
                }
                scope.launch { handleWebSocketFailure("Fallo WebSocket: $failReason") }
            } else {
                Log.d(TAG, "WS: onFailure de un listener obsoleto.")
            }
        }
    }

    /**
     * Procesa una cadena de texto recibida, la parsea como JSON y la emite en el [receivedJsonFlow].
     * Maneja excepciones de parseo de forma segura.
     *
     * @param line La cadena de texto recibida del WebSocket.
     */
    private suspend fun processReceivedJson(line: String) {
        try {
            val json = JSONObject(line)
            _receivedJsonFlow.emit(json)
        } catch (e: JSONException) {
            Log.e(TAG, "Error al parsear JSON recibido: '$line'", e)
        }
    }

    /**
     * Realiza una limpieza final de todos los recursos del conector.
     * Debe ser llamado cuando el componente propietario (ej. ViewModel) es destruido.
     */
    fun cleanup() {
        Log.i(TAG, "Realizando cleanup de BackendConnector.")
        stop()
    }
}
