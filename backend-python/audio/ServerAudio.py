#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Servidor TCP de Audio para Recepcion y Procesamiento
# Funcion Principal: Implementa un servidor TCP multihilo disenado para recibir
#                    flujos de audio crudo (raw PCM) enviados desde un cliente
#                    (presumiblemente el robot, usando una solucion personalizada
#                    basada en PulseAudio debido a dificultades con los servicios
#                    Naoqi para la captura directa de audio).
#                    El servidor convierte los segmentos de audio crudo recibidos a
#                    formato WAV en memoria porque el procesamiento directo de los
#                    datos crudos resulto problematico. Luego, estos datos WAV
#                    son procesados (conversion a mono, remuestreo a 16kHz si es
#                    necesario con librosa) y encolados para su posterior uso por
#                    un sistema de Reconocimiento de Voz (STT). Incluye un
#                    mecanismo de control externo (evento) para permitir o denegar
#                    conexiones de clientes.
# ----------------------------------------------------------------------------------

import socket
import os
import time
import signal
import io
import wave
import numpy as np
import threading
import queue
import traceback
import logging

# Configuracion del logger para este modulo
log = logging.getLogger("ServerAudio")

# --- Librerias Opcionales ---
try:
    import librosa # Para remuestreo de audio de alta calidad
    log.info("librosa encontrado (para resampling de alta calidad).")
except ImportError:
    log.warning("librosa no encontrado. Remuestreo no sera posible si tasas difieren y se requiere.")
    librosa = None # Define librosa como None si no esta disponible
try:
    import sounddevice as sd # Para reproduccion de audio en el demo
    log.info("sounddevice encontrado (para reproduccion en demo).")
except ImportError:
    log.warning("sounddevice no encontrado. Reproduccion en demo desactivada.")
    sd = None # Define sd como None si no esta disponible

# --- Configuracion del Servidor ---
HOST_ESCUCHA = '0.0.0.0' # Escuchar en todas las interfaces de red disponibles
PUERTO_ESCUCHA = 5000    # Puerto en el que el servidor escuchara conexiones
BUFFER_SOCKET = 8192     # Tamano del buffer para la recepcion de datos del socket

# --- Parametros del Audio Entrante (del Cliente/Robot) ---
# ASEGURATE QUE EL CLIENTE DE AUDIO ENVIE CON ESTOS PARAMETROS!!!
INCOMING_SAMPLE_RATE = 16000  # Tasa de muestreo esperada del audio crudo del cliente (en Hz)
INCOMING_CHANNELS = 2         # Numero de canales esperados del audio crudo del cliente
INCOMING_BYTES_PER_SAMPLE = 2 # Bytes por muestra (ej. 2 para PCM s16le, formato de 16 bits)

# --- Parametros de Segmentacion y Procesamiento/Salida ---
SECONDS_PER_CHUNK = 0.5       # Duracion de cada segmento de audio a procesar (en segundos)
TARGET_MONO_SAMPLE_RATE = 16000 # Tasa de muestreo de salida para el sistema STT (Vosk) (en Hz)
OUTPUT_NUMPY_DTYPE = np.int16 # Formato de salida de las muestras de audio (NumPy dtype)

# --- Calculos Derivados (basados en los parametros anteriores) ---
BYTES_PER_FRAME_RAW = INCOMING_CHANNELS * INCOMING_BYTES_PER_SAMPLE # Bytes por cada frame de audio crudo
FRAMES_PER_CHUNK_RAW = int(INCOMING_SAMPLE_RATE * SECONDS_PER_CHUNK) # Frames por cada segmento de audio crudo
BYTES_PER_CHUNK_RAW_SEGMENT = FRAMES_PER_CHUNK_RAW * BYTES_PER_FRAME_RAW # Bytes totales por cada segmento de audio crudo

# --- Colas para Comunicacion Inter-hilo ---
# Cola para segmentos de audio crudo (PCM) recibidos del socket, antes de ser procesados a WAV.
raw_pcm_segment_queue = queue.Queue(maxsize=50)
# Cola para fragmentos de audio ya procesados (mono, int16, listos para STT).
processed_audio_queue = queue.Queue(maxsize=50)

