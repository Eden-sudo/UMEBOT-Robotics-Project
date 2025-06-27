#!/usr/bin/env python3
# -*- encoding: UTF-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Orquestador Principal de la Aplicacion Umebot
# Funcion Principal: Este script es el punto de entrada principal para toda la
#                    aplicacion Umebot. Su funcion es orquestar el ciclo de vida
#                    completo del sistema:
#                    1. Configura el entorno (rutas y logging).
#                    2. Define una configuracion centralizada (APP_CONFIG) para todos
#                       los modulos del sistema.
#                    3. Ejecuta una secuencia de inicializacion asincrona en fases:
#                       - FASE 1: Conecta y prepara el robot a bajo nivel (Init_Robot).
#                       - FASE 2: Compone e interconecta todos los modulos de alto
#                         nivel del sistema (Init_System).
#                       - FASE 3: Inicia todos los servicios de fondo (STT, servidor
#                         web, gestor de movimiento, etc.).
#                    4. Mantiene la aplicacion en ejecucion y maneja las senales de
#                       apagado (ej. Ctrl+C) para una finalizacion ordenada.
# ----------------------------------------------------------------------------------

import asyncio
import logging
import sys
import os
import signal
from datetime import datetime
import traceback
from typing import Optional

# --- Configuracion de Rutas y Logging (Debe ser lo primero) ---
# Agrega el directorio raiz del proyecto al path de Python para asegurar que las importaciones funcionen.
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configura el nivel de log principal para toda la aplicacion. DEBUG para ver todo, INFO para menos verbosidad.
LOG_LEVEL_MAIN = logging.DEBUG
logging.basicConfig(
    level=LOG_LEVEL_MAIN,
    format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("UmebotAppMain")
# --- Fin de la Configuracion Inicial ---

try:
    from Init_Robot import initialize_robot_base_async
    from Init_System import SystemComposer # Clase principal que orquesta los modulos
except ImportError as e_main_imp:
    log.critical(f"MAIN: Error critico importando Init_Robot o Init_System: {e_main_imp}", exc_info=True)
    sys.exit(1)

# --- Configuracion Centralizada de la Aplicacion (APP_CONFIG) ---
# Este diccionario contiene toda la configuracion para los diferentes modulos.
# Centralizar la configuracion aqui facilita la modificacion del comportamiento
# del sistema sin tener que editar el codigo de cada modulo individualmente.
APP_CONFIG = {
    # --- Rutas de Archivos y Directorios ---
    "VOSK_MODEL_PATH": os.path.join(project_root, "audio", "models", "vosk-model-small-es-0.42"),
    "DB_PATH": os.path.join(project_root, "db", "umebot_main_app.db"),
    "DEFAULT_PERSONALITIES_PATH": os.path.join(project_root, "ai", "data_JSONL", "personalities.json"),
    "LOCAL_ANIMATIONS_BASE_PATH": os.path.join(project_root, "data", "animations"),

    # --- Configuracion STT (Vosk y Deteccion de Actividad de Voz - VAD) ---
    "STT_SAMPLE_RATE": 16000,
    "STT_DEFAULT_SOURCE": "robot", # Fuente de audio inicial: "robot" o "local"
    "VOSK_VOCABULARY_LIST": ["Umebot", "UMECIT"], # Vocabulario para mejorar el reconocimiento de estas palabras.
    "STT_VAD_AGGRESSIVENESS": 2, # Nivel de agresividad del VAD (0-3)
    "STT_VAD_FRAME_MS": 30, # Duracion del frame para el VAD (10, 20 o 30)
    "LOG_VAD_STATE": False,  # Poner a True para ver logs detallados del estado del VAD.
    "STT_VAD_SILENCE_TIMEOUT_SEC": 2.0, # Segundos de silencio para que el VAD considere que el usuario ha terminado de hablar.

    # --- Configuracion del Microfono Local (PC/Servidor) ---
    "ENABLE_LOCAL_MIC": True,
    "LOCAL_MIC_NAME": "default", # "default" para el microfono por defecto, o parte del nombre (ej. "USB")
    "LOCAL_MIC_PREFERRED_SR": 48000, # Tasa de captura preferida (si es diferente a STT_SAMPLE_RATE, se remuestreara).

    # --- Configuracion del Servidor de Audio del Robot ---
    "ENABLE_ROBOT_AUDIO_SERVER": True,
    "SERVER_AUDIO_DETAILED_LOGS": False, # Activar para logs muy detallados de ServerAudio.py.

    # --- Configuracion del Servidor de la Tablet (FastAPI/Uvicorn) ---
    "TABLET_SERVER_HOST": "0.0.0.0", # Escuchar en todas las interfaces de red.
    "TABLET_SERVER_PORT": 8088,

    # --- Configuracion del Gestor de Conversacion y Modelos de IA ---
    "DEFAULT_PERSONALITY_KEY": "ume_asistente", # Clave de la personalidad inicial a cargar desde el JSON.
    "DEFAULT_AI_BACKEND": "openai_gpt", # Opciones: "openai_gpt", "local_gguf", "none".
    "ROBOT_BUSY_MESSAGE": "^runTag(hesitation) Un momento por favor, estoy procesando.", # Mensaje si se le habla mientras esta ocupado.

    # --- Configuracion Especifica para el Backend de OpenAI ---
    "CM_OPENAI_CONFIG": {
        "api_key": os.environ.get("OPENAI_API_KEY"), # Lee la clave API desde una variable de entorno.
        "model_name": "gpt-4o" # Modelo especifico a utilizar.
    },
    # --- Configuracion Especifica para el Backend Local GGUF ---
    "CM_LOCAL_GGUF_CONFIG": {
        "model_gguf_path": os.path.join(project_root, "ai", "models", "Phi-3-mini-4k-instruct-q4.gguf"),
        "n_ctx": 2048, # Tamano del contexto.
        "chat_format": "chatml", # Formato del chat, importante para que el modelo funcione correctamente.
        "n_gpu_layers": 0, # Ajustar si se dispone de GPU compatible con llama.cpp.
        "verbose": False # Verbosity de la libreria llama.cpp.
    },

    # --- Configuracion para el Gestor de Movimiento y Gamepad ---
    "GAMEPAD_ANIMATION_CONFIG": { # Define las acciones por capa y boton del gamepad.
        0: { # Capa 0
            "a": {"type": "local_anim", "category": "Hello", "name": None}, # 'name': None para una animacion aleatoria de la categoria "Hello"
            "b": {"type": "local_anim", "category": "Attract", "name": "Attract_L02.qianim"},
            "x": {"type": "local_anim", "category": "Tickle", "name": "tickle_2.qianim"},
            "y": {"type": "standard_tag", "tag": "IDLE_BodyTalk_01"} # Un tag de una animacion estandar del robot
        },
        1: { # Capa 1
            "a": {"type": "local_anim", "category": "Reactions", "name": "Exploration_01"},
            "b": {"type": "speak_annotated", "text": "^runTag(question)¿Que exploramos ahora?"},
            "x": {"type": "none"}, # El boton X no hace nada en la capa 1
            "y": {"type": "standard_tag", "tag": "thinking_02"}
        }
    },
    "GAMEPAD_DEFAULT_LAYER": 0, # Capa de animacion con la que inicia el gamepad.
    "GAMEPAD_INITIAL_SPEED": 0.5, # Modificador de velocidad inicial del gamepad (rango: 0.1-1.0).
    "ACTIVATE_GAMEPAD_ON_START": True, # Poner a True para activar el modo de control por gamepad al iniciar Umebot.
}
# --- Fin de la Configuracion Global APP_CONFIG ---

# Corutina principal que orquesta el ciclo de vida completo de la aplicacion.
# Maneja la secuencia de inicio en fases, mantiene la aplicacion corriendo
# y gestiona el proceso de apagado ordenado.
async def main_orchestrator():
    app_instance_naoqi = None
    system_composer_instance: Optional[SystemComposer] = None
    loop = asyncio.get_running_loop()

    # Verificaciones criticas de configuracion antes de iniciar
    vosk_path_check = APP_CONFIG.get("VOSK_MODEL_PATH")
    if not vosk_path_check or not os.path.isdir(vosk_path_check):
        log.critical(f"La ruta al modelo Vosk ('{vosk_path_check}') no es un directorio valido. Abortando."); return

    try:
        log.info("===== UMEBOT CORE SYSTEM - INICIANDO =====")
        # --- FASE 1: Inicializacion del Robot ---
        log.info("FASE 1: Conectando con el robot (Init_Robot)...")
        app_instance_naoqi, session_val, naoqi_services_val, robot_is_ready = await initialize_robot_base_async()
        if not robot_is_ready:
            log.critical("Fallo en la inicializacion base del robot. Abortando."); return
        log.info("FASE 1 COMPLETADA: Robot conectado y servicios NAOqi base listos.")

        # --- FASE 2: Composicion del Sistema ---
        log.info("FASE 2: Componiendo el sistema de aplicacion (SystemComposer)...")
        system_composer_instance = SystemComposer(
            app_instance=app_instance_naoqi,
            session_instance=session_val,
            naoqi_services=naoqi_services_val,
            app_config=APP_CONFIG, # Se pasa toda la configuracion centralizada
            main_event_loop=loop
        )
        if not system_composer_instance.all_components_ready:
            log.critical("SystemComposer fallo la composicion de los componentes. Abortando.");
            if app_instance_naoqi and hasattr(app_instance_naoqi, 'stop'): app_instance_naoqi.stop()
            return
        log.info("FASE 2 COMPLETADA: Componentes principales de la aplicacion listos e interconectados.")

        # --- FASE 3: Inicio de Servicios ---
        log.info("FASE 3: Iniciando los servicios principales gestionados por SystemComposer...")
        if not await system_composer_instance.start_main_services():
            raise RuntimeError("Fallo al iniciar uno o mas servicios principales de SystemComposer.")
        log.info("===== UMEBOT CORE SYSTEM - CORRIENDO =====")
        log.info("La aplicacion esta en funcionamiento. Presiona Ctrl+C para detenerla de forma ordenada.")

        # --- FASE 4: Bucle Principal en Ejecucion ---
        # El sistema se mantiene en ejecucion esperando una senal de apagado (ej. Ctrl+C).
        stop_event_main = asyncio.Event()
        # Maneja las senales de terminacion para un apagado limpio
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event_main.set)
        await stop_event_main.wait() # Mantiene la corutina principal viva hasta que el evento se active

    except asyncio.CancelledError:
        log.info("Bucle principal de Umebot cancelado.")
    except RuntimeError as e_rt:
        log.critical(f"RuntimeError en el orquestador principal: {e_rt}", exc_info=True)
    except Exception as e_main:
        log.critical(f"Error no manejado en el orquestador principal: {e_main}", exc_info=True)
    finally:
        # --- FASE DE APAGADO ---
        log.info("===== UMEBOT CORE SYSTEM - INICIANDO APAGADO ORDENADO =====")
        # Detiene los servicios de alto nivel primero
        if system_composer_instance and hasattr(system_composer_instance, 'stop_main_services'):
            log.info("Deteniendo los servicios de SystemComposer...");
            await system_composer_instance.stop_main_services()
        # Finalmente, detiene la conexion de bajo nivel con el robot
        if app_instance_naoqi and hasattr(app_instance_naoqi, 'stop'):
            log.info("Limpieza final del robot y detencion de la aplicacion NAOqi...")
            if 'naoqi_services_val' in locals() and naoqi_services_val:
                motion_s = naoqi_services_val.get("ALMotion")
                if motion_s and hasattr(motion_s, 'robotIsWakeUp') and motion_s.robotIsWakeUp():
                    log.info("El robot permanecera en estado 'wakeUp' al finalizar.")
                    # Considera anadir motion_s.rest() aqui si quieres que el robot descanse y pierda rigidez al final.
            app_instance_naoqi.stop()
            log.info("Aplicacion qi (NAOqi) detenida.")
        log.info("===== UMEBOT CORE SYSTEM - APAGADO COMPLETADO =====")
        await asyncio.sleep(0.1) # Pequena pausa antes de salir

