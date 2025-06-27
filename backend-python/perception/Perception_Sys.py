# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Sistema de Percepcion Experimental del Robot (Eventos y Polling)
# Funcion Principal: Este modulo define la clase PerceptionModule, disenada como
#                    un sistema conceptual y experimental para abstraer y centralizar
#                    la obtencion de datos de percepcion del robot. Su objetivo era
#                    suscribirse a diversas senales y eventos de los servicios NAOqi
#                    (ej. sensores tactiles, reconocimiento de gestos, deteccion de
#                    humanos) y tambien sondear (poll) datos que no generan eventos
#                    (ej. bateria, postura).
#                    La vision era que este modulo publicara toda la informacion
#                    recopilada en un almacen de datos centralizado (como una base
#                    de datos vectorial o un 'datastore' de estado), para que otros
#                    modulos del sistema pudieran consumirla y reaccionar a los
#                    cambios del entorno o del estado del robot.
#                    Debido a la falta de tiempo y a la priorizacion de otros puntos,
#                    su integracion final y la implementacion del almacen de datos
#                    quedaron en fase conceptual.
# ----------------------------------------------------------------------------------

import qi
import time
import functools

# --- Placeholder para la Interaccion con el Estado Centralizado ---
# En una implementacion completa, esta funcion interactuaria con un almacen de
# datos, una base de datos vectorial, o un objeto de estado compartido (ej. Robot_Status).
# Aqui, simplemente imprime los datos en la consola para demostrar que se reciben.
def update_robot_status(key, value):
    timestamp = time.time()
    # Se podria anadir logica aqui para formatear valores complejos antes de guardarlos.
    value_repr = repr(value)
    if len(value_repr) > 100: # Acorta el valor si es muy largo para una impresion limpia.
        value_repr = value_repr[:100] + '...'
    print(f"[{timestamp:.2f}][Perception EVENT/POLL -> RobotStatus] {key} = {value_repr}")
# ------------------------------------------------------------------

