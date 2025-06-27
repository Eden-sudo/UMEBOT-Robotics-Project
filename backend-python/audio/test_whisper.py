# ----------------------------------------------------------------------------------
# Titular: Script Experimental de Prueba para Whisper STT (Modelo 'small', VAD Agresivo)
# Funcion Principal: Este script fue disenado para la experimentacion y prueba del
#                    modelo de reconocimiento de voz Whisper (especificamente la version
#                    'small' con faster-whisper) en un bucle de grabacion y transcripcion.
#                    Incluye captura de audio, remuestreo, y el uso de Deteccion de
#                    Actividad de Voz (VAD) con parametros ajustados para ser menos
#                    sensible a pausas cortas.
#                    NOTA: Este enfoque con Whisper no se implemento en el flujo principal
#                    del programa debido a problemas de "alucinaciones" (generacion de
#                    texto incorrecto o inventado) observados durante las pruebas,
#                    ademas de su lentitud en CPU con precision float32. Sirve como
#                    documentacion de las pruebas realizadas.
# ----------------------------------------------------------------------------------

import sounddevice as sd
import numpy as np
import time
import sys
import os

# Intenta importar librerias opcionales y define banderas de disponibilidad
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    print("ERROR: No se pudo importar 'librosa'. El remuestreo no funcionara.")
    print("Por favor, instalalo con: pip install librosa")
    LIBROSA_AVAILABLE = False

try:
    import shutil # Para obtener el ancho del terminal
    TERMINAL_WIDTH = shutil.get_terminal_size((80, 20)).columns
except ImportError:
    TERMINAL_WIDTH = 80 # Ancho por defecto si shutil no esta disponible

try:
    from faster_whisper import WhisperModel # Implementacion optimizada de Whisper
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    print("ERROR: No se pudo importar 'faster_whisper'.")
    print("Por favor, instalalo con: pip install faster-whisper")
    FASTER_WHISPER_AVAILABLE = False

# --- Configuracion del Modelo Whisper y Transcripcion ---
MODEL_SIZE = "small"         # Tamano del modelo Whisper a utilizar (ej. 'tiny', 'base', 'small', 'medium')
LANGUAGE_CODE = "es"         # Codigo de idioma para la transcripcion (espanol)
CHUNK_DURATION_S = 4.0       # Duracion de cada fragmento de audio a grabar y procesar (en segundos)
NATIVE_SAMPLE_RATE = 48000   # Tasa de muestreo nativa esperada del microfono (Hz)
TARGET_SAMPLE_RATE = 16000   # Tasa de muestreo requerida por el modelo Whisper (Hz)
TARGET_MIC_INDEX = 5         # Indice del microfono a utilizar (puede variar segun el sistema)
VAD_FILTER_ENABLED = True    # Activa o desactiva el filtro VAD (Voice Activity Detection)

# --- Ajustes del VAD (Deteccion de Actividad de Voz) ---
# Estos parametros buscan que el VAD sea menos sensible, requiriendo silencios mas largos.
VAD_PARAMETERS = {"threshold": 0.5,                 # Umbral de VAD (valor por defecto comun)
                  "min_silence_duration_ms": 1500,  # Minimo silencio para considerar fin de segmento (mas largo = menos sensible)
                  "min_speech_duration_ms": 250     # Duracion minima de habla para ser considerada
                  }

# --- Configuracion de Hardware para Ejecucion ---
DEVICE = "cpu"               # Dispositivo para la inferencia ('cpu' o 'cuda')
COMPUTE_TYPE = "float32"     # Tipo de computo para la inferencia en CPU ('float32', 'int8', etc.)

# --- Parametros Adicionales de Transcripcion ---
BEAM_SIZE = 5                # Tamano del haz para la busqueda en la decodificacion
TEMPERATURE = 0.2            # Temperatura para el muestreo (controla la aleatoriedad, mas bajo = mas determinista)

