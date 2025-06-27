package com.example.umebotros2.gamepad

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothClass
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.input.InputManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.InputDevice
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Gestiona el ciclo de vida completo de la conexión de gamepads Bluetooth.
 *
 * Esta clase es responsable de:
 * 1.  Descubrir dispositivos Bluetooth cercanos que puedan ser gamepads.
 * 2.  Listar los gamepads ya emparejados con el sistema.
 * 3.  Permitir la selección de un gamepad activo.
 * 4.  Monitorear el estado de conexión/desconexión de los dispositivos de entrada a nivel de sistema.
 * 5.  Mapear un `BluetoothDevice` (el dispositivo físico) a un `InputDevice` de Android (el que genera eventos),
 * que es un paso crucial para la lectura de datos.
 *
 * Expone su estado de forma reactiva mediante `StateFlow` para que la UI pueda observar los cambios.
 *
 * @param context El contexto de la aplicación, necesario para acceder a los servicios del sistema.
 */
class GamepadConnectionManager(private val context: Context) : InputManager.InputDeviceListener {

    private val TAG = "GamepadConnectionMgr"

    private val bluetoothManager: BluetoothManager? =
        context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager?.adapter

    private val inputManager: InputManager =
        context.getSystemService(Context.INPUT_SERVICE) as InputManager
    private var isInputDeviceListenerRegistered = false

    /**
     * Expone el gamepad actualmente seleccionado y activo. Emite `null` si no hay ninguno.
     */
    private val _activeGamepad = MutableStateFlow<GamepadInfo?>(null)
    val activeGamepad: StateFlow<GamepadInfo?> = _activeGamepad.asStateFlow()

    /**
     * Expone una lista de todos los gamepads disponibles (encontrados y emparejados).
     */
    private val _availableGamepads = MutableStateFlow<List<GamepadInfo>>(emptyList())
    val availableGamepads: StateFlow<List<GamepadInfo>> = _availableGamepads.asStateFlow()

    /**
     * Indica si un proceso de descubrimiento de dispositivos Bluetooth está actualmente en curso.
     */
    private val _isDiscovering = MutableStateFlow(false)
    val isDiscovering: StateFlow<Boolean> = _isDiscovering.asStateFlow()

    private val processedDeviceAddressesDuringScan = mutableSetOf<String>()
    private var isDiscoveryReceiverRegistered = false
    private var lastSuccessfullyConnectedGamepadAddress: String? = null

    init {
        Log.d(TAG, "GamepadConnectionManager inicializado.")
        registerInputDeviceListener()
        loadPairedGamepadsIntoAvailableList(clearCurrentList = true)
    }

    //region Implementación de InputManager.InputDeviceListener
    /**
     * Callback del sistema que se invoca cuando un nuevo dispositivo de entrada es añadido al sistema.
     * Es crucial para detectar cuándo un gamepad se conecta físicamente.
     */
    @SuppressLint("MissingPermission")
    override fun onInputDeviceAdded(deviceId: Int) {
        val addedDevice = inputManager.getInputDevice(deviceId)
        val deviceName = addedDevice?.name ?: "Desconocido"
        Log.i(TAG, "InputDeviceListener: Dispositivo AÑADIDO, ID: $deviceId, Nombre: $deviceName")

        // Intenta re-seleccionar automáticamente si el dispositivo añadido es el último que estuvo activo.
        lastSuccessfullyConnectedGamepadAddress?.let { address ->
            val btDevice = bluetoothAdapter?.getRemoteDevice(address)
            if (btDevice != null && addedDevice != null) {
                val sources = addedDevice.sources
                val isGamepadSource = (sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD)
                val isJoystickSource = (sources and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK)
                if (isGamepadSource || isJoystickSource) {
                    val potentialMatchName = try { btDevice.name } catch (e: SecurityException) { null }
                    val descriptor = addedDevice.descriptor
                    val matchesByDescriptor = descriptor?.contains(btDevice.address, ignoreCase = true) == true
                    val matchesByName = potentialMatchName != null && deviceName.equals(potentialMatchName, ignoreCase = true)
                    if (matchesByDescriptor || matchesByName) {
                        Log.i(TAG, "Dispositivo añadido $deviceId ($deviceName) parece ser el último gamepad activo ($address). Intentando re-selección.")
                        selectGamepadByAddress(address)
                        return
                    }
                }
            }
        }
        // Si un gamepad estaba seleccionado pero no mapeado (ID = -1), reintenta el mapeo.
        _activeGamepad.value?.let { activeInfo ->
            if (activeInfo.inputDeviceId == -1) {
                Log.d(TAG, "InputDeviceListener: Gamepad activo ${activeInfo.name} estaba pendiente de InputID. Refrescando mapeo.")
                refreshActiveGamepadMapping()
            }
        }
        // Actualiza la lista de disponibles con la nueva información.
        loadPairedGamepadsIntoAvailableList(clearCurrentList = false)
    }