# Punto de entrada del script. Ejecuta las verificaciones pre-vuelo y
# lanza el orquestador principal asincrono.
if __name__ == "__main__":
    # Verificaciones criticas antes de iniciar el bucle de eventos asyncio.
    vosk_path_check = APP_CONFIG.get("VOSK_MODEL_PATH")
    if not vosk_path_check or not os.path.isdir(vosk_path_check):
        print(f"ERROR CRITICO PRE-LOG: La ruta al modelo Vosk ('{vosk_path_check}') es invalida. Abortando.")
        sys.exit(1)
    if APP_CONFIG.get("DEFAULT_AI_BACKEND") == "local_gguf":
        gguf_path_val = APP_CONFIG.get("CM_LOCAL_GGUF_CONFIG", {}).get("model_gguf_path")
        if not gguf_path_val or not os.path.exists(gguf_path_val):
            print(f"ADVERTENCIA PRE-LOG: El modelo GGUF ('{gguf_path_val}') no fue encontrado. El backend 'local_gguf' podria no funcionar.")

    try:
        # Inicia el bucle de eventos de asyncio y ejecuta el orquestador principal.
        asyncio.run(main_orchestrator())
    except KeyboardInterrupt:
        print(f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) Programa Umebot interrumpido por el usuario (Ctrl+C).")
    except Exception as e_glob_run:
        print(f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) EXCEPCION GLOBAL IRRECUPERABLE en asyncio.run: {e_glob_run}")
        traceback.print_exc()
    finally:
        print(f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) Script main.py finalizado.")