# --- Eventos Globales para Detencion y Control de Conexiones ---
# Evento para senalar la finalizacion global del servidor y todos sus hilos.
terminar_programa_evento = threading.Event()
# Evento para controlar externamente si se aceptan o mantienen conexiones de clientes de audio.
# Por defecto, no se permiten hasta que un modulo externo (ej. Init_System) lo active.
permitir_conexion_audio_robot = threading.Event()
permitir_conexion_audio_robot.clear() # Inicialmente, no permitir conexiones

# Manejador de senales del sistema (ej. SIGINT, SIGTERM) para un apagado ordenado.
# Establece el evento 'terminar_programa_evento' para notificar a los hilos.
def manejador_global_signals(signum, frame):
    global terminar_programa_evento # Accede a la variable global
    signame = signal.Signals(signum).name if hasattr(signal.Signals(signum), 'name') else f'Signal {signum}'
    if not terminar_programa_evento.is_set():
        log.info(f"Senal {signame} recibida. Iniciando apagado global del servidor de audio...")
        terminar_programa_evento.set()
        permitir_conexion_audio_robot.clear() # Importante: dejar de aceptar/mantener conexiones al apagar

# Crea datos WAV en un buffer de memoria a partir de datos PCM crudos.
#
# Args:
#   datos_raw_pcm (bytes): Datos de audio PCM crudo.
#   n_canales (int): Numero de canales.
#   tasa_muestreo (int): Tasa de muestreo en Hz.
#   bytes_ancho_muestra (int): Ancho de muestra en bytes (ej. 2 para 16-bit).
#
# Returns:
#   Optional[bytes]: Bytes de los datos WAV, o None si hay error.
def crear_wav_en_memoria(datos_raw_pcm, n_canales, tasa_muestreo, bytes_ancho_muestra):
    buffer_memoria = io.BytesIO() # Crea un buffer en memoria para escribir los datos WAV
    try:
        with wave.open(buffer_memoria, 'wb') as wf: # Abre el buffer como archivo WAV para escritura
            wf.setnchannels(n_canales)
            wf.setsampwidth(bytes_ancho_muestra)
            wf.setframerate(tasa_muestreo)
            wf.writeframes(datos_raw_pcm) # Escribe los datos PCM crudos
        return buffer_memoria.getvalue() # Devuelve el contenido del buffer (datos WAV)
    except Exception as e:
        log.error(f"Fallo creando WAV en memoria: {e}", exc_info=True)
        return None

