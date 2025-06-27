# ----------------------------------------------------------------------------------
# Titular: Manejador de Microfono Local para Captura de Audio
# Funcion Principal: Define la clase LocalMicHandler, responsable de capturar
#                    audio desde un microfono local utilizando la libreria sounddevice.
#                    Incluye funcionalidad para seleccionar el dispositivo,
#                    remuestrear el audio (si es necesario y librosa esta disponible)
#                    a una tasa de muestreo objetivo, convertirlo a mono, y
#                    poner los fragmentos de audio (chunks) en una cola para su
#                    procesamiento posterior (ej. STT).
# ----------------------------------------------------------------------------------

import sounddevice as sd
import threading
import queue
import numpy as np
import time
import sys
import traceback
import logging
from typing import Optional, Callable, Dict, Any, List, Union

# Configuracion del logger para este modulo
log = logging.getLogger("MicLocal")

# Intenta importar librosa para remuestreo de alta calidad; opcional.
try:
    import librosa
    log.info("librosa encontrado (para resampling de alta calidad).")
except ImportError:
    log.warning("librosa no encontrado. El remuestreo no sera posible si las tasas de muestreo difieren y se requiere.")
    librosa = None # Define librosa como None si no se encuentra

# Constantes para la reconexion del microfono
DEFAULT_RECONNECT_INTERVAL_SEC = 5 # Intervalo en segundos entre intentos de reconexion
DEFAULT_RECONNECT_ATTEMPTS = 3     # Numero de intentos de reconexion por defecto

