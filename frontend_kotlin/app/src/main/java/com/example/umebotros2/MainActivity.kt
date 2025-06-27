package com.example.umebotros2

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.view.KeyEvent
import android.view.MenuItem
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.umebotros2.backend.ConnectionStatus
import com.example.umebotros2.databinding.ActivityMainBinding
import com.example.umebotros2.gamepad.GamepadListAdapter
import com.example.umebotros2.gamepad.GamepadManager
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.switchmaterial.SwitchMaterial
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * La actividad principal y única de la aplicación, que actúa como el controlador de la vista (la 'V' en MVVM).
 *
 * Sus responsabilidades principales son:
 * - Inflar y gestionar el layout principal y el panel de navegación lateral (Drawer).
 * - Observar el `ChatUiState` del `ChatViewModel` y actualizar todos los componentes de la UI para reflejar el estado actual.
 * - Capturar las interacciones del usuario (clics, texto, selecciones de spinners) y delegarlas al `ChatViewModel`.
 * - Interceptar los eventos de entrada de bajo nivel (`KeyEvent`, `MotionEvent`) y pasarlos al `GamepadManager` para su procesamiento.
 * - Gestionar el ciclo de vida de los componentes ligados a la UI, como el `GamepadManager`.
 */
class MainActivity : AppCompatActivity() {

    private val viewModel: ChatViewModel by viewModels()
    private lateinit var messageAdapter: MessageAdapter
    private lateinit var binding: ActivityMainBinding
    private lateinit var drawerToggle: ActionBarDrawerToggle

    // Referencias a los widgets del Navigation Drawer
    private var audioSourceSwitch: SwitchMaterial? = null
    private var chatHistorySwitch: SwitchMaterial? = null
    private var aiPersonalitySpinner: Spinner? = null
    private var aiModelSpinner: Spinner? = null
    private lateinit var personalityAdapter: ArrayAdapter<String>
    private lateinit var modelAdapter: ArrayAdapter<String>

    // Componentes y referencias para la funcionalidad del Gamepad
    private lateinit var gamepadManager: GamepadManager
    private var switchEnableGamepadControl: SwitchMaterial? = null
    private var textViewGamepadConnectionStatus: TextView? = null
    private var buttonRefreshGamepadDevices: Button? = null
    private var recyclerviewGamepadList: RecyclerView? = null
    private lateinit var gamepadListAdapter: GamepadListAdapter
    private lateinit var requestBluetoothEnableLauncher: ActivityResultLauncher<Intent>
    private lateinit var requestBluetoothPermissionsLauncher: ActivityResultLauncher<Array<String>>

    private var isUpdatingEditTextProgrammatically = false
    private val TAG_MAIN_ACTIVITY = "MainActivity"
    private val TAG_DISPATCH_LOG = "MainActivity_Dispatch"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        Log.d(TAG_MAIN_ACTIVITY, "onCreate - Iniciando MainActivity")

        setSupportActionBar(binding.toolbar)
        setupDrawerToggle()
        setupChatRecyclerView()
        setupConfigurationWidgets()

        initializeBluetoothLaunchers()
        // Se inicializa el GamepadManager, pasándole el callback que conecta su salida con el ViewModel.
        gamepadManager = GamepadManager(applicationContext) { commandJson ->
            viewModel.sendGamepadCommand(commandJson)
        }
        setupGamepadDrawerUI()
        observeGamepadManagerEvents()