# Procesa bytes de datos WAV completos (leidos desde memoria).
# Convierte el audio a mono, lo normaliza a float32, lo remuestrea a la
# tasa objetivo si es necesario (usando librosa si esta disponible),
# y lo convierte al formato de salida NumPy especificado (ej. int16).
#
# Args:
#   wav_bytes_completo (bytes): Contenido completo de un archivo WAV.
#   target_sr_out (int): Tasa de muestreo deseada para la salida.
#   output_dtype_np_out (np.dtype): Tipo de dato NumPy para la salida (ej. np.int16).
#   logs_detallados (bool): Si es True, imprime logs mas detallados del proceso.
#
# Returns:
#   Tuple[Optional[bytes], int]: Tupla con los bytes del audio procesado y la tasa
#                                de muestreo final. None si hay error.
def process_wav_bytes(wav_bytes_completo, target_sr_out, output_dtype_np_out, logs_detallados=False):
    thread_name_short = threading.current_thread().name[:15] # Nombre corto del hilo para logs
    try:
        # Usa BytesIO para tratar los bytes WAV como un archivo en memoria
        with io.BytesIO(wav_bytes_completo) as wav_file_in_memory, \
             wave.open(wav_file_in_memory, 'rb') as wf: # Abre el "archivo" WAV en memoria
            sr_orig, n_ch_orig, sampwidth_orig = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            pcm_data_raw = wf.readframes(wf.getnframes()) # Lee todos los frames de audio
            if logs_detallados: log.debug(f"[{thread_name_short}] WAV Interno: {n_ch_orig}ch, {sr_orig}Hz, {sampwidth_orig*8}-bit")

            # Mapea el ancho de muestra en bytes al tipo de dato NumPy correspondiente
            dtype_in_map = {1: np.int8, 2: np.int16, 4: np.int32}
            if sampwidth_orig not in dtype_in_map:
                log.error(f"[{thread_name_short}] Ancho de muestra WAV no soportado: {sampwidth_orig}"); return None, 0
            samples_interleaved = np.frombuffer(pcm_data_raw, dtype=dtype_in_map[sampwidth_orig]) # Convierte bytes a array NumPy

            # Verifica consistencia de los datos
            if not (samples_interleaved.size > 0 and samples_interleaved.size % n_ch_orig == 0):
                log.error(f"[{thread_name_short}] Datos PCM de WAV inconsistentes (size: {samples_interleaved.size}, ch: {n_ch_orig})."); return None, 0

            # Remodela y convierte a mono float32
            data_all_ch = samples_interleaved.reshape((-1, n_ch_orig)) # Separa canales
            mono_audio_f32 = data_all_ch.astype(np.float32) # Convierte a float32 para procesamiento
            if n_ch_orig > 1: mono_audio_f32 = np.mean(mono_audio_f32, axis=1) # Promedia canales para obtener mono
            else: mono_audio_f32 = mono_audio_f32.flatten() # Asegura que sea 1D si ya es mono

            # Normaliza el audio a rango [-1.0, 1.0] si es de tipo entero
            if np.issubdtype(dtype_in_map[sampwidth_orig], np.integer):
                max_val = np.iinfo(dtype_in_map[sampwidth_orig]).max
                if max_val != 0: mono_audio_f32 /= max_val

            final_audio_f32, sr_actual = mono_audio_f32, sr_orig
            # Remuestrea si es necesario y librosa esta disponible
            if sr_orig != target_sr_out:
                if librosa:
                    final_audio_f32 = librosa.resample(y=mono_audio_f32, orig_sr=sr_orig, target_sr=target_sr_out, res_type='kaiser_fast')
                    sr_actual = target_sr_out
                else: # librosa no disponible, no se puede remuestrear
                    if logs_detallados: log.warning(f"[{thread_name_short}] librosa no disponible. Audio NO remuestreado, se mantiene a {sr_actual}Hz (esperado: {target_sr_out}Hz).")
                    # Si las tasas no coinciden y no hay librosa, el STT podria fallar. Se devuelve con SR original.

            # Escala y convierte al tipo de dato de salida NumPy deseado
            if output_dtype_np_out == np.int16: scale_factor = 32767.0
            elif output_dtype_np_out == np.int8: scale_factor = 127.0
            else: scale_factor = 1.0 # Para float32, sin escalado adicional
            scaled_audio = final_audio_f32 * scale_factor
            return scaled_audio.astype(output_dtype_np_out).tobytes(), sr_actual # Devuelve bytes y tasa final
    except wave.Error as e_wav: log.error(f"[{thread_name_short}] Fallo al leer WAV desde memoria: {e_wav}", exc_info=True)
    except Exception as e_proc: log.error(f"[{thread_name_short}] Fallo procesando bytes WAV: {e_proc}", exc_info=True)
    return None, 0 # Devuelve None en caso de error

