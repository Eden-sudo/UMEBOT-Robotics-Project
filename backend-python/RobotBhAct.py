# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Gestor de Comportamiento y Actuacion del Robot (RobotBhAct)
# Funcion Principal: Este modulo define la clase RobotBhAct, disenada como el
#                    controlador principal y cerebro del robot. Su proposito era
#                    integrar todos los componentes del sistema: recibir datos de
#                    percepcion (desde Perception_Sys), mantener un estado centralizado
#                    (con Robot_Status), decidir la siguiente accion a tomar, y
#                    ejecutarla (usando modulos de actuacion como AnimationsSpeech).
#                    La vision para este modulo era muy ambiciosa: no solo reaccionar
#                    a eventos directos, sino tambien analizar el estado general y los
#                    eventos recientes (potencialmente con un modelo de IA de analisis
#                    de contexto) para luego pasar esta informacion enriquecida al modelo
#                    de IA generativo, logrando asi un nivel de autonomia y coherencia
#                    muy alto.
#                    Debido a la falta de tiempo y a la priorizacion de otros puntos
#                    del sistema, su implementacion quedo en una fase experimental y
#                    conceptual, utilizando clases 'placeholder' para simular la
#                    logica de decision y accion.
# ----------------------------------------------------------------------------------

import qi
import time
import threading
import random # Usado en el placeholder de la logica de decision

# Importacion de los otros modulos de la arquitectura.
from Robot_Status import RobotStatus
from Perception_Sys import PerceptionModule

# --- Clases Placeholder para Demostracion Conceptual ---
# Estas clases simulan los modulos de accion que serian llamados por RobotBhAct.
# En una implementacion completa, serian reemplazadas por los modulos reales.

class BasicBehaviors:
    # Simula un modulo que manejaria reacciones basicas y pre-programadas a eventos.
    def __init__(self, tts_proxy, anim_player_proxy): self.tts = tts_proxy; self.anim_player = anim_player_proxy
    def react_to_touch(self, sensor_info): print(f"[BasicBehaviors Placeholder] Reaccionando a toque: {sensor_info}")
    def react_to_gesture(self, gesture): print(f"[BasicBehaviors Placeholder] Reaccionando a gesto: {gesture}")
    def react_to_word(self, word): print(f"[BasicBehaviors Placeholder] Reaccionando a palabra: {word}")

class LocalAnimator:
    # Simula un modulo que ejecutaria animaciones locales .qianim.
    def __init__(self, session, actuation_proxy, actuation_private_proxy): self.session = session; self.actuation=actuation_proxy; self.actuation_private=actuation_private_proxy
    def execute_qianim(self, relative_path): print(f"[LocalAnimator Placeholder] Ejecutando (simulado): {relative_path}")

class GPT_Responder:
    # Simula el modulo que interactuaria con la IA generativa para obtener respuestas complejas.
    def __init__(self): pass
    def get_response(self, context): print(f"[GPT_Responder Placeholder] Obteniendo respuesta para contexto: {context}"); return {"action": "talk", "text": "Respuesta simulada de la IA."}