    /**
     * Callback del sistema que se invoca cuando un dispositivo de entrada es eliminado (desconectado).
     */
    override fun onInputDeviceRemoved(deviceId: Int) {
        Log.i(TAG, "InputDeviceListener: Dispositivo ELIMINADO, ID: $deviceId")
        _activeGamepad.value?.let { currentActive ->
            if (currentActive.inputDeviceId == deviceId) {
                Log.w(TAG, "InputDeviceListener: Gamepad activo (Nombre: ${currentActive.name}, ID: $deviceId) desconectado.")
                if (currentActive.inputDeviceId != -1) {
                    lastSuccessfullyConnectedGamepadAddress = currentActive.address
                }
                _activeGamepad.update { null }
            }
        }
    }

    /**
     * Callback del sistema que se invoca cuando las propiedades de un dispositivo de entrada cambian.
     */
    override fun onInputDeviceChanged(deviceId: Int) {
        Log.i(TAG, "InputDeviceListener: Dispositivo CAMBIADO, ID: $deviceId")
        _activeGamepad.value?.let { currentActive ->
            if (currentActive.inputDeviceId == deviceId) {
                Log.d(TAG, "InputDeviceListener: Gamepad activo (ID: $deviceId) ha cambiado. Refrescando mapeo.")
                refreshActiveGamepadMapping()
            }
        }
    }
    //endregion

