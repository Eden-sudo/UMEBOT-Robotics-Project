#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Controlador de Animaciones y Habla Expresiva
# Funcion Principal: Este modulo define la clase AnimationSpeechController, un
#                    componente fundamental que gestiona la expresividad del robot.
#                    Permite ejecutar tres tipos de acciones:
#                    1. Habla anotada: Texto con 'tags' de animacion estandar
#                       incrustados (ej. ^runTag(joy)) usando ALAnimatedSpeech.
#                       Esto permite que el modelo de IA controle las expresiones del
#                       robot mientras habla.
#                    2. Animaciones estandar: Ejecucion de animaciones nativas del
#                       robot por su nombre de 'tag' (usando ALAnimationPlayer).
#                    3. Animaciones locales (.qianim): Ejecucion de archivos .qianim
#                       personalizados y almacenados localmente. La logica para esto
#                       fue desarrollada mediante ingenieria inversa de los servicios
#                       no documentados 'Actuation' y 'ActuationPrivate', emulando
#                       el comportamiento del SDK de Android para Naoqi 2.9.
# ----------------------------------------------------------------------------------

import qi
import os
import random
import time
import logging
from typing import List, Dict, Optional, Any

# Configuracion del logger para este modulo
log = logging.getLogger("AnimationSpeechController")

