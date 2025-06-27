# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Tablero de Estado del Robot (Almacen de Datos en Memoria)
# Funcion Principal: Este modulo define la clase RobotStatus, un componente
#                    experimental y conceptual disenado para actuar como un 'tablero
#                    de informacion' o un almacen de datos centralizado para el estado
#                    volatil del robot en tiempo real. La idea era que este modulo
#                    recibiera todos los datos de 'Perception_Sys.py' (sensores,
#                    eventos) y los hiciera accesibles de forma segura (thread-safe)
#                    para el resto del sistema.
#                    La vision a futuro era que no solo almacenara datos crudos, sino
#                    que tambien pudiera usar un modelo de IA para analizar la situacion,
#                    crear un contexto general de los ultimos eventos y filtrar esta
#                    informacion para pasarla al modelo de IA generativo, permitiendo
#                    respuestas y comportamientos mucho mas autonomos y conscientes
#                    del entorno. Por falta de tiempo y directrices, la implementacion se centro en
#                    la base de almacenamiento y un metodo para formatear el contexto.
# ----------------------------------------------------------------------------------

import time
import threading # Para proteger el acceso al estado desde multiples hilos

# Almacena y gestiona el estado actual del robot de forma dinamica y segura
# para accesos desde multiples hilos (thread-safe).
class RobotStatus:
    # Inicializa el diccionario de estado, un diccionario para las marcas de tiempo
    # de cada actualizacion, y un Lock para garantizar la concurrencia segura.
    def __init__(self):
        self._status = {} # Diccionario interno para almacenar los datos clave-valor del estado
        self._last_update_time = {} # Diccionario para saber cuando se actualizo por ultima vez cada clave
        self._lock = threading.Lock() # Lock para evitar condiciones de carrera si multiples hilos actualizan el estado simultaneamente
        print("[RobotStatus] Instancia creada. El estado inicial esta vacio.")

    # Actualiza (o anade) un valor en el estado del robot de forma segura.
    # Utiliza un lock para proteger la operacion de escritura contra accesos concurrentes.
    #
    # Args:
    #   key (str): La clave o nombre del dato a actualizar (ej: "battery_level").
    #   value: El nuevo valor para esa clave.
    def update_status(self, key, value):
        timestamp = time.time()
        # Adquiere el bloqueo antes de modificar los diccionarios para garantizar la atomicidad de la operacion
        with self._lock:
            self._status[key] = value
            self._last_update_time[key] = timestamp
        # Descomentar la siguiente linea para depurar cada actualizacion de estado en tiempo real.
        # print(f"[{timestamp:.2f}][RobotStatus UPDATE] {key} = {value}")

    # Obtiene de forma segura el valor actual de una clave especifica del estado.
    #
    # Args:
    #   key (str): La clave del dato a obtener.
    #   default: El valor a devolver si la clave no existe (por defecto None).
    #
    # Returns:
    #   El valor asociado a la clave, o el valor 'default' si no se encuentra.
    def get_value(self, key, default=None):
        with self._lock: # Adquiere el bloqueo para una lectura segura y consistente
            return self._status.get(key, default)

    # Obtiene de forma segura el timestamp de la ultima actualizacion de una clave.
    #
    # Args:
    #   key (str): La clave del dato.
    #
    # Returns:
    #   float: El timestamp (resultado de time.time()) o None si la clave no existe.
    def get_last_update_time(self, key):
        with self._lock:
            return self._last_update_time.get(key)

    # Devuelve una copia completa del diccionario de estado actual, ideal para
    # obtener una "foto" del estado completo en un momento dado.
    #
    # Returns:
    #   dict: Una copia del diccionario de estado para evitar modificaciones externas accidentales.
    def get_full_status_dict(self):
        with self._lock:
            return self._status.copy()

    # Devuelve un diccionario combinado que incluye tanto el valor como la marca
    # de tiempo de la ultima actualizacion para cada clave del estado.
    #
    # Returns:
    #   dict: Un diccionario como {'key1': {'value': v1, 'timestamp': t1}, ...}
    def get_status_with_timestamps(self):
        with self._lock:
            combined = {}
            for key, value in self._status.items():
                combined[key] = {
                    'value': value,
                    'timestamp': self._last_update_time.get(key)
                }
            return combined

    # Prepara y devuelve un diccionario de contexto formateado para ser enviado a un
    # modelo de lenguaje grande (LLM). Este metodo es una primera implementacion
    # de la vision de procesar el estado crudo para la IA. Permite filtrar
    # datos por antiguedad y renombrar/formatear claves para que sean mas
    # comprensibles para el LLM.
    #
    # Args:
    #   max_age_seconds (float, optional): Si se proporciona, solo se incluiran
    #                                      datos actualizados en los ultimos X segundos.
    #                                      Defaults to None (incluir todo).
    # Returns:
    #   dict: Diccionario con el contexto formateado.
    def get_context_for_gpt(self, max_age_seconds=None):
        context = {}
        current_time = time.time()
        with self._lock:
            status_copy = self._status.copy() # Trabaja con una copia para no mantener el lock durante el procesamiento

        # Aqui se pueden seleccionar, renombrar o formatear las claves/valores
        # que sean mas utiles para el contexto del LLM.
        for key, value in status_copy.items():
            include = True
            # Filtra datos antiguos si se especifica max_age_seconds
            if max_age_seconds is not None:
                last_update = self._last_update_time.get(key)
                if last_update is None or (current_time - last_update > max_age_seconds):
                    include = False # Omite el dato si es muy viejo

            if include:
                # Ejemplo de como renombrar y formatear claves para el LLM
                if key == "battery_level":
                    context["nivel_bateria"] = value
                elif key == "robot_posture":
                    context["postura_robot"] = value
                elif key == "last_tactile_gesture":
                    context["ultimo_gesto_tactil"] = value
                elif key == "robot_mood_state": # El que viene del evento de Naoqi
                    # Extrae partes relevantes si el valor es un diccionario complejo
                    if isinstance(value, dict):
                        context["humor_robot_placer"] = value.get("pleasure", "desconocido")
                        context["humor_robot_excitacion"] = value.get("excitement", "desconocido")
                    else:
                        context["humor_robot_estado_raw"] = repr(value)
                elif key == "human_valence":
                    context["percepcion_humano_valencia"] = value
                elif key == "environment_ambiance":
                    context["ambiente_percibido"] = value
                elif key == "last_word_recognized":
                    context["ultima_palabra_escuchada"] = value
                # Se pueden anadir mas claves aqui segun las necesidades del contexto para el LLM

        # En una version mas avanzada, se podria anadir aqui el "contexto principal"
        # que seria analizado por otro modelo de IA.
        # context["contexto_principal"] = self.get_value("contexto_principal_actual", "ninguno")
        return context

# --- Ejemplo Conceptual de Uso ---
# Este bloque de codigo no se ejecuta, solo ilustra como se usaria la clase
# desde otros modulos del sistema.

# En un script principal como main.py:
# from Robot_Status import RobotStatus
# robot_status_instance = RobotStatus() # Se crea UNA unica instancia para toda la aplicacion.

# En un modulo de percepcion como Perception_Sys.py:
# # (En el constructor, se recibiria la instancia unica)
# def __init__(..., robot_status_ref):
#     self.robot_status = robot_status_ref
# # (Y en los callbacks de eventos, se actualizaria el estado)
# def _on_touch_changed(self, value):
#     # ...
#     self.robot_status.update_status("touched_sensors", touched_sensors)

# En un modulo que necesita leer el estado, como ConversationManager.py:
# estado_actual_completo = robot_status_instance.get_full_status_dict()
# nivel_bateria = robot_status_instance.get_value("battery_level")
# # Para preparar el contexto para la IA, solo con datos de los ultimos 10 segundos:
# contexto_para_ia = robot_status_instance.get_context_for_gpt(max_age_seconds=10)
# print(contexto_para_ia)
