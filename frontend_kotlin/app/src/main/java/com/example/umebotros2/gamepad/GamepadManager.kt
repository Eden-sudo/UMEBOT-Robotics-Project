package com.example.umebotros2.gamepad

import android.content.Context
import android.util.Log
import android.view.KeyEvent
import android.view.MotionEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Orquesta toda la funcionalidad del gamepad, actuando como el punto de entrada principal
 * y fachada (Facade) para la interfaz de usuario y los ViewModels.
 *
 * Sus responsabilidades son:
 * 1.  Componer y gestionar las instancias de `GamepadConnectionManager`, `GamepadInputInterpreter` y `GamepadCommandBuilder`.
 * 2.  Observar el estado del gamepad activo (`activeGamepadFlow`).
 * 3.  Iniciar un bucle de sondeo (polling) periódico cuando un gamepad está activo y mapeado.
 * 4.  En cada ciclo del bucle, obtener el estado interpretado, construir un comando JSON y enviarlo
 * hacia la capa de red a través de un callback.
 * 5.  Proporcionar una API simplificada para que la UI controle el descubrimiento y la selección de gamepads.
 *
 * @param context Contexto de la aplicación, necesario para sus componentes hijos.
 * @param onCommandReadyToSend Un callback de tipo función que se invoca con un `JSONObject` cada vez
 * que un nuevo estado del gamepad está listo para ser enviado al backend del robot.
 * Esto desacopla al `GamepadManager` de la capa de red.
 */