# Funcion ejecutada en un hilo para escuchar conexiones TCP entrantes y recibir datos de audio.
# Acepta una conexion a la vez. Lee datos del socket, los segmenta segun
# BYTES_PER_CHUNK_RAW_SEGMENT, y coloca los segmentos en la cola 'raw_q'.
# La aceptacion de conexiones esta controlada por el evento 'control_event'.
def network_receiver_thread_func(stop_event: threading.Event, raw_q: queue.Queue, logs_detallados=False, control_event: threading.Event = permitir_conexion_audio_robot):
    thread_name = threading.current_thread().name
    log.info(f"[{thread_name}] Hilo receptor de red iniciado. Escuchando en {HOST_ESCUCHA}:{PUERTO_ESCUCHA}. Controlado por evento de permiso.")
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Crea socket TCP/IP
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Permite reutilizar la direccion
    conn: Optional[socket.socket] = None # Socket de la conexion con el cliente
    addr = None # Direccion del cliente
    try:
        server_socket.bind((HOST_ESCUCHA, PUERTO_ESCUCHA)) # Enlaza el socket al host y puerto
        server_socket.listen(1) # Permite una conexion en espera
        server_socket.settimeout(0.5) # Timeout para accept(), para no bloquear y poder chequear eventos

        buffer_raw = b'' # Buffer para acumular datos recibidos del socket
        while not stop_event.is_set(): # Bucle principal del hilo, se ejecuta hasta que stop_event se active
            if not control_event.is_set(): # Verifica si se permite la conexion del cliente
                if conn: # Si hay una conexion activa y se desactiva el permiso, cerrarla
                    log.info(f"[{thread_name}] Evento de control de conexion desactivado. Cerrando conexion actual con {addr}.")
                    try: conn.shutdown(socket.SHUT_RDWR) # Intenta un cierre ordenado
                    except: pass
                    conn.close(); conn = None; addr = None; buffer_raw = b''
                    try: raw_q.put(None, timeout=0.1) # Envia senal de fin de stream a la cola raw
                    except queue.Full: log.warning(f"[{thread_name}] Cola raw_q llena al intentar enviar None tras cierre de conexion.")
                time.sleep(0.5) # Espera antes de volver a chequear el evento de control
                continue # Vuelve al inicio del bucle

            # Si se permite la conexion y no hay una activa, intentar aceptar una nueva
            if conn is None:
                try:
                    conn, addr = server_socket.accept() # Acepta nueva conexion (bloqueante con timeout)
                    conn.settimeout(0.5) # Timeout para recv() en la conexion establecida
                    buffer_raw = b'' # Resetea el buffer para la nueva conexion
                    log.info(f"[{thread_name}] Conexion de audio de cliente (robot) aceptada desde {addr}")
                except socket.timeout: continue # Timeout en accept(), normal, reintentar
                except Exception as e: # Error al aceptar conexion
                    if stop_event.is_set(): break # Salir si se esta parando el programa globalmente
                    log.error(f"[{thread_name}] Error aceptando nueva conexion: {e}"); conn=None; time.sleep(1)
                    continue

            # Procesar conexion existente (conn is not None and control_event.is_set())
            try:
                new_data = conn.recv(BUFFER_SOCKET) # Lee datos del socket (bloqueante con timeout)
                if not new_data: # Si recv devuelve 0 bytes, el cliente cerro la conexion
                    log.info(f"[{thread_name}] Cliente (robot) {addr} cerro la conexion.")
                    conn.close(); conn = None; addr = None; buffer_raw = b''
                    try: raw_q.put(None, timeout=0.1) # Envia senal de fin de stream
                    except queue.Full: log.warning(f"[{thread_name}] Cola raw_q llena al intentar enviar None tras cierre de cliente.")
                    continue # Esperar nueva conexion si control_event lo permite

                if logs_detallados: log.debug(f"[{thread_name}] Socket recv {len(new_data)}B. Buffer RAW antes: {len(buffer_raw)}B.")
                buffer_raw += new_data # Acumula datos en el buffer

                # Procesa el buffer si contiene suficientes datos para uno o mas segmentos
                while len(buffer_raw) >= BYTES_PER_CHUNK_RAW_SEGMENT:
                    segment_pcm = buffer_raw[:BYTES_PER_CHUNK_RAW_SEGMENT] # Extrae un segmento
                    buffer_raw = buffer_raw[BYTES_PER_CHUNK_RAW_SEGMENT:]  # Actualiza el buffer
                    if logs_detallados: log.debug(f"[{thread_name}] Segmento RAW de {len(segment_pcm)}B extraido. Buffer restante: {len(buffer_raw)}B.")
                    try: raw_q.put(segment_pcm, timeout=0.1) # Envia el segmento a la cola raw
                    except queue.Full: log.warning(f"[{thread_name}] Cola raw_pcm_segment_queue llena. Segmento RAW descartado.")

            except socket.timeout: continue # Timeout en recv(), normal, reintentar
            except (ConnectionResetError, BrokenPipeError, socket.error) as e_conn: # Errores de conexion
                log.warning(f"[{thread_name}] Error de conexion de socket con {addr}: {e_conn}")
                if conn: conn.close(); conn = None; addr = None; buffer_raw = b''
                try: raw_q.put(None, timeout=0.1) # Envia senal de fin de stream
                except queue.Full: log.warning(f"[{thread_name}] Cola raw_q llena al intentar enviar None tras error de conexion.")
            except Exception as e_loop_recv: # Otros errores inesperados en el bucle de recepcion
                log.error(f"[{thread_name}] Excepcion en bucle de recepcion de datos: {e_loop_recv}", exc_info=True)
                if conn: conn.close(); conn = None; addr = None; buffer_raw = b''
                try: raw_q.put(None, timeout=0.1) # Envia senal de fin de stream
                except queue.Full: log.warning(f"[{thread_name}] Cola raw_q llena al intentar enviar None tras excepcion.")
                time.sleep(0.1) # Pequena pausa antes de reintentar o salir
    except Exception as e_setup_sock: # Error critico al configurar el socket servidor
        log.critical(f"[{thread_name}] Error fatal configurando el socket del servidor de audio: {e_setup_sock}", exc_info=True)
    finally: # Bloque de limpieza del hilo receptor de red
        if conn: # Cierra la conexion del cliente si aun esta abierta
            try: conn.shutdown(socket.SHUT_RDWR)
            except: pass
            conn.close()
        if server_socket: server_socket.close() # Cierra el socket servidor
        try: raw_q.put(None, timeout=0.1) # Asegura que el hilo procesador tambien reciba senal de fin
        except queue.Full: log.warning(f"[{thread_name}] Cola raw_q llena al intentar enviar None final al cerrar.")
        log.info(f"[{thread_name}] Hilo receptor de red terminado.")