    /**
     * Receptor de eventos de Bluetooth para el proceso de descubrimiento de dispositivos.
     */
    private val discoveryReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission")
        override fun onReceive(context: Context, intent: Intent) {
            val action: String? = intent.action
            when (action) {
                BluetoothDevice.ACTION_FOUND -> {
                    val device: BluetoothDevice? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                    }
                    device?.let { foundDevice ->
                        try {
                            val deviceName = foundDevice.name ?: "Dispositivo Desconocido"
                            val deviceAddress = foundDevice.address
                            val isDeviceLikelyGamepad = isLikelyGamepad(foundDevice.bluetoothClass, deviceName)

                            if (!processedDeviceAddressesDuringScan.contains(deviceAddress)) {
                                Log.i(TAG, "Dispositivo procesando (ACTION_FOUND): $deviceName ($deviceAddress), ¿Gamepad?: $isDeviceLikelyGamepad")
                                processedDeviceAddressesDuringScan.add(deviceAddress)
                                _availableGamepads.update { currentList ->
                                    val existingDeviceIndex = currentList.indexOfFirst { it.address == deviceAddress }
                                    val newList = if (existingDeviceIndex == -1) {
                                        currentList + GamepadInfo(deviceAddress, deviceName, -1, isDeviceLikelyGamepad)
                                    } else {
                                        currentList // Evitar duplicados, se actualiza al final si es necesario.
                                    }
                                    newList.sortedWith(compareByDescending<GamepadInfo> { it.isLikelyGamepad }.thenBy { it.name ?: it.address })
                                }
                            }
                        } catch (e: SecurityException) { Log.e(TAG, "SecurityException en ACTION_FOUND: ${e.message}") }
                        catch (e: Exception) { Log.e(TAG, "Excepción general en ACTION_FOUND: ${e.message}") }
                    }
                }
                BluetoothAdapter.ACTION_DISCOVERY_STARTED -> {
                    Log.i(TAG, "Descubrimiento Bluetooth INICIADO.")
                    processedDeviceAddressesDuringScan.clear()
                    val initialList = mutableListOf<GamepadInfo>()
                    // Mantener el gamepad activo en la lista durante el escaneo
                    _activeGamepad.value?.let { active ->
                        if (active.inputDeviceId != -1) {
                            initialList.add(active)
                            processedDeviceAddressesDuringScan.add(active.address)
                        }
                    }
                    loadPairedGamepadsIntoAvailableList(clearCurrentList = false, intoList = initialList)
                    _availableGamepads.value = initialList.distinctBy { it.address }
                        .sortedWith(compareByDescending<GamepadInfo> { it.isLikelyGamepad }.thenBy { it.name ?: it.address })
                    _isDiscovering.value = true
                }
                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> {
                    Log.i(TAG, "Descubrimiento Bluetooth FINALIZADO.")
                    _isDiscovering.value = false
                }
            }
        }
    }

    /** Comprueba si el dispositivo soporta Bluetooth. */
    fun isBluetoothSupported(): Boolean = bluetoothAdapter != null

    /** Comprueba si el Bluetooth está actualmente habilitado. */
    fun isBluetoothEnabled(): Boolean = bluetoothAdapter?.isEnabled ?: false

    /**
     * Verifica si la aplicación tiene los permisos de Bluetooth necesarios.
     * @param includeScanPermissions Si es `true`, también verifica los permisos para escanear,
     * que son más estrictos (ej. Localización en APIs antiguas).
     * @return `true` si todos los permisos requeridos están concedidos.
     */
    fun hasRequiredPermissions(includeScanPermissions: Boolean = false): Boolean {
        val permissions = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            permissions.add(Manifest.permission.BLUETOOTH_CONNECT)
            if (includeScanPermissions) permissions.add(Manifest.permission.BLUETOOTH_SCAN)
        } else {
            permissions.add(Manifest.permission.BLUETOOTH)
            permissions.add(Manifest.permission.BLUETOOTH_ADMIN)
            if (includeScanPermissions) permissions.add(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        return permissions.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED }
    }

    /**
     * Carga los dispositivos Bluetooth ya emparejados con el teléfono y los añade a la lista
     * de gamepads disponibles, fusionándolos con la lista existente si se especifica.
     */
    @SuppressLint("MissingPermission")
    private fun loadPairedGamepadsIntoAvailableList(clearCurrentList: Boolean = true, intoList: MutableList<GamepadInfo>? = null) {
        if (!hasRequiredPermissions(false) || !isBluetoothEnabled()) return
        val pairedDeviceInfos = mutableListOf<GamepadInfo>()
        try {
            bluetoothAdapter?.bondedDevices?.forEach { device ->
                val deviceName = device.name ?: "Dispositivo Desconocido"
                val isGamepad = isLikelyGamepad(device.bluetoothClass, deviceName)
                pairedDeviceInfos.add(GamepadInfo(device.address, deviceName, -1, isGamepad))
            }
        } catch (e: SecurityException) { Log.e(TAG, "SecurityException al cargar emparejados: ${e.message}") }

        _availableGamepads.update { currentList ->
            val listToUse = intoList ?: (if (clearCurrentList) mutableListOf() else currentList.toMutableList())
            val currentAddressesInList = listToUse.map { it.address }.toSet()
            pairedDeviceInfos.forEach { pairedInfo ->
                if (!currentAddressesInList.contains(pairedInfo.address)) {
                    listToUse.add(pairedInfo)
                }
            }
            listToUse.distinctBy { it.address }
                .sortedWith(compareByDescending<GamepadInfo> { it.isLikelyGamepad }.thenBy { it.name ?: it.address })
        }
        Log.d(TAG, "Gamepads emparejados cargados/fusionados. Actual: ${_availableGamepads.value.size}")
    }

    /**
     * Inicia un escaneo de dispositivos Bluetooth para encontrar nuevos gamepads.
     * Se asegura de tener los permisos necesarios y de que el Bluetooth esté activado.
     */
    @SuppressLint("MissingPermission")
    fun startDeviceDiscovery() {
        if (!isBluetoothSupported()) { Log.w(TAG, "Discovery: BT no soportado."); return }
        if (!hasRequiredPermissions(true)) { Log.w(TAG, "Discovery: Faltan permisos."); return }
        if (!isBluetoothEnabled()) { Log.w(TAG, "Discovery: BT no habilitado."); return }
        if (bluetoothAdapter?.isDiscovering == true) { Log.d(TAG, "Discovery ya en progreso."); return }

        if (!isDiscoveryReceiverRegistered) {
            val filter = IntentFilter().apply {
                addAction(BluetoothDevice.ACTION_FOUND)
                addAction(BluetoothAdapter.ACTION_DISCOVERY_STARTED)
                addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
            }
            try {
                // Registrar el receiver de forma segura según la versión de Android
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    context.registerReceiver(discoveryReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
                } else {
                    context.registerReceiver(discoveryReceiver, filter)
                }
                isDiscoveryReceiverRegistered = true
                Log.d(TAG, "DiscoveryReceiver registrado.")
            } catch (e: Exception) { Log.e(TAG, "Error registrando DiscoveryReceiver: ${e.message}"); return }
        }
        if (bluetoothAdapter?.startDiscovery() == true) {
            Log.i(TAG, "Solicitando inicio de descubrimiento Bluetooth...")
        } else {
            Log.e(TAG, "Fallo al solicitar inicio de descubrimiento Bluetooth.")
            _isDiscovering.value = false
        }
    }

    /**
     * Detiene el proceso de descubrimiento de dispositivos Bluetooth activo.
     */
    @SuppressLint("MissingPermission")
    fun stopDeviceDiscovery() {
        if (bluetoothAdapter?.isDiscovering == true) {
            if (hasRequiredPermissions(true)) {
                bluetoothAdapter.cancelDiscovery()
                Log.i(TAG, "Descubrimiento Bluetooth detenido explícitamente.")
            }
        }
        if (isDiscoveryReceiverRegistered) {
            try { context.unregisterReceiver(discoveryReceiver) }
            catch (e: Exception) { Log.e(TAG, "Excepción desregistrando: ${e.message}") }
            finally { isDiscoveryReceiverRegistered = false; Log.d(TAG, "DiscoveryReceiver desregistrado.")}
        }
        _isDiscovering.value = false
    }

    private fun isLikelyGamepad(deviceClass: android.bluetooth.BluetoothClass?, deviceName: String?): Boolean {
        deviceClass?.let { btClass ->
            if (btClass.majorDeviceClass == BluetoothClass.Device.Major.PERIPHERAL) {
                when (btClass.deviceClass) {
                    // Constantes para periféricos tipo Gamepad y Joystick
                    BluetoothClass.Device.PERIPHERAL_GAMEPAD,
                    BluetoothClass.Device.PERIPHERAL_JOYSTICK -> return true
                }
            }
        }
        deviceName?.let { name ->
            val lowerName = name.lowercase()
            val keywords = listOf("controller", "gamepad", "xbox", "joystick", "dualshock", "dualsense", "joy-con")
            return keywords.any { lowerName.contains(it) }
        }
        return false
    }

    /**
     * Selecciona un gamepad de la lista de disponibles usando su dirección MAC y lo establece como activo.
     * Este es el paso final para conectar un gamepad, donde se intenta mapear al InputDevice del sistema.
     *
     * @param address La dirección MAC del gamepad a seleccionar.
     */
    @SuppressLint("MissingPermission")
    fun selectGamepadByAddress(address: String) {
        if (!hasRequiredPermissions(false) || !isBluetoothEnabled()) {
            _activeGamepad.value = null; return
        }
        stopDeviceDiscovery()
        val gamepadToSelect = _availableGamepads.value.find { it.address == address }
        if (gamepadToSelect != null) {
            val selectedBtDevice: BluetoothDevice? = try {
                bluetoothAdapter?.getRemoteDevice(address)
            } catch (e: Exception) { null }

            if (selectedBtDevice != null) {
                try {
                    val deviceNameFromBt = selectedBtDevice.name ?: gamepadToSelect.name
                    val inputDeviceId = findInputDeviceIdForBluetoothDevice(selectedBtDevice)
                    val finalGamepadInfo = gamepadToSelect.copy(name = deviceNameFromBt, inputDeviceId = inputDeviceId)
                    _activeGamepad.value = finalGamepadInfo

                    if (inputDeviceId != -1) {
                        lastSuccessfullyConnectedGamepadAddress = address
                        Log.i(TAG, "Gamepad seleccionado y MAPEADO: ${finalGamepadInfo.name}, InputID: ${finalGamepadInfo.inputDeviceId}")
                    } else {
                        Log.w(TAG, "Gamepad seleccionado PERO NO MAPEADO: ${finalGamepadInfo.name}. Esperando a onInputDeviceAdded.")
                    }
                } catch (e: SecurityException) {
                    Log.e(TAG, "SecurityException con BTDevice ($address): ${e.message}")
                    _activeGamepad.value = null
                }
            } else {
                Log.w(TAG, "No se pudo obtener BluetoothDevice para $address.")
                _activeGamepad.value = null
            }
        } else {
            Log.w(TAG, "Dispositivo con $address no encontrado para seleccionar.")
            _activeGamepad.value = null
        }
    }

    /**
     * Deselecciona el gamepad activo.
     */
    fun clearSelectedGamepad() {
        _activeGamepad.value = null
        lastSuccessfullyConnectedGamepadAddress = null
        Log.i(TAG, "Gamepad deseleccionado explícitamente.")
    }

    /**
     * La función más crítica: intenta encontrar el ID del `InputDevice` del sistema que corresponde
     * a un `BluetoothDevice` específico. La coincidencia se basa en la dirección MAC (la más fiable)
     * o en el nombre del dispositivo.
     *
     * @param bluetoothDevice El dispositivo Bluetooth para el cual se busca el ID de entrada.
     * @return El ID del `InputDevice` o -1 si no se encuentra.
     */
    @SuppressLint("MissingPermission")
    private fun findInputDeviceIdForBluetoothDevice(bluetoothDevice: BluetoothDevice): Int {
        if (!hasRequiredPermissions(false)) return -1
        val deviceNameFromBt = try { bluetoothDevice.name } catch (e: SecurityException) { null } ?: "Desconocido"
        try {
            val deviceIds = inputManager.inputDeviceIds
            for (id in deviceIds) {
                val inputDevice = inputManager.getInputDevice(id)
                if (inputDevice != null) {
                    val sources = inputDevice.sources
                    val isGamepadSource = (sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD)
                    val isJoystickSource = (sources and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK)

                    if (isGamepadSource || isJoystickSource) {
                        // Método 1: El más fiable. Buscar la MAC en el descriptor del InputDevice.
                        if (inputDevice.descriptor?.contains(bluetoothDevice.address, ignoreCase = true) == true) {
                            Log.i(TAG, "MAPEO EXITOSO (Descriptor MAC): ID $id para ${bluetoothDevice.address}")
                            return id
                        }
                        // Método 2: Comparar nombres. Menos fiable pero un buen fallback.
                        if (deviceNameFromBt != "Desconocido" && inputDevice.name?.equals(deviceNameFromBt, ignoreCase = true) == true) {
                            Log.i(TAG, "MAPEO EXITOSO (Nombre): ID $id para '$deviceNameFromBt'")
                            return id
                        }
                    }
                }
            }
        } catch (e: Exception) { Log.e(TAG, "Excepción en findInputDeviceId: ${e.message}") }
        Log.w(TAG, "FALLO EL MAPEO para BTDevice '$deviceNameFromBt' (MAC: ${bluetoothDevice.address}).")
        return -1
    }

    /**
     * Re-evalúa el mapeo del gamepad activo actualmente para encontrar su `InputDevice` ID.
     * Es útil si el dispositivo se conectó pero el sistema tardó en registrarlo como InputDevice.
     */
    @SuppressLint("MissingPermission")
    fun refreshActiveGamepadMapping() {
        val currentActive = _activeGamepad.value ?: return
        try {
            val btDevice = bluetoothAdapter?.getRemoteDevice(currentActive.address)
            if (btDevice != null) {
                val deviceNameFromBt = try {btDevice.name} catch(e: SecurityException){null} ?: currentActive.name
                val newId = findInputDeviceIdForBluetoothDevice(btDevice)
                if (newId != currentActive.inputDeviceId || deviceNameFromBt != currentActive.name) {
                    _activeGamepad.update { it?.copy(inputDeviceId = newId, name = deviceNameFromBt) }
                    Log.i(TAG, "Gamepad '${currentActive.name}' InputID refrescado a $newId, Nombre a '$deviceNameFromBt'")
                    if (newId != -1) {
                        lastSuccessfullyConnectedGamepadAddress = currentActive.address
                    }
                }
            } else {
                _activeGamepad.update { it?.copy(inputDeviceId = -1) }
            }
        } catch (e: Exception) {
            _activeGamepad.update { it?.copy(inputDeviceId = -1) }
        }
    }

    private fun registerInputDeviceListener() {
        if (!isInputDeviceListenerRegistered) {
            val mainHandler = Handler(Looper.getMainLooper())
            inputManager.registerInputDeviceListener(this, mainHandler)
            isInputDeviceListenerRegistered = true
            Log.i(TAG, "InputDeviceListener registrado.")
        }
    }

    private fun unregisterInputDeviceListener() {
        if (isInputDeviceListenerRegistered) {
            try { inputManager.unregisterInputDeviceListener(this) }
            catch (e: Exception) { Log.e(TAG, "Excepción al desregistrar InputDeviceListener: ${e.message}") }
            isInputDeviceListenerRegistered = false
            Log.i(TAG, "InputDeviceListener desregistrado.")
        }
    }

    /**
     * Libera todos los recursos y desregistra los listeners. Debe llamarse cuando
     * el gestor ya no es necesario para evitar fugas de memoria.
     */
    fun cleanup() {
        Log.d(TAG, "Cleanup llamado en GamepadConnectionManager.")
        stopDeviceDiscovery()
        unregisterInputDeviceListener()
    }
}