        setupInputAreaInteractions()
        setupGeneralConfigClickListeners()
        observeChatViewModel()
        setupOnBackPressedHandling()
    }

    //region Input Event Handling
    /**
     * Se sobreescribe para interceptar todos los eventos de teclado antes de que lleguen a las vistas hijas.
     * Aunque no se usa para lógica principal, es útil para depurar eventos de D-Pad.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val keyCode = event.keyCode
        val action = event.action
        Log.d(TAG_DISPATCH_LOG, "dispatchKeyEvent - KeyCode: ${KeyEvent.keyCodeToString(keyCode)}, Action: $action, DeviceId: ${event.deviceId}, Source: ${event.source}")
        if (keyCode == KeyEvent.KEYCODE_DPAD_UP || keyCode == KeyEvent.KEYCODE_DPAD_DOWN || keyCode == KeyEvent.KEYCODE_DPAD_LEFT || keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) {
            Log.w(TAG_DISPATCH_LOG, "¡¡¡D-PAD EVENTO RECIBIDO EN DISPATCH!!! KeyCode: ${KeyEvent.keyCodeToString(keyCode)}, Action: $action")
        }
        return super.dispatchKeyEvent(event)
    }

    /**
     * Se sobreescribe para capturar eventos de "botón presionado".
     * Si el control por gamepad está activado, delega el evento al `gamepadManager`.
     * @return `true` si el evento fue consumido, `false` de lo contrario.
     */
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        Log.d(TAG_MAIN_ACTIVITY, "onKeyDown: keyCode=${KeyEvent.keyCodeToString(keyCode)}, action=${event.action}, deviceId=${event.deviceId}")
        val isGamepadSwitchChecked = switchEnableGamepadControl?.isChecked ?: false

        if (isGamepadSwitchChecked) {
            if (gamepadManager.processKeyEvent(event)) {
                Log.d(TAG_MAIN_ACTIVITY, "onKeyDown: Evento MANEJADO por GamepadManager para ${KeyEvent.keyCodeToString(keyCode)}")
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }

    /**
     * Se sobreescribe para capturar eventos de "botón liberado".
     * Si el control por gamepad está activado, delega el evento al `gamepadManager`.
     * @return `true` si el evento fue consumido, `false` de lo contrario.
     */
    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        Log.d(TAG_MAIN_ACTIVITY, "onKeyUp: keyCode=${KeyEvent.keyCodeToString(keyCode)}, action=${event.action}, deviceId=${event.deviceId}")
        val isGamepadSwitchChecked = switchEnableGamepadControl?.isChecked ?: false

        if (isGamepadSwitchChecked) {
            if (gamepadManager.processKeyEvent(event)) {
                Log.d(TAG_MAIN_ACTIVITY, "onKeyUp: Evento MANEJADO por GamepadManager para ${KeyEvent.keyCodeToString(keyCode)}")
                return true
            }
        }
        return super.onKeyUp(keyCode, event)
    }

    /**
     * Se sobreescribe para capturar eventos de movimiento genéricos, como los de los joysticks y el D-Pad.
     * Si el control por gamepad está activado, delega el evento al `gamepadManager`.
     * @return `true` si el evento fue consumido, `false` de lo contrario.
     */
    override fun onGenericMotionEvent(event: MotionEvent): Boolean {
        val logMessage = StringBuilder("onGenericMotionEvent: Source=${event.source}, DeviceId=${event.deviceId}\n")
        logMessage.append("  AXIS_X: ${event.getAxisValue(MotionEvent.AXIS_X)}\n")
        logMessage.append("  AXIS_Y: ${event.getAxisValue(MotionEvent.AXIS_Y)}\n")
        logMessage.append("  AXIS_Z: ${event.getAxisValue(MotionEvent.AXIS_Z)}\n")
        logMessage.append("  AXIS_RZ: ${event.getAxisValue(MotionEvent.AXIS_RZ)}\n")
        val hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
        val hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        logMessage.append("  AXIS_HAT_X: $hatX\n")
        logMessage.append("  AXIS_HAT_Y: $hatY")
        Log.d(TAG_MAIN_ACTIVITY, logMessage.toString())

        if (switchEnableGamepadControl?.isChecked == true && gamepadManager.processMotionEvent(event)) {
            Log.d(TAG_MAIN_ACTIVITY, "onGenericMotionEvent: Evento de STICK ANALÓGICO MANEJADO por GamepadManager")
            return true
        }
        return super.onGenericMotionEvent(event)
    }
    //endregion

    //region Setup Methods
    /** Inicializa los `ActivityResultLauncher` para solicitar permisos y la activación de Bluetooth. */
    private fun initializeBluetoothLaunchers() {
        Log.d(TAG_MAIN_ACTIVITY, "initializeBluetoothLaunchers")
        requestBluetoothEnableLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == RESULT_OK) {
                Log.i(TAG_MAIN_ACTIVITY, "Bluetooth activado por el usuario.")
                proceedWithGamepadFunctionalityActivation()
            } else {
                Log.w(TAG_MAIN_ACTIVITY, "El usuario no activó Bluetooth.")
                Snackbar.make(binding.root, "Se necesita Bluetooth para usar el gamepad.", Snackbar.LENGTH_LONG).show()
                switchEnableGamepadControl?.isChecked = false
            }
        }

        requestBluetoothPermissionsLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissions ->
            val allGranted = permissions.entries.all { it.value }
            if (allGranted) {
                Log.i(TAG_MAIN_ACTIVITY, "Permisos Bluetooth concedidos post-solicitud.")
                if (switchEnableGamepadControl?.isChecked == true) {
                    proceedWithGamepadFunctionalityActivation()
                }
            } else {
                Log.w(TAG_MAIN_ACTIVITY, "Permisos Bluetooth denegados post-solicitud.")
                Snackbar.make(binding.root, "Se requieren permisos de Bluetooth para el gamepad.", Snackbar.LENGTH_LONG).show()
                switchEnableGamepadControl?.isChecked = false
            }
        }
    }

    /** Configura el `ActionBarDrawerToggle` para el panel de navegación lateral. */
    private fun setupDrawerToggle() {
        drawerToggle = ActionBarDrawerToggle(this, binding.drawerLayout, binding.toolbar, R.string.drawer_open, R.string.drawer_close)
        binding.drawerLayout.addDrawerListener(drawerToggle)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.setHomeButtonEnabled(true)
    }

    /** Configura el `RecyclerView` principal para mostrar los mensajes del chat. */
    private fun setupChatRecyclerView() {
        messageAdapter = MessageAdapter()
        binding.chatRecyclerView.apply {
            layoutManager = LinearLayoutManager(this@MainActivity).apply { stackFromEnd = true }
            adapter = messageAdapter
            // Listener para hacer auto-scroll cuando aparece el teclado.
            addOnLayoutChangeListener { _, _, _, _, bottom, _, _, _, oldBottom ->
                if (bottom < oldBottom && messageAdapter.itemCount > 0) {
                    postDelayed({ scrollToPosition(messageAdapter.itemCount - 1) }, 100)
                }
            }
        }
    }

    /** Inicializa las referencias a los widgets del panel de configuración y sus adaptadores. */
    private fun setupConfigurationWidgets() {
        val drawerContentLayout = binding.drawerContent
        audioSourceSwitch = drawerContentLayout.findViewById(R.id.switch_audio_source)
        chatHistorySwitch = drawerContentLayout.findViewById(R.id.switch_chat_history_visibility)
        aiPersonalitySpinner = drawerContentLayout.findViewById(R.id.spinner_ai_personality)
        aiModelSpinner = drawerContentLayout.findViewById(R.id.spinner_ai_model_backend)

        personalityAdapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, mutableListOf())
        personalityAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        aiPersonalitySpinner?.adapter = personalityAdapter

        modelAdapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, mutableListOf())
        modelAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        aiModelSpinner?.adapter = modelAdapter
    }

    /** Inicializa las referencias a los widgets de la sección del gamepad en el panel y sus listeners. */
    private fun setupGamepadDrawerUI() {
        Log.d(TAG_MAIN_ACTIVITY, "setupGamepadDrawerUI")
        val drawerContentLayout = binding.drawerContent
        switchEnableGamepadControl = drawerContentLayout.findViewById(R.id.switch_enable_gamepad_control)
        textViewGamepadConnectionStatus = drawerContentLayout.findViewById(R.id.textview_gamepad_connection_status)
        buttonRefreshGamepadDevices = drawerContentLayout.findViewById(R.id.button_refresh_gamepad_devices)
        recyclerviewGamepadList = drawerContentLayout.findViewById(R.id.recyclerview_gamepad_list)

        gamepadListAdapter = GamepadListAdapter { gamepadInfo ->
            Log.i(TAG_MAIN_ACTIVITY, "Gamepad UI: Selección de ${gamepadInfo.name ?: gamepadInfo.address}")
            gamepadManager.selectGamepadByAddress(gamepadInfo.address)
        }
        recyclerviewGamepadList?.apply {
            adapter = gamepadListAdapter
            layoutManager = LinearLayoutManager(this@MainActivity)
        }

        switchEnableGamepadControl?.setOnCheckedChangeListener { buttonView, isChecked ->
            // Reaccionar solo a interacciones del usuario para evitar bucles de eventos
            if (buttonView.isPressed) {
                if (isChecked) {
                    Log.i(TAG_MAIN_ACTIVITY, "Switch Gamepad ACTIVADO por el usuario.")
                    proceedWithGamepadFunctionalityActivation()
                } else {
                    Log.i(TAG_MAIN_ACTIVITY, "Switch Gamepad DESACTIVADO por el usuario.")
                    gamepadManager.clearSelectedGamepad()
                    updateGamepadControlsVisibility(false)
                }
            }
        }

        buttonRefreshGamepadDevices?.setOnClickListener {
            if (switchEnableGamepadControl?.isChecked == true) {
                if (gamepadManager.isDiscoveringFlow.value) {
                    Log.i(TAG_MAIN_ACTIVITY, "Botón 'Detener Búsqueda' presionado.")
                    gamepadManager.stopGamepadDiscovery()
                } else {
                    Log.i(TAG_MAIN_ACTIVITY, "Botón 'Buscar/Refrescar Gamepads' presionado.")
                    proceedWithGamepadFunctionalityActivation()
                }
            }
        }
        updateGamepadControlsVisibility(false)
        textViewGamepadConnectionStatus?.text = "Gamepad: Desactivado"
    }

    /** Configura los listeners para el área de entrada de texto del chat. */
    private fun setupInputAreaInteractions() {
        binding.messageEditText.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus) {
                viewModel.userInputInteracted(binding.messageEditText.text.toString())
            }
        }
        binding.messageEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                if (!isUpdatingEditTextProgrammatically) {
                    viewModel.userInputInteracted(s.toString())
                }
            }
            override fun afterTextChanged(s: Editable?) {}
        })
    }

    /** Configura los listeners para los widgets de configuración general. */
    private fun setupGeneralConfigClickListeners() {
        audioSourceSwitch?.setOnCheckedChangeListener { switchView, isChecked ->
            if (switchView.isPressed) viewModel.onAudioSourceSwitchChanged(isChecked)
        }
        chatHistorySwitch?.setOnCheckedChangeListener { switchView, isChecked ->
            if (switchView.isPressed) viewModel.onChatHistoryVisibilityChanged(isChecked)
        }
        aiPersonalitySpinner?.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var userInitiatedSelection = false
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (userInitiatedSelection || view != null) {
                    (parent?.getItemAtPosition(position) as? String)?.let { viewModel.onAiPersonalitySelected(it) }
                }
                userInitiatedSelection = true
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        aiModelSpinner?.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            private var userInitiatedSelection = false
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (userInitiatedSelection || view != null) {
                    (parent?.getItemAtPosition(position) as? String)?.let { viewModel.onAiModelSelected(it) }
                }
                userInitiatedSelection = true
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        binding.sendButton.setOnClickListener {
            val messageText = binding.messageEditText.text.toString().trim()
            if (messageText.isNotEmpty()) {
                viewModel.sendUserChatInput(messageText)
                hideKeyboard()
            }
        }
    }

    /** Configura la gestión del botón "Atrás" para cerrar primero el panel de navegación. */
    private fun setupOnBackPressedHandling() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.drawerLayout.isDrawerOpen(GravityCompat.START)) {
                    binding.drawerLayout.closeDrawer(GravityCompat.START)
                } else {
                    if (isEnabled) {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                    }
                }
            }
        })
    }
    //endregion

    //region Observers and UI Updates
    /** Inicia la observación de los flujos de estado del `GamepadManager` para actualizar la UI. */
    private fun observeGamepadManagerEvents() {
        Log.d(TAG_MAIN_ACTIVITY, "observeGamepadManagerEvents: Configurando observadores.")
        lifecycleScope.launch {
            gamepadManager.availableGamepadsFlow.collectLatest { gamepads ->
                Log.i(TAG_MAIN_ACTIVITY, "Observador: Lista de gamepads para UI: ${gamepads.size}")
                gamepadListAdapter.submitList(gamepads.toList())
                if (switchEnableGamepadControl?.isChecked == true) {
                    updateGamepadControlsVisibility(true)
                }
            }
        }

        lifecycleScope.launch {
            gamepadManager.isDiscoveringFlow.collectLatest { isScanning ->
                Log.i(TAG_MAIN_ACTIVITY, "Observador: Estado de descubrimiento: $isScanning")
                if (switchEnableGamepadControl?.isChecked == true) {
                    updateGamepadControlsVisibility(true)
                }
            }
        }

        lifecycleScope.launch {
            gamepadManager.activeGamepadFlow.collectLatest { gamepadInfo ->
                Log.i(TAG_MAIN_ACTIVITY, "Observador: activeGamepadFlow emitió: $gamepadInfo")
                val statusText: String
                if (gamepadInfo != null) {
                    val mappedStatus = if (gamepadInfo.inputDeviceId != -1) "(Activo)" else "(Conectando...)"
                    statusText = "Gamepad: ${gamepadInfo.name ?: gamepadInfo.address} $mappedStatus"
                } else {
                    statusText = if (switchEnableGamepadControl?.isChecked == true) {
                        if (gamepadManager.isDiscoveringFlow.value) "Gamepad: Buscando..."
                        else if (gamepadListAdapter.itemCount > 0) "Gamepad: Selecciona un dispositivo"
                        else "Gamepad: Ninguno. Pulsa Buscar."
                    } else "Gamepad: Desactivado"
                }
                textViewGamepadConnectionStatus?.text = statusText
                viewModel.updateActiveGamepadInUiState(gamepadInfo)
            }
        }
    }

    /** Inicia la observación del `uiState` del `ChatViewModel` para actualizar toda la UI. */
    private fun observeChatViewModel() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.uiState.collect { state ->
                    // Actualizar lista de mensajes
                    if (state.isChatHistoryVisible) {
                        messageAdapter.submitList(state.messages.toList()) {
                            // Lógica de auto-scroll
                            val layoutManager = binding.chatRecyclerView.layoutManager as LinearLayoutManager
                            val lastVisibleItemPosition = layoutManager.findLastVisibleItemPosition()
                            val itemCount = messageAdapter.itemCount
                            if (itemCount > 0 && (lastVisibleItemPosition == RecyclerView.NO_POSITION || lastVisibleItemPosition >= itemCount - 2 || state.messages.lastOrNull()?.sender == ChatViewModel.SENDER_USER)) {
                                binding.chatRecyclerView.scrollToPosition(itemCount - 1)
                            }
                        }
                    } else {
                        messageAdapter.submitList(emptyList())
                    }
                    // Actualizar el campo de texto con la preview del STT
                    val currentTextInEditText = binding.messageEditText.text.toString()
                    val newTextToShowFromState = state.sttPreviewText ?: ""
                    if (currentTextInEditText != newTextToShowFromState) {
                        isUpdatingEditTextProgrammatically = true
                        binding.messageEditText.setText(newTextToShowFromState)
                        if (newTextToShowFromState.isNotEmpty()) {
                            binding.messageEditText.setSelection(newTextToShowFromState.length)
                        }
                        isUpdatingEditTextProgrammatically = false
                    }
                    // Actualizar todos los demás componentes
                    updateUiComponents(state)
                    // Mostrar mensajes transitorios
                    state.transientUserMessage?.getContentIfNotHandled()?.let { message ->
                        Snackbar.make(binding.root, message, Snackbar.LENGTH_SHORT).show()
                    }
                }
            }
        }
    }

    /**
     * Sincroniza el estado de todos los componentes de la UI con el objeto `ChatUiState` actual.
     * Esta función es el núcleo de la actualización de la vista.
     * @param state El estado más reciente y completo de la UI.
     */
    private fun updateUiComponents(state: ChatUiState) {
        binding.messageEditText.isEnabled = state.inputEnabled
        binding.sendButton.isEnabled = state.inputEnabled
        supportActionBar?.title = when (state.connectionStatus) {
            ConnectionStatus.CONNECTED -> state.serviceName ?: "Conectado"
            ConnectionStatus.CONNECTING -> "Conectando..."
            ConnectionStatus.DISCOVERING -> "Buscando Servicio..."
            ConnectionStatus.RESOLVED -> "Servicio Encontrado (${state.serviceName ?: ""})..."
            ConnectionStatus.SERVICE_FOUND -> "Resolviendo Servicio..."
            ConnectionStatus.RECONNECTING -> "Reconectando..."
            ConnectionStatus.DISCONNECTED -> "Desconectado"
            ConnectionStatus.IDLE -> "Chat Umebot"
        }
        audioSourceSwitch?.apply {
            isEnabled = state.connectionStatus == ConnectionStatus.CONNECTED && !state.isAudioConfigPending
            state.currentAudioSourceIsRobot?.let { if (isChecked != it) isChecked = it }
            text = when (state.currentAudioSourceIsRobot) {
                true -> "Audio: Robot"; false -> "Audio: Local"; null -> "Audio: Desconocido"
            }
        }
        chatHistorySwitch?.apply {
            if (isChecked != state.isChatHistoryVisible) isChecked = state.isChatHistoryVisible
            text = if (state.isChatHistoryVisible) "Ocultar Historial" else "Mostrar Historial"
        }
        val isAiConfigEnabled = state.connectionStatus == ConnectionStatus.CONNECTED && !state.isAiConfigPending
        aiPersonalitySpinner?.apply {
            isEnabled = isAiConfigEnabled
            val currentAdapterData = (0 until personalityAdapter.count).mapNotNull { personalityAdapter.getItem(it) }
            if (state.availableAiPersonalities != currentAdapterData) {
                val currentSelection = selectedItem as? String
                personalityAdapter.clear()
                personalityAdapter.addAll(state.availableAiPersonalities)
                currentSelection?.let { sel -> personalityAdapter.getPosition(sel).takeIf { it >= 0 }?.let { setSelection(it, false) } }
            }
            state.currentAiPersonality?.let { personality ->
                val position = personalityAdapter.getPosition(personality)
                if (position >= 0 && selectedItemPosition != position) setSelection(position, false)
            }
        }
        aiModelSpinner?.apply {
            isEnabled = isAiConfigEnabled
            val currentAdapterData = (0 until modelAdapter.count).mapNotNull { modelAdapter.getItem(it) }
            if (state.availableAiBackends != currentAdapterData) {
                val currentSelection = selectedItem as? String
                modelAdapter.clear()
                modelAdapter.addAll(state.availableAiBackends)
                currentSelection?.let { sel -> modelAdapter.getPosition(sel).takeIf { it >= 0 }?.let { setSelection(it, false) } }
            }
            state.currentAiModelBackend?.let { model ->
                val position = modelAdapter.getPosition(model)
                if (position >= 0 && selectedItemPosition != position) setSelection(position, false)
            }
        }
    }
    //endregion

    //region Gamepad Logic Helpers
    /** Lanza el flujo de activación del gamepad, que incluye la solicitud de permisos y la activación de Bluetooth si es necesario. */
    private fun proceedWithGamepadFunctionalityActivation() {
        Log.d(TAG_MAIN_ACTIVITY, "proceedWithGamepadFunctionalityActivation: Iniciando proceso...")
        if (checkAndRequestBluetoothPermissions()) {
            if (!gamepadManager.connectionManager.isBluetoothEnabled()) {
                promptEnableBluetooth()
            } else {
                Log.d(TAG_MAIN_ACTIVITY, "Permisos y Bluetooth OK. Iniciando escaneo de gamepads.")
                gamepadManager.startGamepadDiscovery()
                updateGamepadControlsVisibility(true)
            }
        } else {
            Log.w(TAG_MAIN_ACTIVITY, "Permisos de Bluetooth no concedidos aún o solicitud en curso.")
        }
    }

    /** Actualiza la visibilidad de los controles del gamepad en el panel de navegación. */
    private fun updateGamepadControlsVisibility(gamepadFunctionalityEnabled: Boolean) {
        val hasDevicesInList = gamepadListAdapter.itemCount > 0
        val isCurrentlyDiscovering = gamepadManager.isDiscoveringFlow.value
        Log.d(TAG_MAIN_ACTIVITY, "updateGamepadControlsVisibility: enabled=$gamepadFunctionalityEnabled, hasDevices=$hasDevicesInList, discovering=$isCurrentlyDiscovering")
        buttonRefreshGamepadDevices?.visibility = if (gamepadFunctionalityEnabled) View.VISIBLE else View.GONE
        if (gamepadFunctionalityEnabled) {
            buttonRefreshGamepadDevices?.text = if (isCurrentlyDiscovering) "Detener Búsqueda" else "Buscar/Refrescar"
        }
        recyclerviewGamepadList?.visibility = if (gamepadFunctionalityEnabled && (hasDevicesInList || isCurrentlyDiscovering)) View.VISIBLE else View.GONE
    }

    /**
     * Verifica si los permisos de Bluetooth están concedidos. Si no, los solicita.
     * Maneja las diferencias de permisos entre versiones de Android (antes y después de Android S/12).
     * @return `true` si los permisos ya están concedidos, `false` si se necesita solicitarlos.
     */
    private fun checkAndRequestBluetoothPermissions(): Boolean {
        val requiredPermissions = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                requiredPermissions.add(Manifest.permission.BLUETOOTH_CONNECT)
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED) {
                requiredPermissions.add(Manifest.permission.BLUETOOTH_SCAN)
            }
        } else {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH) != PackageManager.PERMISSION_GRANTED) {
                requiredPermissions.add(Manifest.permission.BLUETOOTH)
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_ADMIN) != PackageManager.PERMISSION_GRANTED) {
                requiredPermissions.add(Manifest.permission.BLUETOOTH_ADMIN)
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
                requiredPermissions.add(Manifest.permission.ACCESS_FINE_LOCATION)
            }
        }
        return if (requiredPermissions.isEmpty()) {
            Log.d(TAG_MAIN_ACTIVITY, "Todos los permisos necesarios para gamepad están concedidos.")
            true
        } else {
            Log.i(TAG_MAIN_ACTIVITY, "Solicitando permisos para gamepad: ${requiredPermissions.joinToString()}")
            requestBluetoothPermissionsLauncher.launch(requiredPermissions.toTypedArray())
            false
        }
    }

    /**
     * Muestra al usuario un diálogo del sistema para activar Bluetooth si está desactivado.
     * Primero verifica que se tenga el permiso `BLUETOOTH_CONNECT` en versiones de Android S y superiores.
     */
    @SuppressLint("MissingPermission")
    private fun promptEnableBluetooth() {
        if (!gamepadManager.connectionManager.isBluetoothEnabled()) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                Log.w(TAG_MAIN_ACTIVITY, "Falta BLUETOOTH_CONNECT para solicitar activación de BT.")
                Snackbar.make(binding.root, "Se requiere permiso BLUETOOTH_CONNECT para activar Bluetooth.", Snackbar.LENGTH_LONG).show()
                switchEnableGamepadControl?.isChecked = false
                return
            }
            Log.d(TAG_MAIN_ACTIVITY, "Solicitando al usuario que active Bluetooth.")
            val enableBtIntent = Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
            requestBluetoothEnableLauncher.launch(enableBtIntent)
        } else {
            Log.d(TAG_MAIN_ACTIVITY, "Bluetooth ya está habilitado.")
            if (switchEnableGamepadControl?.isChecked == true) {
                proceedWithGamepadFunctionalityActivation()
            }
        }
    }
    //endregion

    //region Utility and Lifecycle Overrides
    /** Oculta el teclado en pantalla. */
    private fun hideKeyboard() {
        val inputMethodManager = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        val view = currentFocus ?: View(this)
        inputMethodManager.hideSoftInputFromWindow(view.windowToken, 0)
    }

    override fun onPostCreate(savedInstanceState: Bundle?) {
        super.onPostCreate(savedInstanceState)
        drawerToggle.syncState()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        drawerToggle.onConfigurationChanged(newConfig)
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (drawerToggle.onOptionsItemSelected(item)) {
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    override fun onDestroy() {
        Log.d(TAG_MAIN_ACTIVITY, "onDestroy - Limpiando GamepadManager")
        gamepadManager.cleanup()
        super.onDestroy()
    }
    //endregion
}