# Controlador principal que integra percepcion, estado, logica de decision y actuacion.
# Orquesta el ciclo de "sentir-pensar-actuar" del robot.
class RobotBhAct:
    # Inicializa el controlador principal.
    # Crea instancias de todos los modulos necesarios (percepcion, estado, actuacion)
    # y los interconecta.
    #
    # Args:
    #   session (qi.Session): La sesion qi activa con el robot.
    #   naoqi_services (dict): Diccionario con los proxies a los servicios de NAOqi
    #                          obtenidos por Nao_Services.py.
    def __init__(self, session, naoqi_services):
        print("[RobotBhAct] Inicializando el gestor de comportamiento...")
        self.session = session
        self.naoqi_services = naoqi_services
        self._is_active = False # Bandera para controlar el estado del bucle principal
        self._processing_thread = None # Hilo para el bucle de procesamiento
        self._lock = threading.Lock() # Lock para proteger el acceso a variables compartidas si fuera necesario

        # 1. Crear la instancia del Estado del Robot (el 'tablero de informacion').
        self.robot_status = RobotStatus()

        # 2. Crear la instancia del Modulo de Percepcion.
        try:
            self.perception = PerceptionModule(
                memory_proxy=self.naoqi_services.get("ALMemory"),
                tactile_gesture_proxy=self.naoqi_services.get("ALTactileGesture"),
                posture_proxy=self.naoqi_services.get("ALRobotPosture"),
                robot_mood_proxy=self.naoqi_services.get("ALRobotMood"),
                mood_proxy=self.naoqi_services.get("ALMood"),
                asr_proxy=self.naoqi_services.get("ALSpeechRecognition")
            )
            # Conecta la salida de PerceptionModule directamente con el metodo de actualizacion de RobotStatus.
            # Esto asegura que cualquier dato de percepcion actualice el estado centralizado.
            self.perception.update_robot_status = self.robot_status.update_status
        except ValueError as ve:
            print(f"[RobotBhAct] ERROR: Faltan proxies obligatorios para PerceptionModule: {ve}"); raise
        except Exception as e:
            print(f"[RobotBhAct] ERROR inesperado creando PerceptionModule: {e}"); raise

        # 3. Crear instancias de los modulos de accion (actualmente placeholders).
        self.basic_behaviors = BasicBehaviors(
            tts_proxy=self.naoqi_services.get("ALTextToSpeech"),
            anim_player_proxy=self.naoqi_services.get("ALAnimationPlayer")
        )
        self.local_animator = LocalAnimator(
            session=self.session,
            actuation_proxy=self.naoqi_services.get("Actuation"),
            actuation_private_proxy=self.naoqi_services.get("ActuationPrivate")
        )
        self.gpt_responder = GPT_Responder() # En una version real, pasaria la configuracion de la IA.

        print("[RobotBhAct] Inicializacion del gestor completada.")

    # Inicia el ciclo de vida del controlador, activando las suscripciones de
    # percepcion y lanzando el bucle de procesamiento principal en un hilo separado.
    def start(self):
        if self._is_active: print("[RobotBhAct] El gestor ya esta activo."); return

        print("[RobotBhAct] Iniciando el gestor de comportamiento...")
        # Inicia las suscripciones de percepcion a los eventos de Naoqi.
        if not self.perception.setup_subscriptions():
            print("[RobotBhAct] ERROR: No se pudieron iniciar las suscripciones de percepcion. El gestor no se iniciara."); return False

        self._is_active = True
        # Inicia el hilo para el bucle de procesamiento principal, permitiendo que start() no bloquee el programa.
        self._processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._processing_thread.start()
        print("[RobotBhAct] Hilo de procesamiento del comportamiento iniciado."); return True

    # Detiene el controlador de forma ordenada, terminando el bucle de procesamiento
    # y desactivando las suscripciones de percepcion.
    def stop(self):
        if not self._is_active: print("[RobotBhAct] El gestor ya esta detenido."); return

        print("[RobotBhAct] Deteniendo el gestor de comportamiento...");
        self._is_active = False # Senala al hilo que debe detenerse
        self.perception.shutdown_subscriptions() # Detiene las suscripciones de percepcion

        # Espera a que el hilo de procesamiento termine
        if self._processing_thread and self._processing_thread.is_alive():
            print("[RobotBhAct] Esperando finalizacion del hilo de procesamiento...")
            self._processing_thread.join(timeout=2.0) # Espera maximo 2 segundos
            if self._processing_thread.is_alive():
                print("[RobotBhAct] ADVERTENCIA: El hilo de procesamiento no termino limpiamente.")

        self._processing_thread = None; print("[RobotBhAct] Gestor de comportamiento detenido.")

    # Bucle principal de "sentir-pensar-actuar", ejecutado en un hilo dedicado.
    # 1. Actualiza periodicamente el estado del robot (polling).
    # 2. Implementa una logica de decision (actualmente un placeholder) para elegir
    #    entre una reaccion basica o una respuesta generada por IA.
    # 3. Llama a la ejecucion de la accion decidida.
    def _processing_loop(self):
        print("[RobotBhAct] Bucle de procesamiento de comportamiento iniciado.")
        last_poll_time = time.time()
        POLL_INTERVAL = 3.0 # Intervalo para sondear datos como bateria/postura
        DECISION_INTERVAL = 1.5 # Intervalo para tomar decisiones proactivas

        while self._is_active:
            loop_start_time = time.time()

            # --- 1. SENTIR (Actualizar datos de Polling) ---
            if loop_start_time - last_poll_time >= POLL_INTERVAL:
                self.perception.update_polled_data()
                last_poll_time = loop_start_time

            # --- 2. PENSAR (Logica de Decision Principal) ---
            try:
                # La implementacion actual es un placeholder para demostrar el concepto.
                # En la version real, esta decision seria mucho mas compleja.
                if random.random() < 0.2: # 20% de probabilidad de usar IA (simulado)
                    print("[RobotBhAct] Decision: Usar IA para una accion proactiva.")
                    contexto_gpt = self.robot_status.get_context_for_gpt() # Obtiene el contexto formateado para la IA
                    respuesta_ia = self.gpt_responder.get_response(contexto_gpt) # Obtiene la accion de la IA
                    self._execute_action(respuesta_ia) # Ejecuta la accion sugerida
                else: # 80% de probabilidad de buscar una reaccion basica a un evento reciente
                    gesture = self.robot_status.get_value("last_tactile_gesture")
                    word = self.robot_status.get_value("last_word_recognized")
                    if gesture:
                        print(f"[RobotBhAct] Decision: Reaccion basica al gesto '{gesture}'.")
                        self.basic_behaviors.react_to_gesture(gesture)
                        self.robot_status.update_status("last_tactile_gesture", None) # "Consume" el evento para no repetirlo
                    elif word:
                        print(f"[RobotBhAct] Decision: Reaccion basica a la palabra '{word}'.")
                        self.basic_behaviors.react_to_word(word)
                        self.robot_status.update_status("last_word_recognized", None) # "Consume" el evento
            except Exception as e_logic:
                print(f"[RobotBhAct] ERROR en la logica de decision: {e_logic}")

            # --- Esperar para el proximo ciclo ---
            elapsed_time = time.time() - loop_start_time
            sleep_time = max(0, DECISION_INTERVAL - elapsed_time)
            time.sleep(sleep_time)

        print("[RobotBhAct] Bucle de procesamiento de comportamiento finalizado.")

    # Despachador de acciones (Action dispatcher).
    # Recibe un diccionario que describe una accion y llama al servicio o
    # modulo correspondiente para ejecutarla fisicamente en el robot.
    def _execute_action(self, action_data):
        if not action_data or not isinstance(action_data, dict): return
        action_type = action_data.get("action")
        print(f"[RobotBhAct] Ejecutando accion de tipo: {action_type}")
        try:
            if action_type == "talk":
                text = action_data.get("text", "No se que decir.")
                # Usa AnimatedSpeech si esta disponible, si no, TTS basico
                animated_speech = self.naoqi_services.get("ALAnimatedSpeech")
                if animated_speech: animated_speech.say(text) # Puede ser asincrono
                else:
                    tts = self.naoqi_services.get("ALTextToSpeech")
                    if tts: tts.say(text)
                self.robot_status.update_status("last_action", f"talk: {text[:30]}...")
            elif action_type == "animate_tag":
                tag = action_data.get("tag")
                if tag:
                    anim_player = self.naoqi_services.get("ALAnimationPlayer")
                    if anim_player: anim_player.runTag(tag)
                    self.robot_status.update_status("last_action", f"animate_tag: {tag}")
            # Se pueden anadir mas tipos de accion aqui (move, animate_local, etc.)
        except Exception as e_exec:
            print(f"[RobotBhAct] ERROR ejecutando la accion '{action_type}': {e_exec}")

# --- Ejemplo Conceptual de Uso en main.py ---
# if __name__ == "__main__":
#     # ... (codigo de conexion para obtener 'session' y 'services') ...
#     if session:
#         try:
#             # ... (codigo de setup) ...
#             # if setup_ok:
#             robot_controller = RobotBhAct(session, services)
#             if robot_controller.start():
#                 print("[Main] RobotBhAct iniciado. Presiona Ctrl+C para detener.")
#                 try:
#                     while True: time.sleep(1) # Mantiene el programa principal vivo
#                 except KeyboardInterrupt:
#                     print("\n[Main] Deteniendo RobotBhAct...")
#                     robot_controller.stop()
#         except Exception as e: print(f"[Main] Error: {e}")
#         finally:
#             # ... (limpieza final) ...
#             print("[Main] Programa finalizado.")
