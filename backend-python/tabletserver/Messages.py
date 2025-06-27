# ----------------------------------------------------------------------------------
# Titular: Definicion del Protocolo de Mensajes (Contrato de Datos)
# Funcion Principal: Este modulo define el "contrato de datos" o protocolo de
#                    comunicacion entre el servidor (backend) y los clientes
#                    (frontend/tablet). Centraliza la creacion y validacion de todos
#                    los mensajes JSON que se intercambian.
#                    Incluye:
#                    - Constantes para los tipos de mensaje.
#                    - Funciones 'craft_*' para construir mensajes JSON estandarizados
#                      que el servidor envia al cliente.
#                    - Una funcion 'deserialize_client_message' para parsear y
#                      validar rigurosamente los mensajes recibidos del cliente.
# ----------------------------------------------------------------------------------

import json
import datetime
from typing import Dict, Any, Literal, Union, Optional, List

# --- Constantes para los Tipos de Mensaje ---
# Usar constantes evita errores por "magic strings" (escribir strings directamente).
MSG_TYPE_INPUT = "input"                           # Mensaje de entrada del usuario (ej. texto de chat)
MSG_TYPE_OUTPUT = "output"                         # Mensaje de salida del robot/IA
MSG_TYPE_SYSTEM = "system"                         # Mensaje del sistema (info, warning, error)
MSG_TYPE_CONFIG = "config"                         # Mensaje del cliente para cambiar una configuracion
MSG_TYPE_CURRENT_CONFIGURATION = "currentConfiguration" # Mensaje del servidor con la configuracion actual
MSG_TYPE_CONFIG_CONFIRMATION = "config_confirmation" # Mensaje del servidor confirmando un cambio de config
MSG_TYPE_PARTIAL_STT_RESULT = "partial_stt_result" # Mensaje con resultados parciales de STT
MSG_TYPE_GAMEPAD_STATE = "gamepad_state"           # Mensaje con el estado completo del gamepad

# --- Funciones Auxiliares ---

# Funcion auxiliar interna para generar una marca de tiempo estandarizada
# en formato ISO 8601 con zona horaria UTC.
def _get_current_timestamp_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds') + 'Z'

# --- Funciones para Construir Mensajes (Servidor -> Cliente) ---

# Crea un mensaje para hacer eco de una entrada de texto del usuario,
# indicando su origen (ej. 'stt', 'gui_manual').
def craft_input_echo_message(text: str, source: Literal["gui", "stt", "stt_auto", "gui_manual", "unknown"]) -> str:
    message = {
        "type": MSG_TYPE_INPUT,
        "timestamp": _get_current_timestamp_utc(),
        "payload": {"text": text, "source": source}
    }
    return json.dumps(message)

# Crea un mensaje con una respuesta de un agente (ej. Umebot, sistema)
# para ser mostrada en la interfaz de chat.
def craft_output_message(sender_name: str, text: str, original_input_source: Literal["gui", "stt", "stt_auto", "gui_manual", "unknown"] = "unknown") -> str:
    message = {
        "type": MSG_TYPE_OUTPUT,
        "timestamp": _get_current_timestamp_utc(),
        "payload": {"sender": sender_name, "text": text, "original_input_source": original_input_source}
    }
    return json.dumps(message)

# Crea un mensaje de sistema para notificar al cliente sobre eventos
# informativos, advertencias o errores.
def craft_system_message(
    sender_name: str,
    level: Literal["info", "warning", "error"],
    text: str,
    detail: Optional[Dict[str, Any]] = None
) -> str:
    payload_content = {"sender": sender_name, "level": level, "text": text}
    if detail is not None:
        payload_content["detail"] = detail
    message = {
        "type": MSG_TYPE_SYSTEM,
        "timestamp": _get_current_timestamp_utc(),
        "payload": payload_content
    }
    return json.dumps(message)

