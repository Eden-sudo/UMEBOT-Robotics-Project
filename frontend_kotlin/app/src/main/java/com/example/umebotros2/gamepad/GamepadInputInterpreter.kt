package com.example.umebotros2.gamepad

import android.util.Log
import android.view.KeyEvent
import android.view.MotionEvent
import kotlin.math.abs

/**
 * Traduce los eventos de bajo nivel de Android (`KeyEvent`, `MotionEvent`) en un estado de gamepad
 * estructurado y fácil de consumir (`GamepadFullState`).
 *
 * Esta clase actúa como un "parser" para el input del gamepad, resolviendo problemas comunes como:
 * - El "ruido" o "drift" de los sticks analógicos mediante una zona muerta (dead zone).
 * - La diferencia entre una pulsación continua (ej. mantener presionado L1) y un evento de acción
 * único (ej. presionar 'A' una vez para una acción).
 * - La unificación de diferentes fuentes de eventos (botones, ejes, D-Pad/hat) en un único objeto de estado.
 *
 * Está diseñada para ser thread-safe, permitiendo que los eventos de entrada se procesen desde el hilo
 * principal de Android mientras el estado se consume desde un bucle de juego o de control en otro hilo.
 *
 * @param analogDeadZone El umbral por debajo del cual los valores de los ejes analógicos se consideran cero.
 * Ayuda a prevenir movimientos no deseados por la imprecisión del hardware.
 */
class GamepadInputInterpreter(private val analogDeadZone: Float = 0.2f) {

    private val TAG = "GamepadInterpreter"
    private val lock = Any()

    // Estados continuos de los sticks analógicos
    private var currentLeftStickX: Float = 0f
    private var currentLeftStickY: Float = 0f
    private var currentRightStickX: Float = 0f
    private var currentRightStickY: Float = 0f

    // Mapa para el estado "mantenido presionado" de botones (L/R, thumbsticks).
    private val buttonCurrentlyPressedMap = mutableMapOf<Int, Boolean>()
    // Conjunto para eventos de "pulsación única". Se limpia después de ser leído.
    private val buttonActionEventSet = mutableSetOf<Int>()

    // Estado del D-Pad (Hat Switch).
    private var currentHatX: Float = 0f
    private var currentHatY: Float = 0f

    /**
     * Procesa un evento de teclado/botón del gamepad.
     * Actualiza el estado interno de los botones, distinguiendo entre eventos de acción única
     * y botones de estado continuo.
     *
     * @param event El `KeyEvent` crudo recibido del sistema Android.
     */
    fun processKeyEvent(event: KeyEvent) {
        synchronized(lock) {
            val keyCode = event.keyCode
            val isPressed = event.action == KeyEvent.ACTION_DOWN
            val previouslyPressed = buttonCurrentlyPressedMap[keyCode] ?: false
            buttonCurrentlyPressedMap[keyCode] = isPressed

            // Se registra como evento de acción única solo en el flanco de subida (cuando se presiona por primera vez).
            if (isPressed && !previouslyPressed) {
                when (keyCode) {
                    KeyEvent.KEYCODE_BUTTON_A,
                    KeyEvent.KEYCODE_BUTTON_B,
                    KeyEvent.KEYCODE_BUTTON_X,
                    KeyEvent.KEYCODE_BUTTON_Y -> {
                        buttonActionEventSet.add(keyCode)
                    }
                }
            }
        }
    }

    /**
     * Procesa un evento de movimiento del gamepad.
     * Extrae y actualiza los valores de los sticks analógicos y del D-Pad (hat switch),
     * aplicando la zona muerta a los sticks.
     *
     * @param event El `MotionEvent` crudo recibido del sistema Android.
     */
    fun processMotionEvent(event: MotionEvent) {
        synchronized(lock) {
            if (event.action == MotionEvent.ACTION_MOVE) {
                currentLeftStickX = applyDeadZone(event.getAxisValue(MotionEvent.AXIS_X))
                currentLeftStickY = applyDeadZone(event.getAxisValue(MotionEvent.AXIS_Y))
                currentRightStickX = applyDeadZone(event.getAxisValue(MotionEvent.AXIS_Z))
                currentRightStickY = applyDeadZone(event.getAxisValue(MotionEvent.AXIS_RZ))

                currentHatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
                currentHatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
            }
        }
    }