# Graba audio desde el dispositivo de entrada especificado utilizando sounddevice.
# Captura a la tasa de muestreo nativa del dispositivo indicado.
#
# Args:
#   duration (float): Duracion de la grabacion en segundos.
#   samplerate (int): Tasa de muestreo para la grabacion.
#   device_index (int): Indice del dispositivo de entrada de audio.
#
# Returns:
#   Optional[np.ndarray]: Array NumPy con el audio grabado (float32),
#                         o None si ocurre un error.
def record_audio(duration, samplerate, device_index):
    print(f"\rGrabando {duration:.1f}s @ {samplerate}Hz desde disp {device_index}... Habla! (Ctrl+C salir) ", end='', flush=True)
    try:
        recording = sd.rec(int(duration * samplerate),
                           samplerate=samplerate,
                           device=device_index,
                           channels=1,        # Captura en mono
                           dtype='float32',   # Tipo de dato de las muestras
                           blocking=True)     # Bloquea hasta que la grabacion termine
        print("\r" + " " * (TERMINAL_WIDTH - 1) + "\r", end='', flush=True) # Limpia la linea de "Grabando..."
        return recording.flatten() # Devuelve el audio como un array 1D
    except sd.PortAudioError as pae:
        print(f"\nERROR PortAudio al grabar: {pae}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"\nERROR durante la grabacion: {e}", file=sys.stderr)
        return None

# Remuestrea los datos de audio de una tasa de muestreo original a una tasa objetivo.
# Utiliza la libreria librosa si esta disponible.
#
# Args:
#   audio_data (np.ndarray): Array NumPy con los datos de audio a remuestrear.
#   orig_sr (int): Tasa de muestreo original del audio_data.
#   target_sr (int): Tasa de muestreo deseada.
#
# Returns:
#   Optional[np.ndarray]: Array NumPy con el audio remuestreado,
#                         o None si librosa no esta disponible o hay un error.
def resample_audio(audio_data, orig_sr, target_sr):
    if not LIBROSA_AVAILABLE: # Verifica si librosa fue importado correctamente
        print("ERROR: librosa no esta disponible, no se puede remuestrear.", file=sys.stderr)
        return None
    if audio_data is None or orig_sr == target_sr: # No remuestrear si no hay datos o las tasas coinciden
        return audio_data

    try:
        # Asegura que el audio este en formato float32 para librosa
        if audio_data.dtype != np.float32: audio_data = audio_data.astype(np.float32)
        resampled = librosa.resample(y=audio_data, orig_sr=orig_sr, target_sr=target_sr, res_type='kaiser_fast')
        return resampled
    except Exception as e:
        print(f"\nERROR durante el remuestreo: {e}", file=sys.stderr)
        return None

# Funcion principal del script de prueba.
# Carga el modelo Whisper 'small' (con faster-whisper), configura los parametros
# de VAD, y entra en un bucle que: graba audio, lo remuestrea, y lo transcribe
# utilizando el modelo cargado. Muestra los resultados de la transcripcion.
# Advierte sobre la lentitud si se usa CPU con float32.
#
# Args:
#   device_override (Optional[str]): Permite forzar el uso de 'cpu' o 'cuda'.
def main(device_override=None):
    if not FASTER_WHISPER_AVAILABLE: sys.exit(1) # Requiere faster-whisper
    if not LIBROSA_AVAILABLE: sys.exit(1)      # Requiere librosa para remuestrear

    selected_device = device_override if device_override else DEVICE
    # Ajusta el tipo de computo si se usa CPU (float32 es mas lento pero puede ser necesario)
    selected_compute_type = "float32" if selected_device == "cpu" else COMPUTE_TYPE # float16 es comun para GPU

    print("-" * 30)
    print(f"Cargando modelo Whisper '{MODEL_SIZE}'...")
    print(f"Usando Dispositivo: '{selected_device}' con Computo: '{selected_compute_type}'")
    if selected_device == "cpu" and selected_compute_type == "float32":
        print("¡¡¡ADVERTENCIA!!!: Usar modelo '{MODEL_SIZE}' con 'float32' en CPU sera EXTREMADAMENTE LENTO.")
    print("(La descarga del modelo puede tardar la primera vez que se ejecuta)")

    try:
        # Carga el modelo Whisper utilizando faster-whisper
        model = WhisperModel(MODEL_SIZE,
                             device=selected_device,
                             compute_type=selected_compute_type)
        print("Modelo Whisper cargado exitosamente.")
    except Exception as e:
        print(f"ERROR cargando el modelo Whisper: {e}", file=sys.stderr)
        return

    # Imprime la configuracion de la prueba
    print("-" * 30)
    print(f"Grabando desde Microfono Indice: {TARGET_MIC_INDEX}")
    print(f"Frecuencia de Grabacion Nativa: {NATIVE_SAMPLE_RATE} Hz")
    print(f"Frecuencia Objetivo (Whisper): {TARGET_SAMPLE_RATE} Hz")
    print(f"Duracion de Fragmento de Audio: {CHUNK_DURATION_S}s")
    print(f"Usando VAD Interno de Whisper: {'Si' if VAD_FILTER_ENABLED else 'No'}")
    if VAD_FILTER_ENABLED:
        print(f"Parametros VAD: {VAD_PARAMETERS}") # Muestra los parametros VAD configurados
    print(f"Temperatura de Transcripcion: {TEMPERATURE}")
    print("-" * 30)

    # 'last_full_text' no se usa como prompt en esta configuracion de prueba.
    last_full_text = ""
    try:
        # Bucle principal de grabacion y transcripcion
        while True:
            # 1. Grabar audio desde el microfono
            raw_audio_data = record_audio(CHUNK_DURATION_S, NATIVE_SAMPLE_RATE, TARGET_MIC_INDEX)

            # Procesa solo si se grabo audio y es suficientemente largo
            if raw_audio_data is not None and raw_audio_data.size > int(NATIVE_SAMPLE_RATE * 0.1): # Minimo 0.1s de audio
                # 2. Remuestrear el audio a la tasa objetivo de Whisper
                audio_to_transcribe = resample_audio(raw_audio_data, NATIVE_SAMPLE_RATE, TARGET_SAMPLE_RATE)

                if audio_to_transcribe is not None:
                    # 3. Transcribir el audio remuestreado
                    print(f"\rTranscribiendo ({selected_compute_type}, {CHUNK_DURATION_S}s chunk)...", end='', flush=True)
                    start_time = time.time()
                    try:
                        # Transcribe el audio, aplicando los parametros VAD si estan habilitados.
                        segments, info = model.transcribe(audio_to_transcribe,
                                                          language=LANGUAGE_CODE,
                                                          beam_size=BEAM_SIZE,
                                                          vad_filter=VAD_FILTER_ENABLED,
                                                          vad_parameters=VAD_PARAMETERS if VAD_FILTER_ENABLED else None,
                                                          temperature=TEMPERATURE
                                                          )
                        end_time = time.time()
                        print("\r" + " " * (TERMINAL_WIDTH -1) + "\r", end='') # Limpia la linea de "Transcribiendo..."

                        current_chunk_text = ""
                        for segment in segments: # Concatena el texto de todos los segmentos detectados
                            current_chunk_text += segment.text.strip() + " "

                        if current_chunk_text.strip():
                            # Muestra el tiempo de transcripcion para evidenciar la velocidad.
                            print(f"Detectado: {current_chunk_text.strip()} (en {end_time - start_time:.2f}s)", flush=True)
                        # No imprime nada si el segmento VAD no contiene texto.

                    except Exception as e:
                        print(f"\nERROR durante transcripcion: {e}", file=sys.stderr)
                        print("\r" + " " * (TERMINAL_WIDTH -1) + "\r", end='') # Limpia la linea "Transcribiendo..."
                        time.sleep(1) # Pausa breve en caso de error de transcripcion

            elif raw_audio_data is None: # Si hubo error en la grabacion
                print("\nEsperando antes de reintentar grabacion...")
                time.sleep(2)
            else: # Si el audio grabado es muy corto, simplemente limpia la linea de estado.
                print("\r" + " " * (TERMINAL_WIDTH -1) + "\r", end='', flush=True)

    except KeyboardInterrupt: # Permite salir del bucle con Ctrl+C
        print("\n\n¡Ctrl+C detectado! Saliendo del bucle de pruebas de Whisper.")
    except Exception as e:
        print(f"\nERROR inesperado en bucle principal: {e}", file=sys.stderr)
    finally:
        print("-" * 30)
        print("Script de prueba de Whisper finalizado.")

# Punto de entrada si el script se ejecuta directamente.
# Permite pasar 'cpu' o 'cuda' como argumento para seleccionar el dispositivo.
if __name__ == "__main__":
    dev = None # Por defecto, usara la constante DEVICE
    if len(sys.argv) > 1: # Si se proporcionan argumentos de linea de comandos
        if sys.argv[1].lower() == "cuda":
            print("INFO: Se intentara usar el dispositivo CUDA para la prueba.")
            dev = "cuda"
        elif sys.argv[1].lower() == "cpu":
            print("INFO: Se usara el dispositivo CPU para la prueba.")
            dev = "cpu"

    main(device_override=dev)