# Controlador de alto nivel para gestionar y ejecutar la expresividad del robot,
# combinando habla, animaciones estandar del sistema y animaciones locales personalizadas.
class AnimationSpeechController:
    # Inicializa el controlador de animaciones y habla.
    #
    # Args:
    #   local_anims_base_path (str): Ruta al directorio base que contiene las animaciones .qianim locales.
    #   animated_speech_proxy (Any): Proxy al servicio ALAnimatedSpeech (requerido para habla anotada).
    #   motion_proxy (Any): Proxy al servicio ALMotion (requerido para verificaciones de estado).
    #   animation_player_proxy (Optional[Any]): Proxy al servicio ALAnimationPlayer (para tags estandar).
    #   actuation_proxy (Optional[Any]): Proxy a Actuation (para .qianim locales, via ingenieria inversa).
    #   actuation_private_proxy (Optional[Any]): Proxy a ActuationPrivate (para .qianim locales, via ingenieria inversa).
    #
    # Raises:
    #   ValueError: Si los proxies requeridos (ALAnimatedSpeech, ALMotion) no se proporcionan.
    def __init__(self,
                 local_anims_base_path: str,
                 animated_speech_proxy: Any,
                 motion_proxy: Any,
                 animation_player_proxy: Optional[Any] = None,
                 actuation_proxy: Optional[Any] = None,
                 actuation_private_proxy: Optional[Any] = None):

        log.info("ASC: Inicializando AnimationSpeechController...")

        if not animated_speech_proxy:
            msg = "ASC: animated_speech_proxy (ALAnimatedSpeech) es requerido."; log.critical(msg); raise ValueError(msg)
        if not motion_proxy:
            msg = "ASC: motion_proxy (ALMotion) es requerido."; log.critical(msg); raise ValueError(msg)

        # Asignacion de los proxies a los servicios de Naoqi
        self.animated_speech = animated_speech_proxy
        self.motion = motion_proxy
        self.anim_player = animation_player_proxy
        self.actuation = actuation_proxy # Servicio no documentado para animaciones locales
        self.actuation_private = actuation_private_proxy # Servicio no documentado para animaciones locales

        # Escanea y carga las animaciones locales desde el sistema de archivos
        self.local_anims_base_path = os.path.abspath(local_anims_base_path)
        self.local_anims_by_category: Dict[str, List[str]] = {}
        self._scan_local_animations()

        # Verifica si el controlador puede ejecutar animaciones por tags estandar
        self.can_run_standard_tags = self.anim_player is not None
        if not self.can_run_standard_tags:
            log.warning("ASC: ALAnimationPlayer no fue proporcionado. La ejecucion de tags de animacion estandar (^runTag) dependera unicamente de ALAnimatedSpeech.")

        # Verifica si el controlador puede ejecutar animaciones .qianim locales
        self.can_run_local_anims = self.actuation is not None and self.actuation_private is not None
        if not self.can_run_local_anims:
            log.warning("ASC: Los servicios Actuation y/o ActuationPrivate no fueron proporcionados. Las animaciones locales .qianim estaran deshabilitadas.")
        elif not self.local_anims_by_category:
            log.warning(f"ASC: No se encontraron animaciones .qianim locales en la ruta '{self.local_anims_base_path}' o sus subdirectorios.")
        else:
            total_anims = sum(len(paths) for paths in self.local_anims_by_category.values())
            log.info(f"ASC: {total_anims} animaciones .qianim locales encontradas en {len(self.local_anims_by_category)} categorias.")

        self._current_speech_future: Optional[qi.Future] = None # Para rastrear el habla asincrona
        log.info("ASC: AnimationSpeechController inicializado correctamente.")

    # Escanea el directorio base de animaciones locales para descubrir y catalogar
    # archivos .qianim. Organiza las animaciones encontradas por categoria (nombre del subdirectorio).
    def _scan_local_animations(self):
        if not os.path.isdir(self.local_anims_base_path):
            log.error(f"ASC: El directorio de animaciones locales especificado no existe: '{self.local_anims_base_path}'")
            return

        log.debug(f"ASC: Escaneando animaciones locales en la ruta: {self.local_anims_base_path}")
        for category_name in os.listdir(self.local_anims_base_path):
            category_path = os.path.join(self.local_anims_base_path, category_name)
            if os.path.isdir(category_path): # Cada subdirectorio es una categoria
                qianim_files = [
                    os.path.join(category_path, f)
                    for f in os.listdir(category_path)
                    if f.lower().endswith(".qianim") # Busca archivos .qianim
                ]
                if qianim_files:
                    self.local_anims_by_category[category_name] = qianim_files
                    log.info(f"ASC: Categoria '{category_name}' cargada con {len(qianim_files)} animaciones.")
                else:
                    log.debug(f"ASC: Categoria (directorio) '{category_name}' encontrada pero no contiene archivos .qianim.")
        if not self.local_anims_by_category:
            log.warning(f"ASC: No se cargo ninguna categoria de animaciones locales desde '{self.local_anims_base_path}'.")

    # Ejecuta un archivo .qianim local de forma asincrona.
    # Este metodo implementa la logica descubierta por ingenieria inversa para
    # ejecutar animaciones locales, utilizando los servicios no documentados
    # Actuation y ActuationPrivate para crear y correr la animacion.
    #
    # Args:
    #   qianim_path_on_pc (str): Ruta completa al archivo .qianim en el PC.
    #
    # Returns:
    #   Optional[qi.Future]: Un objeto Future que representa la ejecucion asincrona de la animacion.
    def _execute_local_qianim_experimental(self, qianim_path_on_pc: str) -> Optional[qi.Future]:
        if not self.can_run_local_anims:
            log.error("ASC: No se puede ejecutar animacion .qianim local: faltan los proxies a Actuation y/o ActuationPrivate."); return None

        anim_name = os.path.basename(qianim_path_on_pc)
        log.info(f"ASC: Intentando ejecutar animacion .qianim local: '{anim_name}' desde '{qianim_path_on_pc}'")
        try:
            # Asegura que el robot este despierto antes de una animacion
            if not self.motion.robotIsWakeUp():
                log.info("ASC: El robot no esta despierto. Ejecutando wakeUp() antes de la animacion..."); self.motion.wakeUp(); time.sleep(0.5)
            # Lee el contenido del archivo de animacion
            with open(qianim_path_on_pc, 'r', encoding='utf-8') as f: qianim_content = f.read()
            # Paso 1 (Ingenieria Inversa): Usa Actuation.makeAnimation para crear un handle de animacion
            future_make = self.actuation.makeAnimation([qianim_content], _async=True)
            anim_handle = future_make.value(timeout=5000) # Espera el resultado
            if not anim_handle: raise RuntimeError("Actuation.makeAnimation devolvio un handle nulo o excedio el timeout.")
            # Paso 2 (Ingenieria Inversa): Usa ActuationPrivate.makeAnimate con el handle para obtener un objeto 'animable'
            future_animate_obj = self.actuation_private.makeAnimate(anim_handle, _async=True)
            animate_obj = future_animate_obj.value(timeout=5000) # Espera el resultado
            if not animate_obj: raise RuntimeError("ActuationPrivate.makeAnimate devolvio un objeto nulo o excedio el timeout.")
            # Paso 3 (Ingenieria Inversa): Ejecuta el objeto 'animable'
            future_run = animate_obj.run(_async=True) # Ejecuta la animacion de forma asincrona
            log.info(f"ASC: Animacion local '{anim_name}' iniciada (asincrona via Actuation).")
            return future_run
        except RuntimeError as e_rt:
            log.error(f"ASC: RuntimeError ejecutando animacion .qianim '{anim_name}': {e_rt}", exc_info=True)
        except Exception as e:
            log.error(f"ASC: Excepcion general ejecutando animacion .qianim '{anim_name}': {e}", exc_info=True)
        return None

    # Utiliza ALAnimatedSpeech para decir un texto que puede contener tags de animacion estandar
    # incrustados (ej. "Hola ^runTag(joy) estoy feliz"). Esto permite al LLM controlar
    # la expresividad del robot directamente en sus respuestas.
    #
    # Args:
    #   annotated_text (str): El texto a decir, posiblemente con tags de animacion.
    #   wait_for_speech (bool): Si es True, la funcion es bloqueante. Si es False, es asincrona.
    #
    # Returns:
    #   Optional[qi.Future]: El futuro de la tarea de habla si es asincrona, o None.
    def say_text_with_embedded_standard_animations(self, annotated_text: str, wait_for_speech: bool = True) -> Optional[qi.Future]:
        if not self.animated_speech:
            log.error("ASC: El proxy a ALAnimatedSpeech no esta disponible para hablar."); return None
        # Advierte si se intenta hablar mientras otra tarea de habla podria estar activa
        if self._current_speech_future and not self._current_speech_future.isFinished():
            log.warning(f"ASC: Llamada a 'say' mientras un futuro de habla anterior podria estar activo. La nueva habla podria interrumpir o ser ignorada.")

        log.info(f"ASC: Solicitando habla (ALAnimatedSpeech): \"{annotated_text[:45].replace(os.linesep,' ')}...\" (Bloqueante: {wait_for_speech})")
        try:
            if not self.motion.robotIsWakeUp():
                log.info("ASC: El robot no esta despierto. Ejecutando wakeUp() antes de hablar..."); self.motion.wakeUp(); time.sleep(0.5)

            if wait_for_speech: # Ejecucion sincrona (bloqueante)
                self.animated_speech.say(annotated_text)
                self._current_speech_future = None
                log.info(f"ASC: Habla (sincrona) completada.")
                return None
            else: # Ejecucion asincrona
                self._current_speech_future = self.animated_speech.say(annotated_text, _async=True)
                log.info(f"ASC: Habla iniciada (asincrona). Futuro ID: {self._current_speech_future.id() if self._current_speech_future else 'N/A'}")
                return self._current_speech_future
        except RuntimeError as e_rt: log.error(f"ASC: RuntimeError en ALAnimatedSpeech.say: {e_rt}", exc_info=True)
        except Exception as e: log.error(f"ASC: Excepcion en say_text_with_embedded_standard_animations: {e}", exc_info=True)
        self._current_speech_future = None
        return None

    # Verifica si la ultima tarea de habla asincrona iniciada por este controlador sigue en ejecucion.
    #
    # Returns:
    #   bool: True si el robot aun esta hablando, False en caso contrario.
    def is_speaking(self) -> bool:
        if self._current_speech_future:
            if self._current_speech_future.isRunning():
                return True
            else: # Si ya no esta corriendo, limpiar la referencia
                self._current_speech_future = None; return False
        return False

    # Intenta detener inmediatamente el habla y las animaciones asociadas que fueron
    # iniciadas a traves de ALAnimatedSpeech.
    def stop_all_speech_and_anims(self):
        log.info("ASC: Solicitando detener toda el habla y animaciones de ALAnimatedSpeech...")
        try:
            if self.animated_speech and hasattr(self.animated_speech, 'stopAll'):
                self.animated_speech.stopAll(); log.info("ASC: ALAnimatedSpeech.stopAll() llamado.")
            # Cancela el futuro si aun esta activo
            if self._current_speech_future and self._current_speech_future.isRunning():
                self._current_speech_future.cancel(); log.info("ASC: Futuro de habla actual (ALAnimatedSpeech) cancelado.")
            self._current_speech_future = None
        except Exception as e: log.error(f"ASC: Error durante stop_all_speech_and_anims: {e}", exc_info=True)

    # Ejecuta una animacion local desde un archivo .qianim, seleccionandola de una categoria.
    # Si no se especifica un nombre, elige una animacion aleatoria de la categoria.
    #
    # Args:
    #   category_name (str): El nombre de la categoria (subdirectorio).
    #   specific_name_no_ext (Optional[str]): Nombre del archivo sin extension para ejecutar uno especifico.
    #   wait (bool): Si es True, la llamada es bloqueante.
    #
    # Returns:
    #   Optional[qi.Future]: El futuro de la tarea de animacion si no es bloqueante.
    def play_local_animation_by_category(self, category_name: str,
                                         specific_name_no_ext: Optional[str] = None,
                                         wait: bool = False) -> Optional[qi.Future]:
        if not self.can_run_local_anims:
            log.error(f"ASC: No se puede ejecutar animacion local. Los modulos Actuation no estan disponibles."); return None

        path_to_run = None
        # Construccion de logs para depuracion
        log_prefix = f"ASC: play_local (Categoria: '{category_name}'"
        log_detail = f", Nombre: '{specific_name_no_ext if specific_name_no_ext else '[ALEATORIO]'}'"
        log_suffix = f", Bloqueante: {wait})"

        if category_name in self.local_anims_by_category:
            anims_in_cat = self.local_anims_by_category[category_name]
            if not anims_in_cat:
                log.warning(f"{log_prefix}{log_detail}{log_suffix} - La categoria '{category_name}' esta vacia.")
            elif specific_name_no_ext: # Si se busca una animacion especifica
                target_file_lower = f"{specific_name_no_ext}.qianim".lower()
                path_to_run = next((p for p in anims_in_cat if os.path.basename(p).lower() == target_file_lower), None)
                if not path_to_run: log.warning(f"{log_prefix}{log_detail}{log_suffix} - La animacion especifica no fue encontrada.")
                else: log.info(f"{log_prefix}{log_detail}{log_suffix} - Animacion encontrada: '{os.path.basename(path_to_run)}'")
            elif anims_in_cat: # Si no se especifica nombre, elegir una aleatoria
                path_to_run = random.choice(anims_in_cat)
                log.info(f"{log_prefix}{log_detail}{log_suffix} - Animacion seleccionada aleatoriamente: '{os.path.basename(path_to_run)}'")
        else: # Si la categoria no existe
            log.warning(f"{log_prefix}{log_detail}{log_suffix} - La categoria '{category_name}' no fue encontrada.")

        if path_to_run:
            future_anim = self._execute_local_qianim_experimental(path_to_run)
            if future_anim and wait: # Si la ejecucion es bloqueante
                anim_name_log = os.path.basename(path_to_run)
                log.info(f"ASC: Esperando finalizacion de animacion .qianim '{anim_name_log}'...")
                try:
                    future_anim.value(timeout=20000) # Espera con timeout
                    log.info(f"ASC: Animacion .qianim '{anim_name_log}' completada (sincrono).")
                except RuntimeError: log.warning(f"ASC: Timeout o error esperando por animacion .qianim '{anim_name_log}'.")
                except Exception as e: log.error(f"ASC: Excepcion esperando por animacion .qianim '{anim_name_log}': {e}", exc_info=True)
            return future_anim
        return None

    # Ejecuta una animacion estandar del sistema del robot utilizando su 'tag' de comportamiento.
    # Usa el servicio ALAnimationPlayer.
    #
    # Args:
    #   tag_name (str): El tag de la animacion a ejecutar (ej. "animations/Stand/Gestures/Bow_1").
    #   wait (bool): Si es True, la llamada es bloqueante.
    #
    # Returns:
    #   Optional[qi.Future]: El futuro de la tarea de animacion si no es bloqueante.
    def play_standard_animation_by_tag(self, tag_name: str, wait: bool = False) -> Optional[qi.Future]:
        if not self.can_run_standard_tags or not self.anim_player:
            log.error("ASC: El proxy a ALAnimationPlayer no esta disponible. No se puede ejecutar el tag estandar."); return None
        log.info(f"ASC: Ejecutando tag de animacion estandar: '{tag_name}' (Bloqueante: {wait})")
        try:
            if not self.motion.robotIsWakeUp():
                log.info("ASC: El robot no esta despierto. Ejecutando wakeUp()..."); self.motion.wakeUp(); time.sleep(0.5)
            if wait: # Ejecucion sincrona
                self.anim_player.runTag(tag_name); log.info(f"ASC: Tag estandar '{tag_name}' completado (sincrono)."); return None
            else: # Ejecucion asincrona
                future_anim = self.anim_player.runTag(tag_name, _async=True); log.info(f"ASC: Tag estandar '{tag_name}' iniciado (asincrono)."); return future_anim
        except RuntimeError as e_rt: log.error(f"ASC: RuntimeError ejecutando tag estandar '{tag_name}': {e_rt}", exc_info=True)
        except Exception as e: log.error(f"ASC: Excepcion desconocida ejecutando tag estandar '{tag_name}': {e}", exc_info=True)
        return None

    # Ejecuta una secuencia de acciones (habla, animaciones locales, animaciones estandar).
    # Permite crear comportamientos complejos y guionizados de forma sencilla.
    #
    # Args:
    #   sequence_segments (List[Dict[str, Any]]): Una lista de diccionarios, donde cada
    #                                            uno define un segmento de la secuencia (tipo, parametros, etc.).
    def play_sequence(self, sequence_segments: List[Dict[str, Any]]):
        log.info(f"ASC: Procesando secuencia de comportamiento con {len(sequence_segments)} segmentos...")
        if not isinstance(sequence_segments, list): log.error("ASC: El parametro 'sequence_segments' debe ser una lista."); return
        if not self.motion.robotIsWakeUp(): log.info("ASC: Despertando al robot para iniciar la secuencia..."); self.motion.wakeUp(); time.sleep(0.5)
        for i, segment in enumerate(sequence_segments):
            if not isinstance(segment, dict): log.warning(f"ASC: El segmento {i+1} de la secuencia no es un diccionario, se omitira."); continue
            seg_type = segment.get("type"); should_wait = segment.get("wait", False)
            # Por defecto, el habla en una secuencia es bloqueante a menos que se especifique lo contrario
            if seg_type == "speak_standard" and "wait" not in segment: should_wait = True
            log.info(f"ASC: Ejecutando segmento de secuencia {i+1}: Tipo='{seg_type}', Esperar={should_wait}")
            future_action: Optional[qi.Future] = None
            # Delega la accion al metodo correspondiente segun el tipo de segmento
            if seg_type == "speak_standard": future_action = self.say_text_with_embedded_standard_animations(segment.get("text", ""), wait_for_speech=False)
            elif seg_type == "local_anim": future_action = self.play_local_animation_by_category(segment.get("category"), segment.get("name_no_ext"), wait=False)
            elif seg_type == "standard_tag_anim": future_action = self.play_standard_animation_by_tag(segment.get("tag"), wait=False)
            else: log.warning(f"ASC: Tipo de segmento de secuencia desconocido: '{seg_type}'")
            # Si se debe esperar a que la accion termine
            if future_action and should_wait:
                timeout_val = 60000 if seg_type == "speak_standard" else 20000 # Timeout mas largo para el habla
                log.info(f"ASC: Esperando finalizacion del segmento '{seg_type}' (max {timeout_val/1000}s)...")
                try: future_action.value(timeout=timeout_val); log.info(f"ASC: Segmento '{seg_type}' completado.")
                except RuntimeError: log.warning(f"ASC: Timeout o error esperando por la finalizacion del segmento '{seg_type}'.")
                except Exception as e_wait_seq: log.error(f"ASC: Error esperando por la finalizacion del segmento '{seg_type}': {e_wait_seq}", exc_info=True)
            # Pequena pausa si no se espera, para dar tiempo a que la accion comience
            if i < len(sequence_segments) - 1 and not should_wait: time.sleep(0.05)
        log.info("ASC: Secuencia de comportamiento completada.")

    # Devuelve una lista de las categorias de animaciones locales que fueron descubiertas al inicializar.
    def get_available_local_animation_categories(self) -> List[str]:
        return list(self.local_anims_by_category.keys())

# Bloque de ejecucion principal para pruebas directas del script.
if __name__ == '__main__':
    # Para probar esta clase, debe ser importada en un script que ya tenga una sesion
    # activa con el robot y pueda proporcionar los proxies necesarios a los servicios de Naoqi.
    print("Para probar la clase AnimationSpeechController, importala y usala con una sesion Naoqi activa.")