# Gestiona la captura de audio desde un microfono local del sistema.
# Procesa el audio en hilos separados para captura y remuestreo/conversion,
# entregando chunks de audio mono en formato int16 listos para STT.
class LocalMicHandler:
    # Inicializa el LocalMicHandler con los parametros de configuracion.
    #
    # Args:
    #   mic_name_part (str): Parte del nombre del microfono a buscar o "default".
    #   target_sample_rate (int): Tasa de muestreo deseada para el audio de salida.
    #   preferred_capture_sr (Optional[int]): Tasa de muestreo preferida para la captura inicial.
    #   channels (int): Numero de canales deseados para la salida (generalmente 1 para mono).
    #   queue_size (int): Tamano maximo de la cola de audio procesado (listo para STT).
    #   raw_queue_size (int): Tamano maximo de la cola de audio crudo (capturado).
    #   reconnect_attempts (int): Numero de intentos para encontrar/conectar al microfono.
    #   reconnect_interval_sec (int): Intervalo en segundos entre intentos de reconexion.
    def __init__(self, mic_name_part="default",
                 target_sample_rate=16000,
                 preferred_capture_sr=None,
                 channels=1,
                 queue_size=50,
                 raw_queue_size=100,
                 reconnect_attempts=DEFAULT_RECONNECT_ATTEMPTS,
                 reconnect_interval_sec=DEFAULT_RECONNECT_INTERVAL_SEC):

        self._configured_mic_name_part = mic_name_part # Nombre o parte del nombre del microfono a usar
        self.target_sample_rate = target_sample_rate     # Tasa de muestreo final del audio
        self.preferred_capture_sr = preferred_capture_sr # Tasa de muestreo preferida para la captura
        self.channels_to_capture = channels              # Canales a capturar (usualmente 1 para mono)

        # Colas para manejar el flujo de audio entre hilos
        self._raw_audio_queue = queue.Queue(maxsize=raw_queue_size) # Audio crudo desde el microfono
        self.audio_queue = queue.Queue(maxsize=queue_size)         # Audio procesado (mono, int16, remuestreado)

        # Configuracion de reintentos de conexion
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_interval_sec = reconnect_interval_sec

        # Atributos de estado del microfono y captura
        self.mic_index: Optional[int] = None             # Indice del dispositivo de microfono seleccionado
        self.mic_name: str = "Not Initialized"           # Nombre del microfono seleccionado
        self.actual_capture_sr: Optional[int] = None     # Tasa de muestreo real de la captura
        self.actual_capture_channels: Optional[int] = None # Canales reales de la captura

        # Eventos y banderas para control de hilos
        self._stop_event = threading.Event()          # Senal para detener todos los hilos
        self._capture_active_flag = threading.Event() # Indica si el stream de captura esta activo
        self.capture_stream: Optional[sd.InputStream] = None # Objeto del stream de sounddevice
        self._capture_thread: Optional[threading.Thread] = None # Hilo para ejecutar _run_capture_thread
        self._resample_thread: Optional[threading.Thread] = None # Hilo para ejecutar _resample_and_process_thread_func
        self._is_shutting_down_for_test: bool = False # Bandera especial para pruebas

        log.info(f"Instancia de LocalMicHandler creada. Mic a buscar: '{mic_name_part}', Salida SR: {target_sample_rate}Hz, Captura Pref SR: {preferred_capture_sr or 'No espec.'}Hz")

    # Verifica si un dispositivo de audio soporta una tasa de muestreo y numero de canales especificos.
    # Utiliza sd.check_input_settings para la verificacion.
    #
    # Args:
    #   device_index (Optional[int]): Indice del dispositivo de audio.
    #   sample_rate (Optional[int]): Tasa de muestreo a verificar.
    #   channels (int): Numero de canales a verificar.
    #
    # Returns:
    #   bool: True si la configuracion es soportada, False en caso contrario.
    def _check_sr_support(self, device_index: Optional[int], sample_rate: Optional[int], channels: int) -> bool:
        if device_index is None or sample_rate is None: return False
        try:
            # sounddevice verifica si la combinacion de dispositivo, tasa y canales es valida para entrada
            sd.check_input_settings(device=device_index, samplerate=int(sample_rate), channels=channels)
            return True
        except Exception: # Si check_input_settings lanza una excepcion, no es soportado
            return False

    # Busca un dispositivo de entrada de audio que coincida con el nombre configurado
    # (o el por defecto del sistema) y determina la tasa de muestreo optima para la captura.
    # Configura los atributos internos mic_index, mic_name y actual_capture_sr.
    #
    # Returns:
    #   bool: True si se encontro y configuro un dispositivo adecuado, False en caso contrario.
    def _find_and_configure_device_params(self) -> bool:
        log.debug(f"Buscando dispositivo de audio que contenga '{self._configured_mic_name_part}'...")
        self.mic_index = None
        self.mic_name = "Not Found"
        self.actual_capture_sr = None
        self.actual_capture_channels = None
        found_device_info = None # Para almacenar la informacion del dispositivo encontrado

        try:
            devices = sd.query_devices() # Obtiene la lista de todos los dispositivos de audio
            # Logica para seleccionar el microfono basado en el nombre configurado
            if self._configured_mic_name_part.lower() != "default":
                best_match_so_far = None
                for index, dev_details in enumerate(devices):
                    if dev_details.get('max_input_channels', 0) >= self.channels_to_capture:
                        dev_name_lower = dev_details.get('name', '').lower()
                        conf_name_lower = self._configured_mic_name_part.lower()
                        if conf_name_lower == dev_name_lower: # Coincidencia exacta del nombre
                            best_match_so_far = {'index': index, 'device': dev_details, 'match': 'Exact Name'}
                            break
                        elif conf_name_lower in dev_name_lower: # Coincidencia parcial del nombre
                            if best_match_so_far is None: # Prioriza la primera coincidencia parcial
                                best_match_so_far = {'index': index, 'device': dev_details, 'match': 'Partial Name'}
                if best_match_so_far: found_device_info = best_match_so_far
                else:
                    log.warning(f"No se encontro microfono por nombre especifico: '{self._configured_mic_name_part}'.")
                    return False
            # Logica para seleccionar el microfono "default"
            elif self._configured_mic_name_part.lower() == "default":
                log.debug("Buscando microfono 'default' (HostAPI o System)...")
                # Intenta encontrar el default de la HostAPI
                for index, dev_details in enumerate(devices):
                    if dev_details.get('max_input_channels', 0) >= self.channels_to_capture:
                        try:
                            hostapi_info = sd.query_hostapis(dev_details['hostapi'])
                            if hostapi_info and index == hostapi_info.get('default_input_device', -1):
                                found_device_info = {'index': index, 'device': dev_details, 'match': 'HostAPI Default'}
                                break
                        except Exception: pass # Ignora errores si no se puede consultar HostAPI
                # Si no se encontro por HostAPI, intenta el default del sistema
                if not found_device_info:
                    try:
                        default_idx = sd.default.device[0] # Indice del dispositivo de entrada por defecto
                        if default_idx != -1:
                            dev_details_dict = sd.query_devices(default_idx, 'input')
                            # Normaliza dev_details_dict si es necesario (sounddevice puede devolver objetos o dicts)
                            if not isinstance(dev_details_dict, dict):
                                if hasattr(dev_details_dict, 'name') and hasattr(dev_details_dict, 'max_input_channels'):
                                    dev_details_dict = {
                                        'name': dev_details_dict.name,
                                        'max_input_channels': dev_details_dict.max_input_channels,
                                        'default_samplerate': dev_details_dict.default_samplerate,
                                        'hostapi': dev_details_dict.hostapi}
                                else: dev_details_dict = {} # Si no tiene atributos esperados, objeto vacio
                            if dev_details_dict.get('max_input_channels',0) >= self.channels_to_capture:
                                found_device_info = {'index': default_idx, 'device': dev_details_dict, 'match': 'System Default'}
                    except Exception: pass # Ignora errores al obtener el default del sistema

            if found_device_info:
                self.mic_index = found_device_info['index']
                self.mic_name = found_device_info['device']['name']
                # Obtiene informacion completa del dispositivo seleccionado para la tasa de muestreo por defecto
                full_dev_info = sd.query_devices(self.mic_index, 'input')
                if not isinstance(full_dev_info, dict) and hasattr(full_dev_info, 'default_samplerate'):
                    full_dev_info = {'default_samplerate': full_dev_info.default_samplerate}
                elif not isinstance(full_dev_info, dict): full_dev_info = {}
                dev_default_sr = int(full_dev_info.get('default_samplerate', self.target_sample_rate))

                log.info(f"Dispositivo seleccionado ({found_device_info['match']}): '{self.mic_name}' (Idx: {self.mic_index}, DefaultSR: {dev_default_sr}Hz)")
                # Determina la tasa de muestreo para la captura, probando opciones en orden de preferencia
                sr_options_to_try = []
                if self.preferred_capture_sr: sr_options_to_try.append(self.preferred_capture_sr)
                if self.target_sample_rate not in sr_options_to_try:
                    if self.preferred_capture_sr and self.preferred_capture_sr != self.target_sample_rate:
                        sr_options_to_try.insert(1, self.target_sample_rate)
                    elif not self.preferred_capture_sr:
                        sr_options_to_try.append(self.target_sample_rate)
                if dev_default_sr not in sr_options_to_try: sr_options_to_try.append(dev_default_sr)
                # Anade tasas comunes como fallback
                if 48000 not in sr_options_to_try: sr_options_to_try.append(48000)
                if 44100 not in sr_options_to_try: sr_options_to_try.append(44100)

                log.debug(f"Tasas de muestreo a probar para captura: {sr_options_to_try}")
                for sr_to_try in sr_options_to_try:
                    if self._check_sr_support(self.mic_index, sr_to_try, self.channels_to_capture):
                        self.actual_capture_sr = sr_to_try
                        log.info(f"Tasa de captura {self.actual_capture_sr}Hz soportada y seleccionada.")
                        break
                if self.actual_capture_sr is None: # Si ninguna tasa probada es soportada
                    self.actual_capture_sr = dev_default_sr # Usa la tasa por defecto del dispositivo como ultimo recurso
                    log.warning(f"No se pudo confirmar soporte para tasas comunes/preferidas. Usando SR por defecto del dispositivo: {self.actual_capture_sr} Hz.")
                if self.actual_capture_sr is None: # Fallo critico si aun es None
                    log.error("Fallo critico: actual_capture_sr sigue siendo None despues de todos los intentos.")
                    return False

                log.info(f"Tasa de captura final establecida a: {self.actual_capture_sr} Hz.")
                # Verifica si se necesita remuestreo y si librosa esta disponible
                if self.actual_capture_sr != self.target_sample_rate and not librosa:
                    log.critical("Se requiere remuestreo (captura SR != target SR) pero librosa NO esta disponible. El STT podria fallar.")
                return True
            else:
                log.warning(f"No se encontro microfono para '{self._configured_mic_name_part}'.")
                self.mic_name = "Not Found"
                self.actual_capture_sr = None
                return False
        except Exception as e:
            log.error(f"Excepcion buscando dispositivo: {e}", exc_info=True)
            self.mic_name = "Error"
            self.actual_capture_sr = None
            return False

    # Funcion callback invocada por sounddevice.InputStream cada vez que hay nuevos datos de audio.
    # Coloca los datos de audio crudo (indata) en una cola para su procesamiento posterior.
    # Tambien registra advertencias o errores si ocurren problemas en el stream (ej. overflow).
    #
    # Args:
    #   indata (np.ndarray): Array NumPy con los datos de audio del microfono.
    #   frames (int): Numero de frames en indata.
    #   time_info (Any): Informacion de tiempo proporcionada por sounddevice.
    #   status (sd.CallbackFlags): Banderas de estado del stream de audio.
    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags):
        if status: # Verifica si hay algun estado reportado por el stream
            if status.input_overflow: # Si se detecta un sobreflujo de datos de entrada
                log.error("¡¡¡SOBREFLUJO DE ENTRADA DETECTADO EN EL STREAM DE AUDIO!!! Se estan perdiendo datos.")
        if not self._capture_active_flag.is_set(): return # No procesar si la captura no esta activa
        try:
            # Coloca una copia de los datos crudos en la cola para el hilo de procesamiento
            self._raw_audio_queue.put_nowait(indata.copy())
        except queue.Full: log.debug("Cola de audio crudo (_raw_audio_queue) llena, descartando frame.")
        except Exception as e_cb: log.error(f"Error en _audio_callback: {e_cb}", exc_info=True)

    # Funcion ejecutada en un hilo separado para procesar el audio crudo.
    # Toma datos de _raw_audio_queue, los convierte a mono, los remuestrea si es
    # necesario (usando librosa si esta disponible) a target_sample_rate,
    # los convierte a formato int16 y los pone en self.audio_queue.
    def _resample_and_process_thread_func(self):
        log.info("Hilo de remuestreo y procesamiento iniciado.")
        while not self._stop_event.is_set() or not self._raw_audio_queue.empty():
            try:
                # Obtiene audio crudo de la cola (bloqueante con timeout)
                raw_audio_f32_chunk = self._raw_audio_queue.get(block=True, timeout=0.1)
                if raw_audio_f32_chunk is None: # Senal de finalizacion para el hilo
                    log.debug("Hilo de remuestreo recibio None (senal de terminacion), finalizando.")
                    break
                if not isinstance(raw_audio_f32_chunk, np.ndarray): # Verificacion de tipo
                    log.warning(f"Hilo de remuestreo recibio tipo inesperado: {type(raw_audio_f32_chunk)}")
                    continue

                processed_audio_f32 = raw_audio_f32_chunk
                # Convierte a mono si la captura es multicanal
                if self.actual_capture_channels is not None and self.actual_capture_channels > 1:
                    processed_audio_f32 = np.mean(raw_audio_f32_chunk, axis=1) # Promedia los canales
                elif self.actual_capture_channels == 1 and len(raw_audio_f32_chunk.shape) > 1:
                    processed_audio_f32 = raw_audio_f32_chunk.flatten() # Aplana si es [N,1]

                # Remuestrea si la tasa de captura es diferente a la tasa objetivo y librosa esta disponible
                if self.actual_capture_sr is not None and self.actual_capture_sr != self.target_sample_rate:
                    if librosa:
                        processed_audio_f32 = librosa.resample(
                            y=processed_audio_f32.astype(np.float32),
                            orig_sr=self.actual_capture_sr,
                            target_sr=self.target_sample_rate
                        )
                    else: # Si no hay librosa, no se puede remuestrear; el log critico ya se emitio antes.
                        pass

                # Convierte de float32 a int16 (formato comun para STT)
                final_audio_i16 = (processed_audio_f32 * 32767.0).astype(np.int16)
                # Coloca el audio procesado en la cola de salida
                self.audio_queue.put_nowait(final_audio_i16.tobytes())
            except queue.Empty: # Timeout esperando en _raw_audio_queue.get()
                if self._stop_event.is_set(): break # Si se pide detener, salir del bucle
                continue
            except queue.Full: log.warning("Cola de audio procesado (self.audio_queue) llena. Descartando chunk.")
            except AttributeError as ae: # Ocurre si raw_audio_f32_chunk es None y se intenta acceder a un atributo
                log.error(f"Error de atributo en hilo de remuestreo (probablemente raw_audio_f32_chunk es None o inesperado): {ae}")
                if self._stop_event.is_set(): break
            except Exception as e: log.error(f"Error en hilo de remuestreo: {e}", exc_info=True)
        log.info("Hilo de remuestreo y procesamiento finalizado.")

    # Funcion ejecutada en un hilo separado que maneja el stream de captura de audio.
    # Configura y abre un sounddevice.InputStream con el microfono y la tasa de
    # muestreo seleccionados, utilizando _audio_callback para recibir los datos.
    # Mantiene el stream activo hasta que se senala la detencion.
    def _run_capture_thread(self):
        thread_name = f"MicCapture-{self.mic_name[:15].replace(' ', '_')}" # Nombre descriptivo para el hilo
        log.info(f"Hilo de captura '{thread_name}' iniciando para '{self.mic_name}'...")
        if self.mic_index is None or self.actual_capture_sr is None:
            log.error(f"Hilo '{thread_name}': Configuracion de microfono invalida (mic_index o actual_capture_sr es None). Abortando hilo.")
            self._capture_active_flag.clear()
            return
        try:
            log.info(f"Abriendo InputStream: Idx={self.mic_index}, SR={self.actual_capture_sr}Hz, ChReq={self.channels_to_capture}")
            # Crea y abre el stream de entrada de audio
            self.capture_stream = sd.InputStream(
                samplerate=self.actual_capture_sr, device=self.mic_index,
                channels=self.channels_to_capture, dtype='float32', # Captura como float32
                callback=self._audio_callback,       # Funcion a llamar con nuevos datos
                blocksize=int(self.actual_capture_sr * 0.05) # Tamano del bloque (ej. 50ms)
            )
            self.actual_capture_channels = self.capture_stream.channels # Canales reales del stream
            log.info(f"Stream de audio abierto. Canales de captura reales: {self.actual_capture_channels}.")
            # Usa el stream como context manager para asegurar que se cierre correctamente
            with self.capture_stream:
                self._capture_active_flag.set() # Indica que la captura esta activa
                log.info(f"Captura de audio activa para '{self.mic_name}'. Esperando evento de parada (_stop_event)...")
                self._stop_event.wait() # Mantiene el hilo activo hasta que se senale _stop_event
            log.info(f"Stream de audio para '{self.mic_name}' detenido (saliendo del 'with self.capture_stream').")
        except Exception as e_stream:
            log.error(f"Fallo CRITICO en stream de audio para '{self.mic_name}': {e_stream}", exc_info=True)
        finally: # Bloque de limpieza para el hilo de captura
            self._capture_active_flag.clear() # Indica que la captura ya no esta activa
            if self.capture_stream and not self.capture_stream.closed:
                try:
                    self.capture_stream.abort(ignore_errors=True) # Intenta abortar el stream
                    self.capture_stream.close(ignore_errors=True) # Intenta cerrar el stream
                except Exception as e_close: log.warning(f"Error menor cerrando stream de audio: {e_close}")
            self.capture_stream = None # Limpia la referencia al stream
            log.info(f"Hilo de captura '{thread_name}' finalizado.")

    # Inicia el proceso de captura de audio.
    # Realiza intentos para encontrar y configurar el microfono, y si tiene exito,
    # inicia los hilos de captura (_run_capture_thread) y de procesamiento/remuestreo
    # (_resample_and_process_thread_func).
    #
    # Returns:
    #   bool: True si la captura se inicio correctamente, False en caso contrario.
    def start_capture(self) -> bool:
        if self._capture_active_flag.is_set() or \
           (self._capture_thread and self._capture_thread.is_alive()) or \
           (self._resample_thread and self._resample_thread.is_alive()):
            log.warning("start_capture llamado, pero la captura o el procesamiento de audio ya estan activos.")
            return True # Considera exito si ya esta activo

        self._stop_event.clear() # Resetea el evento de parada para permitir un nuevo inicio
        attempts_made = 0
        max_attempts = self.reconnect_attempts if self.reconnect_attempts > 0 else float('inf') # Permite intentos infinitos si es 0 o negativo

        # Bucle de intentos para iniciar la captura
        while not self._stop_event.is_set() and attempts_made < max_attempts:
            attempts_made += 1
            log.info(f"Intento de inicio de captura #{attempts_made} (Buscando microfono: '{self._configured_mic_name_part}')...")
            if not self._find_and_configure_device_params(): # Intenta encontrar y configurar el microfono
                if self._stop_event.is_set(): break # Si se solicito parada global, salir
                if attempts_made < max_attempts:
                    log.warning(f"Microfono '{self._configured_mic_name_part}' no encontrado en intento #{attempts_made}. Reintentando en {self.reconnect_interval_sec}s...")
                    self._stop_event.wait(self.reconnect_interval_sec) # Espera antes de reintentar
                continue # Vuelve al inicio del bucle para el proximo intento

            if self.mic_index is None or self.actual_capture_sr is None: # Doble verificacion de configuracion
                log.error(f"Configuracion de microfono fallida en intento #{attempts_made} (mic_index o actual_capture_sr es None). Reintentando...")
                if self._stop_event.is_set(): break
                if attempts_made < max_attempts:
                    self._stop_event.wait(self.reconnect_interval_sec)
                continue

            # Inicia el hilo de procesamiento y remuestreo
            self._resample_thread = threading.Thread(target=self._resample_and_process_thread_func, name="MicResampleProc")
            self._resample_thread.daemon = True
            self._resample_thread.start()

            self._capture_active_flag.clear() # Asegura que la bandera este limpia antes de iniciar el hilo de captura
            # Inicia el hilo de captura de audio
            self._capture_thread = threading.Thread(target=self._run_capture_thread, name=f"MicRun-{self.mic_name[:10]}")
            self._capture_thread.daemon = True
            self._capture_thread.start()

            # Espera a que el hilo de captura senale que el stream esta activo
            activated = self._capture_active_flag.wait(timeout=5.0) # Timeout de 5 segundos
            if activated:
                log.info(f"Captura iniciada y stream activo para '{self.mic_name}'. Hilo de remuestreo tambien activo.")
                return True # Exito al iniciar la captura
            else: # Si el stream no se activo a tiempo
                log.error(f"Fallo al activar stream para '{self.mic_name}' en intento #{attempts_made} (timeout o hilo de captura fallo).")
                self._stop_event.set() # Senala a los hilos que se detengan
                # Intenta hacer join a los hilos para limpieza
                if self._capture_thread and self._capture_thread.is_alive():
                    self._capture_thread.join(timeout=1.0)
                if self._resample_thread and self._resample_thread.is_alive():
                    try: self._raw_audio_queue.put_nowait(None) # Envia senal de fin al hilo de resample
                    except queue.Full: pass
                    self._resample_thread.join(timeout=1.0)

                if self._stop_event.is_set() and attempts_made >= max_attempts:
                    break # Salir del bucle si se alcanzaron maximos intentos y hay parada global
                elif attempts_made < max_attempts: # Si aun quedan intentos
                    self._stop_event.clear() # Permite el proximo intento
                    log.info(f"Esperando {self.reconnect_interval_sec}s antes del proximo intento de inicio de captura...")
                    self._stop_event.wait(self.reconnect_interval_sec)
        # Si sale del bucle sin exito
        log.error(f"No se pudo iniciar la captura para '{self._configured_mic_name_part}' despues de {attempts_made} intento(s) o por parada global.")
        return False

    # Detiene el proceso de captura de audio de forma controlada.
    # Senala a los hilos de captura y procesamiento que deben detenerse, espera
    # a que finalicen (join) y limpia las colas de audio.
    def stop_capture(self):
        log.info("Solicitando detencion de captura de audio local...")
        self._stop_event.set() # Senala a todos los hilos que deben parar
        self._capture_active_flag.clear() # Indica que la captura ya no debe considerarse activa

        # Espera a que el hilo de captura finalice
        if self._capture_thread and self._capture_thread.is_alive():
            log.debug("Esperando finalizacion del hilo de captura (_capture_thread)...")
            self._capture_thread.join(timeout=2.5) # Espera con timeout
            if self._capture_thread.is_alive(): log.warning("Hilo de captura (_capture_thread) no finalizo a tiempo.")
        self._capture_thread = None

        # Espera a que el hilo de procesamiento/remuestreo finalice
        if self._resample_thread and self._resample_thread.is_alive():
            log.debug("Esperando finalizacion del hilo de remuestreo (_resample_thread)...")
            try: self._raw_audio_queue.put_nowait(None) # Envia senal de fin al hilo si esta esperando en la cola
            except queue.Full: pass # Ignora si la cola esta llena
            self._resample_thread.join(timeout=2.5) # Espera con timeout
            if self._resample_thread.is_alive(): log.warning("Hilo de remuestreo (_resample_thread) no finalizo a tiempo.")
        self._resample_thread = None

        # Limpia cualquier dato restante en las colas de audio
        log.debug("Limpiando colas de audio (_raw_audio_queue, self.audio_queue)...")
        for q_to_clear in [self._raw_audio_queue, self.audio_queue]:
            while not q_to_clear.empty():
                try: q_to_clear.get_nowait() # Vacia la cola
                except queue.Empty: break # Sale si la cola ya esta vacia
        log.info("Captura de audio local detenida y colas limpiadas.")

    # Obtiene un fragmento (chunk) de audio procesado (mono, int16, remuestreado)
    # de la cola de salida.
    #
    # Args:
    #   block (bool): Si es True, la llamada se bloquea hasta que haya un item disponible
    #                 o se alcance el timeout.
    #   timeout (float): Tiempo maximo en segundos para esperar un item si block es True.
    #
    # Returns:
    #   Optional[bytes]: El fragmento de audio como bytes, o None si la cola esta vacia
    #                    (y no se bloqueo o se alcanzo el timeout) o si la captura no esta activa.
    def get_mono_chunk(self, block=True, timeout=0.1) -> Optional[bytes]:
        try:
            # Solo intenta obtener de la cola si la captura esta activa o si es para una prueba durante el apagado
            if not self._is_running_or_has_data_for_test():
                return None
            return self.audio_queue.get(block=block, timeout=timeout)
        except queue.Empty: # Si la cola esta vacia (y no se bloqueo o se alcanzo el timeout)
            return None

    # Metodo auxiliar, principalmente para pruebas, que verifica si la captura
    # esta activa o si aun hay datos en la cola durante el apagado para pruebas.
    #
    # Returns:
    #   bool: True si esta activo o hay datos para pruebas, False en caso contrario.
    def _is_running_or_has_data_for_test(self) -> bool:
        if self._capture_active_flag.is_set(): return True # Si la captura esta marcada como activa
        # Si es una prueba y se esta apagando, permite consumir datos restantes en la cola
        if getattr(self, '_is_shutting_down_for_test', False) and not self.audio_queue.empty(): return True
        return False

    # Devuelve un diccionario con informacion sobre el estado actual del microfono
    # y los hilos de captura y procesamiento.
    #
    # Returns:
    #   dict: Diccionario con detalles como nombre del microfono, tasas de muestreo,
    #         estado de los hilos, etc.
    def get_mic_info(self) -> dict:
        return {
            "target_mic_name": self._configured_mic_name_part,
            "found_mic_name": self.mic_name if self.mic_index is not None else "N/A",
            "capture_sr_actual": self.actual_capture_sr,
            "output_sr_target": self.target_sample_rate,
            "is_stream_active": self._capture_active_flag.is_set(),
            "is_capture_thread_alive": self._capture_thread.is_alive() if self._capture_thread else False,
            "is_resample_thread_alive": self._resample_thread.is_alive() if self._resample_thread else False
        }

    # Metodo para realizar un apagado completo y ordenado del LocalMicHandler.
    # Llama a stop_capture() para asegurar que todos los recursos se liberen.
    def shutdown(self):
        log.info("Solicitando SHUTDOWN de LocalMicHandler...")
        self.stop_capture() # Llama al metodo de detencion para limpieza
        log.info("LocalMicHandler SHUTDOWN completo.")