# Crea un mensaje para enviar el estado actual de la configuracion del
# backend al cliente, usualmente al conectar o solicitarlo.
def craft_current_configuration_message(settings: Dict[str, Any]) -> str:
    message = {
        "type": MSG_TYPE_CURRENT_CONFIGURATION,
        "timestamp": _get_current_timestamp_utc(),
        "payload": {
            "settings": settings
        }
    }
    return json.dumps(message)

# Crea un mensaje para confirmar al cliente si un cambio de configuracion
# fue exitoso, devolviendo el valor actual del item configurado.
def craft_config_confirmation_message(
    config_item: str,
    success: bool,
    current_value: Any,
    message_to_display: str
) -> str:
    message = {
        "type": MSG_TYPE_CONFIG_CONFIRMATION,
        "timestamp": _get_current_timestamp_utc(),
        "payload": {
            "config_item": config_item,
            "success": success,
            "current_value": current_value,
            "message_to_display": message_to_display
        }
    }
    return json.dumps(message)

# Crea un mensaje para enviar resultados parciales o finales de STT a la UI,
# permitiendo una retroalimentacion en tiempo real mientras el usuario habla.
def craft_partial_stt_result_message(partial_text: str, is_final: bool) -> str:
    message = {
        "type": MSG_TYPE_PARTIAL_STT_RESULT,
        "timestamp": _get_current_timestamp_utc(),
        "payload": {"text": partial_text, "is_final": is_final}
    }
    return json.dumps(message)


# --- Funciones Auxiliares para Preparar Payloads (Uso en Cliente Python de Prueba) ---
# NOTA: Estas funciones son de conveniencia, principalmente para pruebas con un
# cliente Python. El cliente principal (Android) construye sus propios JSONs.
def prepare_client_input_payload(text: str, source: Optional[str] = None) -> Dict[str, Any]:
    payload_data = {"text": text}
    if source:
        payload_data["source"] = source
    return {
        "type": MSG_TYPE_INPUT,
        "payload": payload_data
    }

def prepare_client_config_payload(config_item: str, value: Any) -> Dict[str, Any]:
    return {
        "type": MSG_TYPE_CONFIG,
        "payload": {
            "config_item": config_item,
            "value": value
        }
    }

# --- Funciones para Validar Mensajes (Cliente -> Servidor) ---