# Gestiona las suscripciones a eventos de los servicios NAOqi y el sondeo (polling)
# periodico de datos para recopilar informacion de percepcion del robot y su entorno.
class PerceptionModule:
    # Inicializa el modulo de percepcion.
    # Utiliza inyeccion de dependencias para recibir los proxies a los servicios
    # de NAOqi necesarios para obtener los datos de percepcion.
    #
    # Args:
    #   memory_proxy: Proxy a ALMemory (Obligatorio).
    #   tactile_gesture_proxy: Proxy a ALTactileGesture (Obligatorio).
    #   posture_proxy: Proxy a ALRobotPosture (Obligatorio para sondeo de postura).
    #   robot_mood_proxy: Proxy a ALRobotMood (Opcional, para eventos/sondeo de humor del robot).
    #   mood_proxy: Proxy a ALMood (Opcional, para eventos/sondeo de humor del entorno/humano).
    #   human_awareness_proxy: Proxy a ALHumanAwareness (Opcional, para eventos de deteccion de humanos).
    #   asr_proxy: Proxy a ALSpeechRecognition (Opcional, para eventos de reconocimiento de voz de Naoqi).
    #
    # Raises:
    #   ValueError: Si faltan los proxies obligatorios.
    def __init__(self, memory_proxy, tactile_gesture_proxy, posture_proxy,
                 robot_mood_proxy=None, mood_proxy=None,
                 human_awareness_proxy=None, asr_proxy=None):
        print("[PerceptionModule] Inicializando con proxies de servicio inyectados...")
        if not all([memory_proxy, tactile_gesture_proxy, posture_proxy]):
            raise ValueError("Se requieren proxies validos para ALMemory, ALTactileGesture y ALRobotPosture.")

        # Almacena los proxies recibidos
        self.memory = memory_proxy
        self.tactile_gesture = tactile_gesture_proxy
        self.posture = posture_proxy
        self.robot_mood = robot_mood_proxy
        self.mood = mood_proxy
        self.human_awareness = human_awareness_proxy
        self.asr = asr_proxy

        # Almacenamiento para gestionar y limpiar las suscripciones
        self._subscribers = {}      # Para suscriptores de ALMemory
        self._signal_links = {}     # Para senales directas de servicios
        self._is_running = False    # Bandera para controlar el estado del modulo

        print("[PerceptionModule] Proxies de servicio recibidos correctamente.")

    # --- Callbacks para Senales y Eventos de NAOqi ---
    # Cada uno de estos metodos es llamado automaticamente cuando ocurre un evento especifico en el robot.
    # Su funcion es tomar los datos del evento y pasarlos al actualizador de estado central.

    def _on_touch_changed(self, value):
        # Callback para el evento 'TouchChanged' de ALMemory.
        if not self._is_running or not value: return
        touched_sensors = {name: state for name, state in value}
        update_robot_status("touched_sensors", touched_sensors)

    def _on_tactile_gesture(self, gesture_name):
        # Callback para la senal 'onGesture' de ALTactileGesture.
        if not self._is_running or not gesture_name: return
        update_robot_status("last_tactile_gesture", gesture_name)

    def _on_word_recognized(self, value):
        # Callback para el evento 'WordRecognized' de ALMemory (usado por ASR).
        if not self._is_running or not isinstance(value, list) or len(value) < 2: return
        word, confidence = value[0], value[1]
        if confidence > 0.45: # Filtrar por confianza
            update_robot_status("last_word_recognized", word)
            update_robot_status("last_word_confidence", confidence)

    def _on_robot_mood_changed(self, state):
        # Callback para la senal 'stateChanged' de ALRobotMood.
        if not self._is_running: return
        update_robot_status("robot_mood_state", state) # Ej: {"pleasure":"positive", "excitement":"calm"}

    def _on_human_valence_changed(self, valence_state):
        # Callback para la senal 'valenceChanged' de ALMood.
        if not self._is_running: return
        update_robot_status("human_valence", valence_state) # Ej: "positive", "neutral", "negative"

    def _on_human_attention_changed(self, attention_state):
        # Callback para la senal 'attentionChanged' de ALMood.
        if not self._is_running: return
        update_robot_status("human_attention", attention_state) # Ej: "looking", "not_looking"

    def _on_ambiance_changed(self, ambiance_state):
        # Callback para la senal 'ambianceChanged' de ALMood.
        if not self._is_running: return
        update_robot_status("environment_ambiance", ambiance_state) # Ej: "calm", "agitated"

    def _on_humans_around_changed(self, humans_list):
        # Callback para la senal 'humansAround' de ALHumanAwareness.
        if not self._is_running: return
        num_humans = len(humans_list) if humans_list else 0
        update_robot_status("humans_around_count", num_humans)
        # En una implementacion mas avanzada, se podrian extraer mas datos de cada humano detectado.

    # --- Metodos de Configuracion y Limpieza de Suscripciones ---

    # Configura y activa todas las suscripciones a los eventos y senales de NAOqi.
    # Conecta los metodos callback de esta clase a las senales correspondientes de los
    # servicios del robot. Mantiene un registro de las suscripciones para su limpieza posterior.
    #
    # Returns:
    #   bool: True si todas las suscripciones obligatorias se configuraron con exito.
    def setup_subscriptions(self):
        if self._is_running: return True
        print("[PerceptionModule] Configurando suscripciones a eventos de NAOqi...")
        self._is_running = True
        success = True
        try:
            # --- Suscripciones Obligatorias ---
            # Evento para sensores tactiles
            touch_sub = self.memory.subscriber("TouchChanged")
            self._subscribers["TouchChanged"] = touch_sub
            link_id = touch_sub.signal.connect(self._on_touch_changed)
            self._signal_links[("ALMemory", "TouchChanged")] = link_id
            print("   - Suscrito a ALMemory:TouchChanged.")

            # Senal para gestos tactiles (ej. doble toque en la cabeza)
            link_id = self.tactile_gesture.onGesture.connect(self._on_tactile_gesture)
            self._signal_links[("ALTactileGesture", "onGesture")] = link_id
            print("   - Suscrito a ALTactileGesture.onGesture.")

            # --- Suscripciones Opcionales (si se proporcionaron los proxies) ---
            if self.robot_mood:
                try:
                    link_id = self.robot_mood.stateChanged.connect(self._on_robot_mood_changed)
                    self._signal_links[("ALRobotMood", "stateChanged")] = link_id
                    print("   - Suscrito a ALRobotMood.stateChanged.")
                except Exception as e: print(f"   - WARN: Fallo al suscribir a ALRobotMood.stateChanged: {e}")

            if self.mood:
                try:
                    link_id = self.mood.valenceChanged.connect(self._on_human_valence_changed)
                    self._signal_links[("ALMood", "valenceChanged")] = link_id
                    print("   - Suscrito a ALMood.valenceChanged.")
                    link_id = self.mood.attentionChanged.connect(self._on_human_attention_changed)
                    self._signal_links[("ALMood", "attentionChanged")] = link_id
                    print("   - Suscrito a ALMood.attentionChanged.")
                    link_id = self.mood.ambianceChanged.connect(self._on_ambiance_changed)
                    self._signal_links[("ALMood", "ambianceChanged")] = link_id
                    print("   - Suscrito a ALMood.ambianceChanged.")
                except Exception as e: print(f"   - WARN: Fallo al suscribir a senales de ALMood: {e}")

            if self.human_awareness:
                try:
                    link_id = self.human_awareness.humansAround.connect(self._on_humans_around_changed)
                    self._signal_links[("HumanAwareness", "humansAround")] = link_id
                    print("   - Suscrito a HumanAwareness.humansAround.")
                except Exception as e: print(f"   - WARN: Fallo al suscribir a HumanAwareness.humansAround: {e}")

            # El ASR de Naoqi es opcional y generalmente se reemplaza por sistemas externos (Vosk).
            if self.asr:
                try:
                    print("   - Configurando ASR de Naoqi...")
                    self.asr.pause(True); self.asr.setLanguage("Spanish"); self.asr.setVocabulary(["hola", "adios", "ayuda"], False); self.asr.pause(False)
                    word_sub = self.memory.subscriber("WordRecognized")
                    self._subscribers["WordRecognized"] = word_sub
                    link_id = word_sub.signal.connect(self._on_word_recognized)
                    self._signal_links[("ALMemory", "WordRecognized")] = link_id
                    print("   - Suscrito a ALMemory:WordRecognized.")
                except Exception as e_asr: print(f"   - ADVERTENCIA: Fallo en configuracion o suscripcion a ASR de Naoqi: {e_asr}")

            print("[PerceptionModule] Suscripciones configuradas exitosamente.")
        except Exception as e:
            print(f"[PerceptionModule] ERROR CRITICO configurando las suscripciones: {e}")
            self.shutdown_subscriptions(); success = False # Intenta limpiar si algo fallo
        if not success: self._is_running = False
        return success

    # Desconecta de forma ordenada todas las senales y limpia los suscriptores.
    # Es crucial llamar a este metodo para evitar fugas de memoria y callbacks
    # huerfanos cuando el modulo de percepcion ya no esta en uso.
    def shutdown_subscriptions(self):
        if not self._is_running: return
        print("[PerceptionModule] Terminando todas las suscripciones activas...")
        self._is_running = False
        # Itera sobre una copia de los items para poder modificar el diccionario original
        links_to_disconnect = list(self._signal_links.items())
        for key, link_id in links_to_disconnect:
            try:
                service_proxy = None; signal_name = None; is_memory_event = False
                # Logica para obtener el proxy correcto basado en la clave guardada, ya que los nombres
                # de los atributos de la clase no siempre coinciden con los nombres de los servicios.
                if isinstance(key, tuple): service_name_str, signal_name = key; service_proxy = getattr(self, service_name_str.lower().replace('al',''), None);
                else: service_name_str = "ALMemory"; signal_name = key; service_proxy = self.memory; is_memory_event = True
                # Casos especiales si el nombre del atributo no coincide
                if service_name_str == "ALTactileGesture" and not service_proxy: service_proxy = self.tactile_gesture
                if service_name_str == "ALRobotMood" and not service_proxy: service_proxy = self.robot_mood
                if service_name_str == "ALMood" and not service_proxy: service_proxy = self.mood
                if service_name_str == "HumanAwareness" and not service_proxy: service_proxy = self.human_awareness

                if service_proxy:
                    if is_memory_event: # Los eventos de ALMemory se desconectan a traves del suscriptor
                        subscriber = self._subscribers.get(signal_name);
                        if subscriber: subscriber.signal.disconnect(link_id); print(f"   - Suscriptor {service_name_str}:{signal_name} desconectado.")
                    else: # Las senales directas se desconectan del proxy del servicio
                        signal = getattr(service_proxy, signal_name, None)
                        if signal: signal.disconnect(link_id); print(f"   - Senal {service_name_str}.{signal_name} desconectada.")
            except Exception as e: print(f"   - ERROR desconectando suscripcion '{key}': {e}")
        self._subscribers.clear(); self._signal_links.clear() # Limpia los diccionarios
        print("[PerceptionModule] Suscripciones terminadas.")

    # Obtiene datos mediante sondeo (polling) que no estan disponibles a traves de eventos.
    # Este metodo esta disenado para ser llamado periodicamente desde un bucle externo
    # para actualizar datos como el nivel de bateria y la postura actual del robot.
    def update_polled_data(self):
        if not self._is_running: return # No hacer nada si el modulo no esta activo

        # Sondeo del nivel de bateria
        try:
            battery_level = round(self.memory.getData("Device/SubDeviceList/Battery/Charge/Sensor/Value"), 3)
            update_robot_status("battery_level", battery_level)
        except Exception:
            update_robot_status("battery_level", None) # Informa que no se pudo obtener

        # Sondeo de la postura actual
        try:
            posture = self.posture.getPosture()
            update_robot_status("robot_posture", posture)
        except Exception:
            update_robot_status("robot_posture", "Unknown")

        # Ejemplo de sondeo opcional para otros datos
        # if self.robot_mood:
        #     try: update_robot_status("robot_mood_polled", self.robot_mood.getState())
        #     except: update_robot_status("robot_mood_polled", None)
        # if self.mood:
        #     try: update_robot_status("ambiance_polled", self.mood.ambianceState())
        #     except: update_robot_status("ambiance_polled", None)