# --- Bloque de Prueba Standalone ---
# Este bloque se ejecuta solo si el script es llamado directamente.
# Sirve para probar la funcionalidad de LocalMicHandler en conjunto con VoskHelper.
if __name__ == "__main__":
    # Configuracion basica de logging para la prueba
    logging.basicConfig(
        level=logging.INFO, # Nivel de log general (INFO para reducir verbosidad)
        format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    test_log = logging.getLogger("MicLocal_VoskTest") # Logger especifico para esta prueba
    # Para habilitar logs DEBUG especificamente para este script de prueba y MicLocal:
    # test_log.setLevel(logging.DEBUG)
    # logging.getLogger("MicLocal").setLevel(logging.DEBUG)

    all_recognized_segments = [] # Lista para almacenar segmentos reconocidos

    test_log.info("Dispositivos de audio disponibles (sounddevice):")
    try:
        print(sd.query_devices()) # Muestra los dispositivos disponibles
    except Exception as e_qd:
        test_log.error(f"No se pudo listar dispositivos de audio: {e_qd}")
    test_log.info("-" * 30)

    try: # Intenta importar VoskHelper para la prueba completa
        from vosk_helper import VoskHelper
    except ImportError:
        test_log.critical("No se pudo importar VoskHelper. Asegurate que vosk_helper.py este en el directorio 'audio'.")
        sys.exit(1)

    # Define la ruta al modelo Vosk para la prueba
    script_dir = os.path.dirname(os.path.abspath(__file__))
    VOSK_MODEL_PATH_FOR_TEST = os.path.join(script_dir, "models", "vosk-model-small-es-0.42") # Modelo pequenno para pruebas

    if not os.path.isdir(VOSK_MODEL_PATH_FOR_TEST):
        test_log.error(f"Ruta del modelo Vosk para prueba NO VALIDA: '{VOSK_MODEL_PATH_FOR_TEST}'")
        sys.exit(1)
    test_log.info(f"Usando modelo Vosk para prueba desde: {VOSK_MODEL_PATH_FOR_TEST}")

    mic_handler_instance = None
    vosk_helper_instance = None

    MIC_TO_TEST = "default" # Nombre o parte del nombre del microfono a probar. "default" para el del sistema.
    test_log.info(f"Intentando usar microfono que contenga: '{MIC_TO_TEST}'")

    try:
        test_log.info("\n--- INICIANDO PRUEBA DE MicLocal CON VoskHelper (CON HILO DE RESAMPLE) ---")

        vosk_helper_instance = VoskHelper(
            model_path=VOSK_MODEL_PATH_FOR_TEST,
            sample_rate=16000, # Tasa de muestreo para Vosk
            vocabulary=None    # Sin vocabulario especifico para esta prueba
        )
        test_log.info("VoskHelper para prueba instanciado.")

        mic_handler_instance = LocalMicHandler(
            mic_name_part=MIC_TO_TEST,
            target_sample_rate=16000,     # Tasa de muestreo de salida deseada
            preferred_capture_sr=48000    # Intenta capturar a 48kHz si es posible, luego remuestrea
        )
        setattr(mic_handler_instance, '_is_shutting_down_for_test', False) # Para logica de get_mono_chunk en test
        test_log.info("LocalMicHandler para prueba instanciado.")

        if mic_handler_instance.start_capture(): # Inicia la captura
            mic_info_running = mic_handler_instance.get_mic_info()
            test_log.info(f"Info del Microfono (despues de start_capture): {mic_info_running}")

            # Verifica si el microfono se configuro correctamente
            if mic_info_running.get("found_mic_name", "Not Found") == "Not Found" or \
               mic_info_running.get("found_mic_name") == "Not Initialized" or \
               mic_info_running.get("actual_capture_sr") is None:
                test_log.error(f"No se pudo encontrar o configurar correctamente el microfono '{MIC_TO_TEST}'. Atributos: {mic_info_running}. Abortando prueba.")
                sys.exit(1)

            # Advertencia si se necesita remuestreo pero librosa no esta
            if mic_info_running.get("actual_capture_sr") != 16000:
                if not librosa:
                    test_log.error(f"CAPTURA A {mic_info_running.get('actual_capture_sr')}Hz PERO LIBROSA NO DISPONIBLE PARA REMUESTREAR A 16000Hz. El STT probablemente fallara.")
                else:
                    test_log.info(f"Capturando a {mic_info_running.get('actual_capture_sr')}Hz. El hilo interno remuestreara a 16000Hz.")

            test_log.info(f"MicLocalHandler iniciado con '{mic_handler_instance.mic_name}'. Habla al microfono durante unos 25 segundos...")

            start_time = time.time()
            duration = 25 # Duracion de la prueba de captura
            last_printed_partial = ""

            # Bucle principal de la prueba: obtiene chunks y los procesa con Vosk
            while time.time() - start_time < duration:
                audio_chunk_16k = mic_handler_instance.get_mono_chunk(timeout=0.05) # Obtiene chunk procesado

                if audio_chunk_16k:
                    is_segment_end = vosk_helper_instance.process_audio_chunk(audio_chunk_16k)

                    partial_text = vosk_helper_instance.get_partial_result()
                    # Muestra resultados parciales si cambian
                    if partial_text and partial_text != last_printed_partial:
                        test_log.info(f"   VOSK PARCIAL: '{partial_text}'")
                        last_printed_partial = partial_text
                    elif not partial_text and last_printed_partial: # Si el parcial se borra
                        test_log.info(f"   VOSK PARCIAL: (limpio despues de '{last_printed_partial}')")
                        last_printed_partial = ""

                    if is_segment_end: # Si Vosk detecta fin de segmento
                        if hasattr(vosk_helper_instance, 'get_current_segment_text'):
                            segment_text = vosk_helper_instance.get_current_segment_text()
                            if segment_text:
                                test_log.info(f"   VOSK SEGMENTO FINALIZADO: '{segment_text}'")
                                all_recognized_segments.append(segment_text)
                        else: # Fallback si el metodo no existe (deberia existir)
                            if last_printed_partial:
                                test_log.info(f"   VOSK SEGMENTO (desde ultimo parcial): '{last_printed_partial}'")
                                all_recognized_segments.append(last_printed_partial)
                        last_printed_partial = "" # Limpia el ultimo parcial despues de un segmento
                else:
                    time.sleep(0.01) # Pequena pausa si no hay audio para no sobrecargar CPU

            test_log.info("Tiempo de prueba de captura finalizado. Obteniendo resultado final de Vosk...")
            final_recognition = vosk_helper_instance.get_final_result() # Obtiene el texto final de Vosk
            test_log.info(f"===> VOSK RESULTADO FINAL COMPLETO: '{final_recognition}'")
            if final_recognition and final_recognition not in all_recognized_segments:
                all_recognized_segments.append(final_recognition)
        else:
            test_log.error(f"Fallo al iniciar LocalMicHandler para prueba con microfono '{MIC_TO_TEST}'.")

    except SystemExit: # Captura SystemExit para no tratarlo como error inesperado
        test_log.info("Prueba abortada.")
    except Exception as e:
        test_log.critical(f"Error durante la prueba standalone de MicLocal con VoskHelper: {e}", exc_info=True)
    finally: # Bloque de limpieza final de la prueba
        if mic_handler_instance:
            test_log.info("Deteniendo LocalMicHandler en la prueba...")
            setattr(mic_handler_instance, '_is_shutting_down_for_test', True) # Para la logica de get_mono_chunk
            mic_handler_instance.shutdown() # Llama al shutdown del manejador de microfono

        test_log.info(f"\nSegmentos/Finales Reconocidos en la Prueba: {all_recognized_segments}")
        test_log.info("--- PRUEBA STANDALONE DE MicLocal CON VoskHelper FINALIZADA ---")