    /**
     * Captura una "instantánea" del estado actual de todos los controles del gamepad.
     *
     * Este método tiene un efecto secundario crucial: **resetea los eventos de pulsación única**
     * (los de los botones A, B, X, Y) después de leerlos. Esto asegura que cada pulsación
     * genere un solo evento. Está diseñado para ser llamado en un bucle regular.
     *
     * @return Un objeto `GamepadFullState` que representa el estado completo del gamepad en este instante.
     */
    fun getCurrentStateAndResetEvents(): GamepadFullState {
        synchronized(lock) {
            val l1Pressed = buttonCurrentlyPressedMap[KeyEvent.KEYCODE_BUTTON_L1] ?: false
            val r1Pressed = buttonCurrentlyPressedMap[KeyEvent.KEYCODE_BUTTON_R1] ?: false
            val l3PhysicallyPressed = buttonCurrentlyPressedMap[KeyEvent.KEYCODE_BUTTON_THUMBL] ?: false
            val r3PhysicallyPressed = buttonCurrentlyPressedMap[KeyEvent.KEYCODE_BUTTON_THUMBR] ?: false

            val actionA = buttonActionEventSet.contains(KeyEvent.KEYCODE_BUTTON_A)
            val actionB = buttonActionEventSet.contains(KeyEvent.KEYCODE_BUTTON_B)
            val actionX = buttonActionEventSet.contains(KeyEvent.KEYCODE_BUTTON_X)
            val actionY = buttonActionEventSet.contains(KeyEvent.KEYCODE_BUTTON_Y)

            // Interpretar el estado del D-Pad a partir de los valores del hat switch.
            val dpadUp = currentHatY < -0.5f
            val dpadDown = currentHatY > 0.5f
            val dpadLeft = currentHatX < -0.5f
            val dpadRight = currentHatX > 0.5f

            val state = GamepadFullState(
                leftStickX = currentLeftStickX,
                leftStickY = currentLeftStickY,
                rightStickX = currentRightStickX,
                rightStickY = currentRightStickY,
                dpadUpPressed = dpadUp,
                dpadDownPressed = dpadDown,
                dpadLeftPressed = dpadLeft,
                dpadRightPressed = dpadRight,
                actionAEvent = actionA,
                actionBEvent = actionB,
                actionXEvent = actionX,
                actionYEvent = actionY,
                l1PressedState = l1Pressed,
                r1PressedState = r1Pressed,
                l3PressedState = l3PhysicallyPressed,
                r3PressedState = r3PhysicallyPressed
            )

            // Resetear los eventos de acción única para que no se disparen de nuevo.
            if (buttonActionEventSet.isNotEmpty()) {
                buttonActionEventSet.clear()
            }

            return state
        }
    }

    /**
     * Función de utilidad para anular los pequeños valores de un eje analógico causados por
     * imprecisiones del hardware (conocido como "stick drift").
     *
     * @param value El valor crudo del eje, típicamente entre -1.0 y 1.0.
     * @return 0f si el valor está dentro de la zona muerta, o el valor original en caso contrario.
     */
    private fun applyDeadZone(value: Float): Float {
        return if (abs(value) < analogDeadZone) 0f else value
    }

    /**
     * Limpia y resetea todos los estados internos del intérprete a sus valores por defecto.
     * Es útil al desconectar o cambiar de gamepad para evitar estados residuales.
     */
    fun resetInternalStates() {
        synchronized(lock) {
            Log.i(TAG, "Reseteando estados internos del intérprete de gamepad.")
            currentLeftStickX = 0f; currentLeftStickY = 0f
            currentRightStickX = 0f; currentRightStickY = 0f
            buttonCurrentlyPressedMap.clear()
            buttonActionEventSet.clear()
            currentHatX = 0f
            currentHatY = 0f
        }
    }
}