class GamepadManager(
    private val context: Context,
    private val onCommandReadyToSend: (JSONObject) -> Unit
) {
    private val TAG = "GamepadManager"
    // Scope de corutinas propio del manager. Usa un SupervisorJob para que un fallo en una
    // corutina hija (como el bucle de envío) no cancele todo el scope.
    private val managerScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    // Instancias de los componentes especializados que este manager orquesta.
    val connectionManager = GamepadConnectionManager(context)
    private val inputInterpreter = GamepadInputInterpreter(analogDeadZone = 0.2f)
    private val commandBuilder = GamepadCommandBuilder()

    // Expone los flujos de estado del ConnectionManager para que la UI los observe.
    val activeGamepadFlow: StateFlow<GamepadInfo?> = connectionManager.activeGamepad
    val availableGamepadsFlow: StateFlow<List<GamepadInfo>> = connectionManager.availableGamepads
    val isDiscoveringFlow: StateFlow<Boolean> = connectionManager.isDiscovering

    private var gamepadStateSendJob: Job? = null
    private val sendIntervalMillis: Long = 50 // Intervalo de envío (50ms = 20Hz)

    init {
        Log.i(TAG, "GamepadManager inicializado.")
        observeActiveGamepadChanges()
    }

    /**
     * Observa de forma reactiva los cambios en el gamepad activo.
     *
     * Utiliza `collectLatest` para asegurar que si el gamepad activo cambia (se conecta uno nuevo,
     * se desconecta el actual, etc.), el bucle de envío de estado anterior se cancela
     * automáticamente y se inicia uno nuevo solo si hay un nuevo gamepad válido.
     * Esto previene bucles "zombie" y condiciones de carrera.
     */
    private fun observeActiveGamepadChanges() {
        managerScope.launch {
            activeGamepadFlow.collectLatest { gamepadInfo ->
                if (gamepadInfo != null && gamepadInfo.inputDeviceId != -1) {
                    Log.i(TAG, "Gamepad activo: ${gamepadInfo.name} (ID: ${gamepadInfo.inputDeviceId}). Iniciando bucle de estado.")
                    inputInterpreter.resetInternalStates()
                    startSendingGamepadState(gamepadInfo.inputDeviceId)
                } else {
                    Log.i(TAG, "No hay gamepad activo o no está mapeado. Deteniendo bucle de estado.")
                    stopSendingGamepadState()
                    inputInterpreter.resetInternalStates()
                }
            }
        }
    }

    /**
     * Inicia el bucle periódico que lee el estado del gamepad y lo envía.
     *
     * @param activeDeviceId El ID del `InputDevice` del gamepad activo, para asegurar que el bucle
     * procesa eventos únicamente de este dispositivo.
     */
    private fun startSendingGamepadState(activeDeviceId: Int) {
        if (gamepadStateSendJob?.isActive == true) return

        gamepadStateSendJob = managerScope.launch {
            Log.i(TAG,"Bucle de envío de estado INICIADO (Intervalo: $sendIntervalMillis ms) para Device ID: $activeDeviceId.")
            try {
                while (isActive) {
                    // Verificación de seguridad: si el gamepad activo en el sistema ya no es
                    // el que inició este bucle, el bucle se detiene.
                    if (activeGamepadFlow.value?.inputDeviceId != activeDeviceId) {
                        Log.w(TAG,"Bucle de envío: El gamepad activo ha cambiado. Deteniendo este bucle.")
                        break
                    }

                    val currentState = inputInterpreter.getCurrentStateAndResetEvents()
                    commandBuilder.buildGamepadStateCommand(currentState)?.let { commandJson ->
                        onCommandReadyToSend(commandJson)
                    }
                    delay(sendIntervalMillis)
                }
            } finally {
                Log.i(TAG,"Bucle de envío de estado FINALIZADO para Device ID: $activeDeviceId.")
            }
        }
    }

    /** Detiene el bucle de envío de estado del gamepad. */
    private fun stopSendingGamepadState() {
        if (gamepadStateSendJob?.isActive == true) {
            gamepadStateSendJob?.cancel()
            Log.i(TAG,"Bucle de envío de estado DETENIDO.")
        }
        gamepadStateSendJob = null
    }

    //region API de Fachada para la UI (Delega a GamepadConnectionManager)

    /** Inicia el descubrimiento de dispositivos Bluetooth. */
    fun startGamepadDiscovery() {
        if (connectionManager.isBluetoothEnabled() && connectionManager.hasRequiredPermissions(includeScanPermissions = true)) {
            Log.i(TAG, "Iniciando descubrimiento de dispositivos Bluetooth...")
            connectionManager.startDeviceDiscovery()
        } else {
            Log.w(TAG, "No se puede iniciar descubrimiento: BT no habilitado o faltan permisos.")
        }
    }

    /** Detiene el descubrimiento de dispositivos Bluetooth. */
    fun stopGamepadDiscovery() {
        Log.i(TAG, "Deteniendo descubrimiento de dispositivos Bluetooth.")
        connectionManager.stopDeviceDiscovery()
    }

    /**
     * Intenta seleccionar un gamepad por su dirección MAC.
     * @param address La dirección MAC del dispositivo a seleccionar.
     */
    fun selectGamepadByAddress(address: String) {
        Log.i(TAG, "Intentando seleccionar gamepad con dirección: $address")
        connectionManager.selectGamepadByAddress(address)
    }

    /** Limpia la selección del gamepad activo. */
    fun clearSelectedGamepad() {
        Log.i(TAG, "Limpiando selección de gamepad activo.")
        connectionManager.clearSelectedGamepad()
    }

    //endregion

    //region Procesamiento de Eventos de Input (Punto de entrada desde la Activity/View)

    /**
     * Procesa un KeyEvent crudo. Debe ser llamado desde `dispatchKeyEvent` de la Activity.
     * Solo procesa el evento si proviene del gamepad actualmente activo.
     *
     * @param event El evento de teclado/botón a procesar.
     * @return `true` si el evento fue consumido por el gamepad activo, `false` en caso contrario.
     */
    fun processKeyEvent(event: KeyEvent): Boolean {
        val activeId = activeGamepadFlow.value?.inputDeviceId
        if (activeId != null && activeId != -1 && event.deviceId == activeId) {
            inputInterpreter.processKeyEvent(event)
            return true // Evento consumido
        }
        return false // Evento no consumido
    }

    /**
     * Procesa un MotionEvent crudo. Debe ser llamado desde `dispatchGenericMotionEvent` de la Activity.
     * Solo procesa el evento si proviene del gamepad actualmente activo.
     *
     * @param event El evento de movimiento (sticks, D-Pad) a procesar.
     * @return `true` si el evento fue consumido por el gamepad activo, `false` en caso contrario.
     */
    fun processMotionEvent(event: MotionEvent): Boolean {
        val activeId = activeGamepadFlow.value?.inputDeviceId
        if (activeId != null && activeId != -1 && event.deviceId == activeId &&
            (event.source and android.view.InputDevice.SOURCE_JOYSTICK == android.view.InputDevice.SOURCE_JOYSTICK ||
             event.source and android.view.InputDevice.SOURCE_GAMEPAD == android.view.InputDevice.SOURCE_GAMEPAD)) {
            inputInterpreter.processMotionEvent(event)
            return true // Evento consumido
        }
        return false // Evento no consumido
    }
    //endregion

    /** Libera recursos y cancela todas las corutinas activas en este manager. */
    fun cleanup() {
        Log.i(TAG, "Realizando cleanup de GamepadManager.")
        stopSendingGamepadState()
        connectionManager.cleanup()
        managerScope.cancel()
    }
}
