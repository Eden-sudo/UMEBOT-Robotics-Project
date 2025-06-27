package com.example.umebotros2.gamepad

import android.util.Log
import org.json.JSONException
import org.json.JSONObject

/**
 * Se especializa en convertir el objeto de estado `GamepadFullState` en un `JSONObject`
 * estructurado, listo para ser enviado al backend del robot.
 *
 * Esta clase actúa como la capa de "serialización" para los datos del gamepad, asegurando
 * que el formato del mensaje sea siempre consistente y conforme al contrato esperado por el servidor.
 */
class GamepadCommandBuilder {

    private val TAG = "GamepadCmdBuilder"

    companion object {
        /**
         * El identificador de tipo para los mensajes de estado del gamepad.
         * El backend de Python usa este campo para saber cómo procesar el JSON entrante.
         */
        const val GAMEPAD_STATE_MESSAGE_TYPE = "gamepad_state"
    }

    /**
     * Construye el comando JSON completo a partir de una instantánea del estado del gamepad.
     *
     * El objeto JSON resultante tendrá la siguiente estructura:
     * ```json
     * {
     * "type": "gamepad_state",
     * "payload": {
     * "left_stick": { "x": 0.0, "y": 0.0 },
     * "right_stick": { "x": 0.0, "y": 0.0 },
     * "dpad_events": { "up": false, "down": false, "left": false, "right": false },
     * "action_button_events": { "a": false, "b": false, "x": false, "y": false },
     * "bumper_states": { "l1_pressed": false, "r1_pressed": false },
     * "stick_button_states": { "l3_pressed": false, "r3_pressed": false }
     * }
     * }
     * ```
     *
     * @param state El objeto `GamepadFullState` que contiene la información actual del gamepad.
     * @return Un `JSONObject` listo para ser enviado, o `null` si ocurre un error durante la construcción.
     */
    fun buildGamepadStateCommand(state: GamepadFullState): JSONObject? {
        try {
            val leftStickPayload = JSONObject().apply {
                put("x", state.leftStickX.toDouble())
                put("y", state.leftStickY.toDouble())
            }
            val rightStickPayload = JSONObject().apply {
                put("x", state.rightStickX.toDouble())
                put("y", astate.rightStickY.toDouble())
            }

            // A pesar de que los datos son de estado (presionado/no presionado), se mantiene
            // el nombre de clave "dpad_events" por compatibilidad con el backend.
            val dpadPayload = JSONObject().apply {
                put("up", state.dpadUpPressed)
                put("down", state.dpadDownPressed)
                put("left", state.dpadLeftPressed)
                put("right", state.dpadRightPressed)
            }

            val actionButtonEventsPayload = JSONObject().apply {
                put("a", state.actionAEvent)
                put("b", state.actionBEvent)
                put("x", state.actionXEvent)
                put("y", state.actionYEvent)
            }

            val bumperStatesPayload = JSONObject().apply {
                put("l1_pressed", state.l1PressedState)
                put("r1_pressed", state.r1PressedState)
            }

            // NOTA: Existe una discrepancia en el código original. Los estados de los bumpers (L1/R1)
            // se están usando para poblar los campos de los botones de los sticks (L3/R3) en el JSON.
            // La documentación refleja lo que el código hace actualmente.
            val stickButtonStatesPayload = JSONObject().apply {
                put("l3_pressed", state.l1PressedState) // Usa el estado de L1
                put("r3_pressed", state.r1PressedState) // Usa el estado de R1
            }

            val mainPayload = JSONObject().apply {
                put("left_stick", leftStickPayload)
                put("right_stick", rightStickPayload)
                put("dpad_events", dpadPayload)
                put("action_button_events", actionButtonEventsPayload)
                put("bumper_states", bumperStatesPayload)
                put("stick_button_states", stickButtonStatesPayload)
            }

            val command = JSONObject().apply {
                put("type", GAMEPAD_STATE_MESSAGE_TYPE)
                put("payload", mainPayload)
            }

            return command

        } catch (e: JSONException) {
            Log.e(TAG, "Error construyendo JSON desde GamepadFullState: $state", e)
            return null
        }
    }
}