# Funcion ejecutada en un hilo para procesar segmentos de audio crudo.
# Toma segmentos PCM de 'raw_q', los convierte a formato WAV en memoria usando
# 'crear_wav_en_memoria', luego procesa estos bytes WAV usando 'process_wav_bytes'
# para convertirlos a mono, remuestrearlos y normalizarlos.
# Finalmente, coloca el audio procesado en 'processed_q'.
def wav_creator_processor_thread_func(stop_event: threading.Event, raw_q: queue.Queue, processed_q: queue.Queue, logs_detallados=False):
    thread_name = threading.current_thread().name
    log.info(f"[{thread_name}] Hilo procesador de WAV iniciado.")
    active_client_stream = True # Asume un stream activo si hay datos o al inicio.
    while True: # Bucle principal del hilo
        try:
            segment_raw_pcm = raw_q.get(timeout=0.5) # Obtiene segmento PCM crudo de la cola
            if segment_raw_pcm is None: # Senal de fin de stream para el cliente actual
                if active_client_stream:
                    log.info(f"[{thread_name}] Recibida senal de fin de stream (None) de raw_q para cliente actual.")
                    active_client_stream = False # Ya no hay un stream activo de este cliente
                if stop_event.is_set() and raw_q.empty(): # Si se pide parada global y no hay mas datos/senales en cola
                    log.info(f"[{thread_name}] Evento de parada y raw_q vacia despues de None. Terminando hilo procesador.")
                    break # Salir del bucle
                raw_q.task_done(); continue # Marca el 'None' como procesado y continua esperando

            active_client_stream = True # Si se reciben datos, el stream esta activo
            if logs_detallados: log.debug(f"[{thread_name}] Tomado segmento RAW de {len(segment_raw_pcm)}B para procesar.")
            # Convierte el segmento PCM crudo a formato WAV en memoria
            wav_bytes = crear_wav_en_memoria(segment_raw_pcm, INCOMING_CHANNELS, INCOMING_SAMPLE_RATE, INCOMING_BYTES_PER_SAMPLE)
            if not wav_bytes:
                log.error(f"[{thread_name}] Fallo la creacion de WAV para el segmento RAW."); raw_q.task_done(); continue
            if logs_detallados: log.debug(f"[{thread_name}] Segmento RAW ({len(segment_raw_pcm)}B) -> WAV en memoria ({len(wav_bytes)}B).")
            # Procesa los bytes WAV (mono, remuestreo, formato)
            audio_mono_processed, final_sr = process_wav_bytes(wav_bytes, TARGET_MONO_SAMPLE_RATE, OUTPUT_NUMPY_DTYPE, logs_detallados)
            if audio_mono_processed:
                if logs_detallados: log.debug(f"[{thread_name}] Audio procesado a {len(audio_mono_processed)}B mono ({final_sr}Hz). Encolando en processed_q.")
                try: processed_q.put((audio_mono_processed, final_sr), timeout=0.1) # Envia audio procesado a la cola de salida
                except queue.Full: log.warning(f"[{thread_name}] Cola processed_audio_queue llena. Audio procesado descartado.")
            raw_q.task_done() # Marca el item como procesado en raw_q
        except queue.Empty: # Timeout esperando en raw_q.get()
            if stop_event.is_set(): # Si se pide parada global y la cola esta vacia
                log.info(f"[{thread_name}] Evento de parada y raw_q vacia. Terminando hilo procesador."); break
            # Si no hay stream activo y la cola esta vacia, el hilo puede esperar o terminar.
            # Por ahora, continua esperando por si un nuevo cliente se conecta.
            if not active_client_stream and raw_q.empty(): pass
            continue # Continuar esperando si la cola esta vacia pero no hay senal de parada total
        except Exception as e_proc_loop: # Otros errores en el bucle de procesamiento
            log.error(f"[{thread_name}] Error en bucle de procesamiento WAV: {e_proc_loop}", exc_info=True)
            if hasattr(raw_q, 'task_done'): # Asegura marcar tarea como hecha si es posible para evitar bloqueos
                try: raw_q.task_done()
                except ValueError: pass # Si ya no esta en la cola
    try: processed_q.put(None, timeout=0.1) # Envia senal de fin al consumidor final
    except queue.Full: log.warning(f"[{thread_name}] Cola processed_q llena al intentar enviar None final.")
    log.info(f"[{thread_name}] Hilo procesador de WAV terminado.")