# Deserializa y valida un mensaje JSON recibido del cliente.
# Verifica que la estructura sea correcta, que contenga las claves 'type' y
# 'payload', y que el contenido del payload sea valido para el tipo de mensaje
# especifico (ej. validacion detallada para mensajes de gamepad).
#
# Args:
#   json_string (str): La cadena JSON recibida del cliente.
#
# Returns:
#   Dict[str, Any]: El mensaje deserializado como un diccionario Python.
#
# Raises:
#   ValueError: Si el string no es un JSON valido o si el mensaje no
#               cumple con la estructura esperada.
def deserialize_client_message(json_string: str) -> Dict[str, Any]:
    try:
        message = json.loads(json_string) # Intenta parsear el string JSON
    except json.JSONDecodeError as e:
        raise ValueError(f"El mensaje recibido no es un JSON valido: {e}")

    if not isinstance(message, dict):
        raise ValueError("El mensaje JSON debe ser un objeto (diccionario).")

    msg_type = message.get("type")
    payload = message.get("payload")

    if not msg_type or not isinstance(msg_type, str):
        raise ValueError("El mensaje JSON es invalido: la clave 'type' es faltante o no es un string.")

    # La validacion especifica del payload depende del tipo de mensaje.
    # Se asegura que el payload sea un diccionario para los tipos que lo requieren.
    if msg_type in [MSG_TYPE_INPUT, MSG_TYPE_CONFIG, MSG_TYPE_GAMEPAD_STATE]:
        if payload is None: # Primero se verifica que el payload no sea None.
            raise ValueError(f"El mensaje JSON es invalido: 'payload' es faltante para el tipo '{msg_type}'.")
        if not isinstance(payload, dict): # Si no es None, se verifica que sea un diccionario.
            raise ValueError(f"El mensaje JSON es invalido: 'payload' no es un objeto (diccionario) para el tipo '{msg_type}'.")

    # --- Funciones Auxiliares para la Validacion del Payload de GAMEPAD_STATE ---
    def _is_valid_joystick_object(stick_obj: Any, stick_name: str) -> bool:
        # Valida que un objeto de joystick dentro del payload tenga la estructura y tipos correctos.
        if not isinstance(stick_obj, dict):
            raise ValueError(f"'{stick_name}' debe ser un objeto (diccionario).")
        if not all(key in stick_obj for key in ["x", "y"]):
            raise ValueError(f"'{stick_name}' debe contener las claves 'x' e 'y'.")
        if not isinstance(stick_obj["x"], (int, float)) or not isinstance(stick_obj["y"], (int, float)):
            raise ValueError(f"Los valores 'x' e 'y' de '{stick_name}' deben ser numericos.")
        # Opcional: Se podria validar aqui que los valores esten en el rango [-1.0, 1.0].
        return True

    def _is_valid_button_object(button_obj: Any, button_group_name: str, expected_keys: List[str]) -> bool:
        # Valida que un objeto de grupo de botones dentro del payload tenga la estructura y tipos correctos.
        if not isinstance(button_obj, dict):
            raise ValueError(f"'{button_group_name}' debe ser un objeto (diccionario).")
        if not all(key in button_obj for key in expected_keys):
            missing_keys = [k for k in expected_keys if k not in button_obj]
            raise ValueError(f"A '{button_group_name}' le faltan las claves: {missing_keys}.")
        if not all(isinstance(button_obj[key], bool) for key in expected_keys):
            non_bool_keys = [k for k in expected_keys if not isinstance(button_obj[k], bool)]
            raise ValueError(f"Los valores en '{button_group_name}' deben ser booleanos. Claves no booleanas: {non_bool_keys}.")
        return True
    # --- Fin de Funciones Auxiliares de Validacion ---

    # Validaciones especificas por tipo de mensaje
    if msg_type == MSG_TYPE_INPUT:
        if "text" not in payload or not isinstance(payload.get("text"), str):
            raise ValueError(f"Mensaje '{MSG_TYPE_INPUT}' invalido: 'payload.text' es faltante o no es un string.")
    elif msg_type == MSG_TYPE_CONFIG:
        if "config_item" not in payload or not isinstance(payload.get("config_item"), str):
            raise ValueError(f"Mensaje '{MSG_TYPE_CONFIG}' invalido: 'payload.config_item' es faltante o no es un string.")
        if "value" not in payload:
            raise ValueError(f"Mensaje '{MSG_TYPE_CONFIG}' invalido: 'payload.value' es faltante.")

    elif msg_type == MSG_TYPE_GAMEPAD_STATE: # Validacion detallada para el estado del gamepad
        required_top_level_keys = ["left_stick", "right_stick", "dpad_events", "action_button_events", "stick_button_states"]
        for key in required_top_level_keys:
            if key not in payload:
                raise ValueError(f"Mensaje '{MSG_TYPE_GAMEPAD_STATE}' invalido: 'payload.{key}' es faltante.")
        try:
            _is_valid_joystick_object(payload.get("left_stick"), "payload.left_stick")
            _is_valid_joystick_object(payload.get("right_stick"), "payload.right_stick")

            dpad_event_keys = ["up", "down", "left", "right"]
            _is_valid_button_object(payload.get("dpad_events"), "payload.dpad_events", dpad_event_keys)

            action_button_keys = ["a", "b", "x", "y"]
            _is_valid_button_object(payload.get("action_button_events"), "payload.action_button_events", action_button_keys)

            stick_button_keys = ["l3_pressed", "r3_pressed"]
            _is_valid_button_object(payload.get("stick_button_states"), "payload.stick_button_states", stick_button_keys)
        except ValueError as e_val: # Captura y re-lanza los errores de validacion de las funciones auxiliares con mas contexto.
            raise ValueError(f"El payload del mensaje '{MSG_TYPE_GAMEPAD_STATE}' es invalido: {e_val}")

    # No se validan otros tipos de mensajes que el servidor no espera procesar desde el cliente.
    return message
