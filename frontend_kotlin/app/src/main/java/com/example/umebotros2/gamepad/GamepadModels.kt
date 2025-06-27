package com.example.umebotros2.gamepad

/**
 * Representa la información de un único dispositivo gamepad, tanto si está
 * conectado, emparejado o simplemente descubierto en la red.
 *
 * Sirve como el modelo de datos para las listas de dispositivos en la UI y para
 * rastrear el estado de conexión del gamepad activo.
 *
 * @property address La dirección MAC del dispositivo. Actúa como su identificador único.
 * @property name El nombre legible por humanos del dispositivo (ej. "Xbox Wireless Controller"). Puede ser nulo.
 * @property inputDeviceId El ID que el sistema Android (`InputManager`) asigna al dispositivo una vez
 * que está completamente conectado y listo para enviar eventos. Un valor de -1 indica que
 * el dispositivo no está mapeado o no está disponible para la lectura de entradas.
 * @property isLikelyGamepad Un indicador heurístico para ayudar a la UI a priorizar o
 * destacar dispositivos que probablemente son gamepads.
 */
data class GamepadInfo(
    val address: String,
    val name: String?,
    val inputDeviceId: Int = -1,
    val isLikelyGamepad: Boolean = false
)

/**
 * Modela una "instantánea" completa del estado de todos los controles del gamepad en un momento dado.
 *
 * Esta clase distingue entre dos tipos de entrada:
 * - **Estado continuo (`...State`, `...Pressed`):** Refleja si un control (stick, D-Pad, bumper)
 * está siendo presionado *en este preciso instante*.
 * - **Evento de acción (`...Event`):** Es `true` solo para un único ciclo de lectura después de que
 * se presiona un botón de acción (A, B, X, Y), implementando un patrón de "evento consumible".
 */
data class GamepadFullState(
    // Ejes de Joysticks (-1.0 a 1.0)
    val leftStickX: Float = 0.0f,
    val leftStickY: Float = 0.0f,
    val rightStickX: Float = 0.0f,
    val rightStickY: Float = 0.0f,

    // Estado del D-Pad (leído como un estado continuo desde el Hat Switch)
    val dpadUpPressed: Boolean = false,
    val dpadDownPressed: Boolean = false,
    val dpadLeftPressed: Boolean = false,
    val dpadRightPressed: Boolean = false,

    // Eventos de botones de acción (pulsación única)
    val actionAEvent: Boolean = false,
    val actionBEvent: Boolean = false,
    val actionXEvent: Boolean = false,
    val actionYEvent: Boolean = false,

    // Estado de los botones de los sticks (presión física)
    val l3PressedState: Boolean = false, // Pulsación joystick izquierdo
    val r3PressedState: Boolean = false, // Pulsación joystick derecho

    // Estado de los botones superiores (bumpers/shoulders)
    val l1PressedState: Boolean = false, // L1/LB
    val r1PressedState: Boolean = false  // R1/RB
)

/**
 * (Clase obsoleta, mantenida por referencia)
 * Enumeración interna que originalmente mapeaba nombres simbólicos a los botones.
 * Su relevancia ha disminuido ya que la lógica principal se basa en los `KeyCode`
 * y los ejes del `MotionEvent` directamente en el `GamepadInputInterpreter`.
 */
internal enum class GamepadKey {
    DPAD_UP,
    DPAD_DOWN,
    DPAD_LEFT,
    DPAD_RIGHT,
    BUTTON_A,
    BUTTON_B,
    BUTTON_X,
    BUTTON_Y,
    BUTTON_L1,
    BUTTON_R1,
    BUTTON_L3,
    BUTTON_R3
}