# Bloque principal para prueba standalone del servidor de audio.
# Inicia los hilos de recepcion, procesamiento y un consumidor de demostracion.
if __name__ == '__main__':
    # Configuracion de logging para la prueba
    logging.basicConfig(
        level=logging.DEBUG if os.getenv("AUDIO_SERVER_DEBUG_MAIN") else logging.INFO, # Nivel DEBUG si variable de entorno esta seteada
        format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    main_log = logging.getLogger("ServerAudio_Main") # Logger especifico para el bloque main

    ENABLE_DETAILED_LOGS_MAIN = bool(os.getenv("AUDIO_SERVER_DEBUG_MAIN")) # Para logs detallados en funciones

    # Para la prueba standalone, se permiten conexiones por defecto al inicio.
    # En un sistema integrado, esto seria controlado por Init_System.py.
    permitir_conexion_audio_robot.set()
    main_log.info("ServerAudio en modo standalone: PERMITIENDO conexiones de cliente (robot) al inicio.")

    # Configura manejadores para senales de interrupcion y terminacion
    signal.signal(signal.SIGINT, manejador_global_signals)  # Ctrl+C
    signal.signal(signal.SIGTERM, manejador_global_signals) # kill

    # Creacion de los hilos del servidor de audio
    receiver_h = threading.Thread(
        target=network_receiver_thread_func,
        args=(terminar_programa_evento, raw_pcm_segment_queue, ENABLE_DETAILED_LOGS_MAIN, permitir_conexion_audio_robot),
        name="NetRecv_Main", daemon=True # daemon=True para que terminen si el hilo principal termina
    )
    processor_h = threading.Thread(
        target=wav_creator_processor_thread_func,
        args=(terminar_programa_evento, raw_pcm_segment_queue, processed_audio_queue, ENABLE_DETAILED_LOGS_MAIN),
        name="WavProc_Main", daemon=True
    )

    # Hilo consumidor de ejemplo para la prueba standalone
    def demo_consumer(stop_ev, data_q, detailed_logs):
        log_consumer = logging.getLogger("DemoAudioConsumer")
        log_consumer.info("Consumidor Demo iniciado. Esperando audio procesado de processed_audio_queue...")
        count = 0
        while not stop_ev.is_set() or not data_q.empty(): # Continuar mientras no haya senal de parada o la cola tenga datos
            try:
                item = data_q.get(timeout=0.2) # Obtiene item de la cola de audio procesado
                if item is None: log_consumer.info("Fin de stream (None) recibido en consumidor demo."); break # Senal de fin
                audio_data, sr_data = item
                count +=1
                if detailed_logs or count % 20 == 0 : # Loguear cada 20 chunks o si logs detallados estan activos
                    log_consumer.info(f"Consumidor Demo: Chunk {count} recibido, {len(audio_data)} bytes a {sr_data}Hz.")
                data_q.task_done() # Marca el item como procesado
            except queue.Empty: # Timeout esperando en la cola
                if stop_ev.is_set() and data_q.empty(): break # Si se pide parada y la cola esta vacia, salir
        log_consumer.info("Consumidor Demo terminado.")

    consumer_h = threading.Thread(
        target=demo_consumer,
        args=(terminar_programa_evento, processed_audio_queue, ENABLE_DETAILED_LOGS_MAIN),
        name="DemoConsumer_Main", daemon=True
    )

    # Inicio de los hilos
    receiver_h.start()
    processor_h.start()
    consumer_h.start()

    main_log.info("[MainServerAudio] Todos los hilos iniciados. El servidor esta escuchando. Esperando Ctrl+C para detener...")
    main_log.info("Para probar el control de conexion del cliente (simulado con senales USR1/USR2 si estan disponibles):")
    main_log.info(f"  (PID actual de ServerAudio.py para enviar senales: {os.getpid()})")
    main_log.info("  - Desactivar permiso: Enviar SIGUSR1 (ej. `kill -USR1 PID`)")
    main_log.info("  - Activar permiso de nuevo: Enviar SIGUSR2 (ej. `kill -USR2 PID`)")

    # Manejadores de senal USR1/USR2 para simular control externo del evento permitir_conexion_audio_robot
    # Esto es solo para la prueba standalone y puede no funcionar en todos los OS (ej. Windows).
    def signal_handler_usr1_demo(signum, frame):
        main_log.info("SENAL USR1 RECIBIDA (DEMO): DESACTIVANDO permiso de conexion del robot.")
        permitir_conexion_audio_robot.clear()
    def signal_handler_usr2_demo(signum, frame):
        main_log.info("SENAL USR2 RECIBIDA (DEMO): ACTIVANDO permiso de conexion del robot.")
        permitir_conexion_audio_robot.set()

    if hasattr(signal, 'SIGUSR1') and hasattr(signal, 'SIGUSR2'): # Verifica si las senales estan disponibles en el OS
        signal.signal(signal.SIGUSR1, signal_handler_usr1_demo)
        signal.signal(signal.SIGUSR2, signal_handler_usr2_demo)
    else:
        main_log.warning("Senales SIGUSR1/SIGUSR2 no disponibles en este OS para prueba de control de evento de conexion.")

    try:
        # Mantiene el hilo principal vivo, chequeando periodicamente el evento de terminacion
        while not terminar_programa_evento.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt: # Captura Ctrl+C
        main_log.info("[MainServerAudio] Ctrl+C recibido en bucle principal. Iniciando apagado global...")
    finally: # Bloque de limpieza final del script principal
        if not terminar_programa_evento.is_set():
            terminar_programa_evento.set() # Asegura que el evento de parada este activo
            permitir_conexion_audio_robot.clear() # Asegura que no se acepten mas conexiones al apagar

        main_log.info("[MainServerAudio] Esperando finalizacion de todos los hilos (join)...")
        threads_to_join = [receiver_h, processor_h, consumer_h]
        for t in threads_to_join:
            if t.is_alive(): t.join(timeout=3.0) # Espera a que cada hilo termine, con timeout
            if t.is_alive(): main_log.warning(f"Hilo {t.name} no finalizo a tiempo.") # Advierte si algun hilo no termina
        main_log.info("--- Servidor de Audio Umebot (Prueba Standalone) Terminado ---")
