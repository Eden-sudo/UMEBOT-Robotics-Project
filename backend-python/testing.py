# ----------------------------------------------------------------------------------
# Titular: Script de Prueba del Pipeline de Audio (Robot -> PC -> Vosk)
# Funcion Principal: Este script esta disenado para realizar una prueba de extremo
#                    a extremo (end-to-end) de todo el flujo de audio desde el robot
#                    hasta el reconocimiento de voz en el PC.
#                    Inicia el sistema completo, incluyendo un modulo de streaming
#                    de audio en el robot, recibe el audio en el PC, lo procesa con
#                    Vosk y muestra el texto reconocido. Sirve para depurar y validar
#                    toda la cadena de audio.
# ----------------------------------------------------------------------------------

import qi
import sys
import time

# Importacion de los modulos principales del sistema necesarios para la prueba.
try:
    import Init_System # Para la inicializacion completa del sistema.
    # Los siguientes imports son para type hinting o comprension, pero las instancias
    # se crean dentro de la funcion de prueba.
    from audio.mic_robot_handler import RobotMicHandler
    from audio.ManagerProcessAudio import AudioProcessor
except ImportError as e:
    print(f"ERROR: No se pudieron importar los modulos necesarios: {e}")
    sys.exit(1)

# --- Configuracion Estatica para la Prueba ---
# Ruta estatica al modelo Vosk. En un sistema de produccion, esto estaria en un archivo de configuracion.
STATIC_VOSK_MODEL_PATH = "/home/umecit-nueva/ros2_pepper_ws/umebot_core/audio/models/vosk-model-small-es-0.42"

# --- Funcion Callback para el Resultado del STT ---

# Callback que se ejecuta cuando el AudioProcessor finaliza el reconocimiento de un segmento de texto.
def on_text_recognized(text):
    print(f"\n[TESTING SCRIPT] TEXTO FINAL RECONOCIDO: ===> '{text}' <===\n")

# --- Funcion Principal de la Prueba ---

# Orquesta la parte de la prueba que se ejecuta en el PC: inicia el receptor de audio
# y el procesador STT, y mantiene la prueba activa durante un tiempo determinado.
#
# Args:
#   app, session: Instancias de la aplicacion y sesion de Naoqi.
#   audio_streamer_from_init: La instancia del modulo de streaming de audio que se esta ejecutando en el robot.
#   vosk_model_path_static: Ruta al modelo Vosk a utilizar.
def run_audio_pipeline_test(app, session, audio_streamer_from_init, vosk_model_path_static):
    print("\n[TEST] Iniciando prueba del PIPELINE DE AUDIO COMPLETO...")
    mic_handler = None
    audio_processor = None
    PC_MIC_HANDLER_PORT = 5000 # Puerto en el que el PC escuchara el audio del robot.

    try:
        # Verifica que el modulo de streaming de audio en el robot se haya inicializado correctamente.
        if not audio_streamer_from_init:
            print("ERROR [TEST]: El modulo NaoqiAudioStreamerModule no fue inicializado por Init_System. Abortando prueba.")
            return
        # Verifica si el modulo en el robot reporta estar transmitiendo.
        if not audio_streamer_from_init.isStreaming():
            print("ADVERTENCIA [TEST]: NaoqiAudioStreamerModule no reporta estar transmitiendo. Verifica que su inicializacion fue exitosa.")

        # Inicia el manejador en el PC que escucha el audio enviado por el robot.
        print(f"[TEST] Iniciando RobotMicHandler en el puerto {PC_MIC_HANDLER_PORT}...")
        mic_handler = RobotMicHandler(pc_listen_port=PC_MIC_HANDLER_PORT)
        print("[TEST] RobotMicHandler iniciado.")

        # Crea el procesador de audio que usara Vosk para el reconocimiento.
        print(f"[TEST] Creando AudioProcessor con el modelo: {vosk_model_path_static}")
        audio_processor = AudioProcessor(
            mic_handler_instance=mic_handler, # Le pasa el manejador de microfono del robot.
            vosk_model_path=vosk_model_path_static,
            text_recognized_callback=on_text_recognized # Asigna el callback para el texto final.
        )
        audio_processor.start_processing() # Inicia el hilo de procesamiento de audio.
        print("[TEST] AudioProcessor iniciado.")

        print("\n=======================================================================")
        print("PIPELINE DE AUDIO ACTIVO. Habla al robot durante los proximos 60 segundos.")
        print("Presiona Ctrl+C para detener la prueba antes.")
        print("=======================================================================")
        test_duration = 60
        # Bucle principal de la prueba, mantiene el script vivo mientras se procesa el audio.
        for i in range(test_duration):
            if audio_processor and not audio_processor.is_processing():
                print("[TEST] AudioProcessor ha dejado de procesar inesperadamente.")
                break
            print(f"   ...escuchando... Tiempo restante: {test_duration - i}s", end='\r')
            time.sleep(1)
        print("\n[TEST] Tiempo de prueba de audio finalizado.")

    except KeyboardInterrupt:
        print("\n[TEST] Interrupcion de teclado. Deteniendo el pipeline de prueba...")
    except RuntimeError as e_vosk:
        print(f"ERROR CRITICO [TEST] con el motor Vosk: {e_vosk}")
    except Exception as e:
        print(f"\nERROR [TEST]: Ocurrio un error general durante la prueba: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Bloque de limpieza para detener los componentes iniciados en esta prueba.
        print("\n[TEST] Limpieza final del pipeline de audio...")
        if audio_processor:
            print("   Deteniendo AudioProcessor...")
            audio_processor.stop_processing()
        if mic_handler:
            print("   Deteniendo RobotMicHandler...")
            mic_handler.shutdown()
        # El NaoqiAudioStreamerModule es manejado por el bloque finally del __main__ para asegurar
        # un desregistro correcto del servicio Naoqi.
        print("[TEST] Prueba del pipeline de audio finalizada (logica de prueba).")

# --- Punto de Entrada Principal del Script de Prueba ---
if __name__ == "__main__":
    print("******************************************************")
    print("Ejecutando testing.py (Prueba del Pipeline de Audio - Vosk)...")
    print("******************************************************")

    # Variables para asegurar la limpieza final
    app_instance = None
    session_instance_for_cleanup = None
    audio_service_id_for_cleanup = None
    audio_streamer_for_cleanup = None

    try:
        # 1. Llama a la inicializacion completa del sistema.
        # Se asume que esta funcion prepara el robot, los servicios y el modulo de streaming de audio.
        app_instance, session_instance, service_proxies_dict, audio_streamer_module, audio_service_id, robot_is_ready = Init_System.initialize_robot_system()

        # Guarda las referencias necesarias para la limpieza en el bloque finally.
        session_instance_for_cleanup = session_instance
        audio_service_id_for_cleanup = audio_service_id
        audio_streamer_for_cleanup = audio_streamer_module

        # 2. Si la inicializacion fue exitosa, ejecuta la prueba del pipeline de audio.
        if robot_is_ready and app_instance and session_instance:
            run_audio_pipeline_test(app_instance, session_instance, audio_streamer_module, STATIC_VOSK_MODEL_PATH)
        elif not robot_is_ready:
            print("\nERROR [Main Test]: La inicializacion del robot fallo. No se puede ejecutar la prueba.")
        else:
            print("\nERROR [Main Test]: Fallo la conexion inicial con el robot. No se puede ejecutar la prueba.")

    except Exception as e_main_test:
        print(f"ERROR CATASTROFICO en el script testing.py: {e_main_test}")
        import traceback
        traceback.print_exc()
    finally:
        # --- Limpieza Final Global ---
        print("\n[Main Test Script] Iniciando limpieza final global...")
        # Detiene y desregistra el servicio de audio en el robot.
        if audio_streamer_for_cleanup:
            print("   Deteniendo NaoqiAudioStreamerModule (shutdown)...")
            audio_streamer_for_cleanup.shutdown()
        if audio_service_id_for_cleanup and session_instance_for_cleanup and session_instance_for_cleanup.isConnected():
            try:
                print(f"   Desregistrando servicio de audio con ID: {audio_service_id_for_cleanup}...")
                session_instance_for_cleanup.unregisterService(audio_service_id_for_cleanup)
                print("   Servicio de audio desregistrado.")
            except Exception as e_unreg_main:
                print(f"ADVERTENCIA: Error al desregistrar el servicio de audio: {e_unreg_main}")

        # Pone al robot en estado de reposo.
        if 'service_proxies_dict' in locals() and service_proxies_dict:
            motion_proxy = service_proxies_dict.get("ALMotion")
            if motion_proxy:
                try:
                    print("   Quitando rigidez del cuerpo del robot (si es necesario)...")
                    motion_proxy.stopMove()
                    time.sleep(0.2)
                    motion_proxy.setStiffnesses("Body", 0.0)
                except Exception as e_stiff: print(f"ADVERTENCIA: Error quitando la rigidez del robot: {e_stiff}")

        # Cierra la conexion principal de Naoqi.
        if app_instance:
            if app_instance.session.isConnected():
                print("   Deteniendo aplicacion qi...")
                app_instance.stop()
                print("   Aplicacion qi detenida.")
            else:
                print("   La aplicacion qi ya parecia estar detenida.")
        print("[Main Test Script] Limpieza final global completada.")

    print("\n******************************************************")
    print("Script testing.py finalizado.")
    print("******************************************************")
