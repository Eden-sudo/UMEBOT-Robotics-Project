#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Gestor de Conversacion y Logica de IA (ConversationManager)
# Funcion Principal: Este modulo define la clase ConversationManager, el "cerebro"
#                    o pilar central que orquesta toda la logica conversacional
#                    del sistema. Sus responsabilidades incluyen:
#                    - Gestionar el flujo de una conversacion completa.
#                    - Interactuar con PromptBuilder para construir prompts contextualizados.
#                    - Utilizar diferentes backends de IA (como GPTAPIClient para OpenAI
#                      o LocalLlamaClient para modelos locales GGUF) para generar respuestas.
#                    - Permitir el cambio dinamico de la personalidad del robot y del
#                      modelo de IA en uso.
#                    - Registrar el historial de la conversacion en la base de datos a
#                      traves de DBManager.
#                    - Servir como punto de union entre la entrada del usuario (STT/UI)
#                      y la salida expresiva del robot (que sera manejada por
#                      AnimationSpeechController).
# ----------------------------------------------------------------------------------

import sys
import os
import asyncio
import json
from datetime import datetime, timezone
import logging
from typing import Dict, Any, Optional, List, Union, Literal

# --- Configuracion de Paths para Importaciones ---
# Se ajusta el sys.path para asegurar que los modulos en otros directorios (ai/, db/) puedan ser importados.
UMEBOT_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
# sys.path.insert(0, os.path.dirname(UMEBOT_CORE_DIR)) # Opcional, si la estructura es mas compleja
sys.path.insert(0, UMEBOT_CORE_DIR)

# Configuracion del logger para este modulo
log = logging.getLogger("ConversationManager")

# --- Importacion de Modulos del Proyecto ---
try:
    from ai.prompt_builder import PromptBuilder
    from db.dbManager import DBManager
    from ai.gpt_api import GPTAPIClient
    from ai.llama_interface import LocalLlamaClient # Deberia renombrarse a gguf_interface en el futuro
except ImportError as e:
    # Fallback de logging si hay un error critico en las importaciones
    _critical_log_import = logging.getLogger("CM_Imports_Critical_Fallback")
    if not _critical_log_import.hasHandlers():
        logging.basicConfig(level=logging.CRITICAL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    _critical_log_import.critical(f"Error critico importando modulos en ConversationManager: {e}. Verifica la estructura de directorios y sys.path.", exc_info=True)
    sys.exit(1)

# --- Constantes y Rutas por Defecto ---
OPENAI_API_KEY_FROM_ENV = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL_DEFAULT = "gpt-4o"
DEFAULT_PERSONALITIES_PATH = os.path.join(UMEBOT_CORE_DIR, "ai", "data_JSONL", "personalities.json")
DB_PATH_STANDALONE_DEMO = os.path.join(UMEBOT_CORE_DIR, "db", "conversations_cm_standalone_test.db")

# Gestiona el flujo de la conversacion, interactuando con LLMs, PromptBuilder y
# DBManager para generar respuestas, mantener contexto y personalidad.
# Permite cambiar dinamicamente de personalidad y backend de IA.
class ConversationManager:
    # Inicializa el ConversationManager.
    #
    # Args:
    #   db_manager_instance (DBManager): Instancia del gestor de base de datos (requerido).
    #   personalities_config_path (str): Ruta al archivo JSON de personalidades.
    #   default_personality_key (str): La clave de la personalidad a cargar por defecto.
    #   default_ai_backend_type (Literal): El backend de IA a usar por defecto ('openai_gpt', 'local_gguf', 'none').
    #   initial_openai_config (Optional[Dict]): Configuracion inicial para el cliente GPT.
    #   initial_gguf_config (Optional[Dict]): Configuracion inicial para el cliente GGUF local.
    #   initial_conversation_id (Optional[int]): ID de una conversacion existente para continuarla.
    def __init__(self,
                 db_manager_instance: DBManager,
                 personalities_config_path: str = DEFAULT_PERSONALITIES_PATH,
                 default_personality_key: str = "ume_asistente",
                 default_ai_backend_type: Literal["openai_gpt", "local_gguf", "none"] = "openai_gpt",
                 initial_openai_config: Optional[Dict[str, Any]] = None,
                 initial_gguf_config: Optional[Dict[str, Any]] = None,
                 initial_conversation_id: Optional[int] = None):

        if not db_manager_instance:
            msg = "Se debe proporcionar una instancia de DBManager a ConversationManager."; log.critical(msg); raise ValueError(msg)
        self.db_manager = db_manager_instance
        self.personalities_data: Dict[str, Dict[str, Any]] = self._load_personalities(personalities_config_path)

        self.prompt_builder: Optional[PromptBuilder] = None
        self.ai_client: Optional[Union[GPTAPIClient, LocalLlamaClient]] = None
        self.current_ai_backend_type: Optional[Literal["openai_gpt", "local_gguf", "none"]] = "none"
        self.current_personality_key: Optional[str] = None

        # Carga la personalidad por defecto o la primera disponible
        loaded_pers_ok = False
        pers_keys = list(self.personalities_data.keys()) if self.personalities_data else []
        if default_personality_key in self.personalities_data:
            loaded_pers_ok = self.set_active_personality(default_personality_key)
        elif pers_keys:
            log.warning(f"Personalidad por defecto '{default_personality_key}' no encontrada. Usando la primera disponible: '{pers_keys[0]}'.")
            loaded_pers_ok = self.set_active_personality(pers_keys[0])
        else:
            log.error(f"No se cargaron personalidades validas desde '{personalities_config_path}'.")

        # Si falla la carga de personalidad, usa una de fallback para no detener el sistema
        if not loaded_pers_ok or not self.prompt_builder:
            log.warning("Usando PromptBuilder con un system prompt de fallback generico.")
            try:
                self.prompt_builder = PromptBuilder(predefined_system_prompt="Eres un asistente de IA. Se directo y conciso."); self.current_personality_key = "fallback_generic"
            except Exception as e_pb: log.critical(f"Fallo al crear el PromptBuilder de fallback: {e_pb}", exc_info=True); raise

        # Configura el backend de IA por defecto
        self.ai_client = None
        active_backend_set = False
        if default_ai_backend_type == "openai_gpt":
            cfg = initial_openai_config or {}
            # Combina API key de la config y de las variables de entorno
            current_api_key_for_init = cfg.get("api_key") or OPENAI_API_KEY_FROM_ENV
            if not current_api_key_for_init:
                log.warning("API Key de OpenAI no proporcionada. El backend de OpenAI no fue inicializado.")
            else:
                cfg_for_gpt = {"api_key": current_api_key_for_init, "model_name": cfg.get("model_name", OPENAI_MODEL_DEFAULT)}
                active_backend_set = self.set_active_ai_model("openai_gpt", cfg_for_gpt)
        elif default_ai_backend_type == "local_gguf":
            if initial_gguf_config: active_backend_set = self.set_active_ai_model("local_gguf", initial_gguf_config)
            else: log.warning("Configuracion GGUF inicial no proporcionada. El backend local no fue inicializado.")
        elif default_ai_backend_type == "none":
            log.info("Backend de IA configurado a 'none' por defecto. El sistema no generara respuestas de IA.")
            self.current_ai_backend_type = "none"; active_backend_set = True
        else:
            log.warning(f"Tipo de backend de IA por defecto '{default_ai_backend_type}' no es reconocido.")

        if not active_backend_set and default_ai_backend_type != "none":
            log.warning(f"No se pudo inicializar el backend por defecto '{default_ai_backend_type}'. El cliente de IA queda desactivado.")
            self.current_ai_backend_type = "none"

        # Gestiona la conversacion actual (continua una o inicia una nueva)
        self.current_conversation_id: Optional[int] = None
        if initial_conversation_id and hasattr(self.db_manager, 'conversation_exists') and self.db_manager.conversation_exists(initial_conversation_id):
            self.current_conversation_id = initial_conversation_id
            log.info(f"Continuando con la conversacion existente ID: {self.current_conversation_id}")
        else:
            if initial_conversation_id: log.warning(f"El ID de conversacion inicial {initial_conversation_id} no fue encontrado en la BD. Se creara una nueva.")
            self._start_new_db_conversation(summary=f"Inicio de sesion ({self.current_personality_key or 'N/A'})")

        if self.current_conversation_id is None:
            msg = "No se pudo establecer un ID de conversacion valido en la base de datos."; log.critical(msg); raise RuntimeError(msg)

        log.info(f"ConversationManager listo. ConvID:{self.current_conversation_id}, Personalidad:'{self.current_personality_key}', Backend:'{self.current_ai_backend_type or 'NoAI'}'")

    # Metodo auxiliar para cargar las personalidades desde un archivo de configuracion JSON.
    def _load_personalities(self, config_path: str) -> Dict[str, Dict[str, Any]]:
        default_fallback = {"fallback_generic": {"name": "Asistente Generico", "system_prompt": "Eres un asistente de IA conciso."}}
        if not os.path.exists(config_path):
            log.error(f"El archivo de personalidades '{config_path}' no fue encontrado. Usando personalidad de fallback."); return default_fallback
        try:
            with open(config_path, 'r', encoding='utf-8') as f: personalities = json.load(f)
            log.info(f"Personalidades cargadas exitosamente desde '{config_path}' ({len(personalities)} perfiles encontrados).")
            return personalities if personalities else default_fallback
        except Exception as e:
            log.error(f"Error cargando el archivo de personalidades '{config_path}': {e}", exc_info=True); return default_fallback

    # Cambia la personalidad activa del robot.
    # Carga el system_prompt de la nueva personalidad y reinstancia el PromptBuilder con el.
    #
    # Returns:
    #   bool: True si el cambio fue exitoso, False en caso contrario.
    def set_active_personality(self, personality_key: str) -> bool:
        if not self.personalities_data or personality_key not in self.personalities_data:
            keys_avail = list(self.personalities_data.keys()) if self.personalities_data else "Ninguna"
            log.error(f"La personalidad '{personality_key}' no fue encontrada. Disponibles: {keys_avail}")
            return False
        personality_cfg = self.personalities_data[personality_key]
        system_prompt = personality_cfg.get("system_prompt")
        robot_name_in_pers = personality_cfg.get("robot_name", "Umebot")
        if not system_prompt:
            log.error(f"La configuracion de la personalidad '{personality_key}' no contiene una clave 'system_prompt'."); return False
        try:
            self.prompt_builder = PromptBuilder(predefined_system_prompt=system_prompt)
            if hasattr(self.prompt_builder, 'robot_name'): self.prompt_builder.robot_name = robot_name_in_pers
            self.current_personality_key = personality_key
            log.info(f"Personalidad activa cambiada a: '{personality_key}' (Nombre: {personality_cfg.get('name', 'N/A')}).")
            return True
        except Exception as e:
            log.error(f"Error creando una instancia de PromptBuilder para la personalidad '{personality_key}': {e}", exc_info=True)
            self.prompt_builder = None; return False

    # Cambia el backend de IA activo (ej. de OpenAI a un modelo local).
    # Reinstancia el cliente de IA correspondiente (GPTAPIClient o LocalLlamaClient)
    # con la configuracion proporcionada.
    #
    # Returns:
    #   bool: True si el cambio fue exitoso, False en caso contrario.
    def set_active_ai_model(self, model_type: Literal["openai_gpt", "local_gguf", "none"], config: Optional[Dict[str, Any]] = None) -> bool:
        log.info(f"Configurando el backend de IA a: {model_type}")
        current_config = config or {}
        try:
            if model_type == "openai_gpt":
                self.ai_client = GPTAPIClient(
                    api_key=current_config.get("api_key"),
                    default_model=current_config.get("model_name", OPENAI_MODEL_DEFAULT)
                )
            elif model_type == "local_gguf":
                gguf_path = current_config.get("model_gguf_path","")
                if not gguf_path or not os.path.exists(gguf_path):
                    log.error(f"La configuracion para 'local_gguf' requiere una ruta valida en 'model_gguf_path'. Ruta proporcionada: {gguf_path}");
                    self.ai_client = None; self.current_ai_backend_type = "none"; return False
                self.ai_client = LocalLlamaClient(**current_config) # Pasa toda la config al constructor
            elif model_type == "none":
                self.ai_client = None # Desactiva el cliente de IA
            else:
                log.error(f"Tipo de backend de IA '{model_type}' no reconocido."); self.ai_client = None; return False

            self.current_ai_backend_type = model_type
            # Loguea informacion util sobre el cliente activo
            client_info = "Ninguno (desactivado)"
            if self.ai_client:
                client_info = type(self.ai_client).__name__
                if isinstance(self.ai_client, GPTAPIClient): client_info += f" (Modelo: {self.ai_client.default_model})"
                elif isinstance(self.ai_client, LocalLlamaClient): client_info += f" (Path: {os.path.basename(getattr(self.ai_client, 'model_path', 'GGUF'))})"
            log.info(f"Backend de IA activo cambiado a: {model_type} (Cliente: {client_info})")
            return True
        except Exception as e:
            log.error(f"Error configurando el backend de IA '{model_type}': {e}", exc_info=True)
            self.ai_client = None; self.current_ai_backend_type = "none"; return False

    # Metodo auxiliar para crear una nueva entrada de conversacion en la base de datos y
    # establecerla como la conversacion activa actual.
    def _start_new_db_conversation(self, summary: Optional[str] = None, user_id: str = "default_user_cm"):
        timestamp_val = datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z'
        pers_name = "N/P"; ai_model_info = "NoAI"
        if self.current_personality_key and self.personalities_data:
            pers_name = self.personalities_data.get(self.current_personality_key, {}).get('name', 'Desconocida')
        if self.ai_client:
            if isinstance(self.ai_client, GPTAPIClient): ai_model_info = f"GPT-{self.ai_client.default_model}"
            elif isinstance(self.ai_client, LocalLlamaClient): ai_model_info = f"Local-{os.path.basename(getattr(self.ai_client, 'model_path', 'GGUF'))}"
        # Crea un resumen automatico si no se proporciona uno
        final_summary = summary if summary else f"Umebot ({pers_name} - {ai_model_info}) @ {timestamp_val}"
        new_id = self.db_manager.start_conversation(summary=final_summary, user_id=user_id)
        if new_id is None: log.error("Fallo critico al intentar iniciar una nueva conversacion en la base de datos.")
        else: self.current_conversation_id = new_id; log.info(f"Nueva conversacion iniciada en la BD (ID: {new_id}, Usuario: {user_id}).")

    # Metodo principal para obtener una respuesta de la IA.
    # Orquesta todo el proceso: construye el prompt con contexto e historial,
    # guarda la entrada del usuario en la BD, llama al backend de IA activo,
    # guarda la respuesta de la IA en la BD y devuelve la respuesta generada.
    #
    # Args:
    #   user_input (str): El texto de entrada del usuario.
    #   source (str): El origen de la entrada (ej. 'stt', 'gui').
    #   images (Optional[List[str]]): Lista de imagenes en formato base64 data URI (para multimodal).
    #
    # Returns:
    #   Optional[str]: La respuesta del modelo de IA, posiblemente con tags de animacion.
    async def get_ai_response(self, user_input: str,
                              source: str = "unknown",
                              images: Optional[List[str]] = None) -> Optional[str]:
        # Validaciones iniciales
        if self.current_conversation_id is None: return "^runTag(error)Error: No hay una conversacion activa."
        if not self.prompt_builder: return "^runTag(error)Error: La personalidad no esta configurada correctamente."
        if not self.ai_client and self.current_ai_backend_type != "none": return "^runTag(error)Error: El motor de IA no esta inicializado."
        elif self.current_ai_backend_type == "none" or self.ai_client is None: return "El modo de IA esta desactivado."

        log.info(f"CM: Procesando entrada (ConvID {self.current_conversation_id}, Origen: {source}): '{user_input[:40]}...' (Imagenes: {len(images) if images else 0})")
        # Prepara el contenido para el PromptBuilder, manejando multimodalidad para GPT
        user_input_content_for_builder: Union[str, List[Dict[str, Any]]] = user_input
        if images and self.current_ai_backend_type == "openai_gpt" and isinstance(self.ai_client, GPTAPIClient):
            content_list = [{"type": "text", "text": user_input}]
            for img_url_data_uri in images: content_list.append({"type": "image_url", "image_url": {"url": img_url_data_uri}})
            user_input_content_for_builder = content_list
        elif images: log.warning(f"Se proporcionaron imagenes, pero el backend actual ({self.current_ai_backend_type}) no es GPT multimodal. Se usara solo el texto.")

        # Construye la lista de mensajes para el modelo de IA
        messages_for_llm: List[Dict[str, Any]]
        try:
            log.debug("CM: Construyendo el prompt completo para el modelo de IA...")
            # La construccion del prompt puede ser intensiva, se ejecuta en un hilo para no bloquear.
            messages_for_llm = await asyncio.to_thread(
                self.prompt_builder.build_messages_for_chat_api, user_input_content_for_builder,
                self.db_manager, self.current_conversation_id
            )
            # Loguea los mensajes completos enviados al modelo en nivel DEBUG para una depuracion detallada.
            log.debug(f"CM: Mensajes preparados para enviar al modelo IA ({self.current_ai_backend_type}): {messages_for_llm}")
        except Exception as e_prompt:
            log.error(f"CM: Error en PromptBuilder al construir el prompt: {e_prompt}", exc_info=True); return "^runTag(embarrassed)Tuve un problema preparando el contexto de mi respuesta."

        # Guarda la entrada del usuario en la base de datos
        try:
            user_payload_db: Dict[str, Any] = {"text": user_input, "source": source}
            if images: user_payload_db["image_info_count"] = len(images)
            user_content_to_save = json.dumps({"type": "input", "timestamp_original": datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z', "payload_data": user_payload_db})
            await asyncio.to_thread(self.db_manager.add_interaction, self.current_conversation_id, 'user', user_content_to_save)
        except Exception as e_db_user: log.error(f"CM: Error guardando la entrada del usuario en la base de datos: {e_db_user}", exc_info=True)

        # Solicita la respuesta al backend de IA activo
        raw_ai_response: Optional[str] = None
        log.info(f"CM: Solicitando respuesta al backend de IA activo ({self.current_ai_backend_type})...")
        try:
            if isinstance(self.ai_client, (GPTAPIClient, LocalLlamaClient)):
                raw_ai_response = await self.ai_client.generate_response(messages=messages_for_llm)
        except Exception as e_ai_call:
            log.error(f"CM: Error en la llamada al cliente de IA ({self.current_ai_backend_type}): {e_ai_call}", exc_info=True)
            raw_ai_response = f"^runTag(sad)Tuve un problema con mi IA ({self.current_ai_backend_type})."

        # Procesa y guarda la respuesta de la IA
        if raw_ai_response:
            response_text_to_log = str(raw_ai_response) if raw_ai_response is not None else ""
            log.info(f"CM: Respuesta de la IA recibida: '{response_text_to_log[:60].replace(os.linesep, ' ')}...'")
            try:
                # Guarda la respuesta del asistente en la base de datos
                model_info = "Unknown"; backend_type = self.current_ai_backend_type or "unknown_backend"
                if isinstance(self.ai_client, GPTAPIClient): model_info = self.ai_client.default_model
                elif isinstance(self.ai_client, LocalLlamaClient): model_info = os.path.basename(getattr(self.ai_client, 'model_path', 'GGUF'))
                assistant_content_to_save = json.dumps({"type": "output", "timestamp_original": datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z', "payload_data": {"text": raw_ai_response, "model_used": f"{backend_type}_{model_info}"}})
                await asyncio.to_thread(self.db_manager.add_interaction, self.current_conversation_id, 'assistant', assistant_content_to_save)
            except Exception as e_db_assist: log.error(f"CM: Error guardando la respuesta de la IA en la base de datos: {e_db_assist}", exc_info=True)
        else: # Si la IA no devuelve respuesta
            raw_ai_response = "^runTag(confused)No estoy seguro de que responder."
            log.warning(f"CM: El backend de IA no devolvio una respuesta. Usando respuesta de fallback: {raw_ai_response}")
            try:
                fallback_to_save = json.dumps({"type": "output", "timestamp_original": datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z', "payload_data": {"text": raw_ai_response, "model_used": "fallback_empty"}})
                await asyncio.to_thread(self.db_manager.add_interaction, self.current_conversation_id, 'assistant', fallback_to_save)
            except Exception as e_db_fb: log.error(f"CM: Error guardando la respuesta de fallback en la base de datos: {e_db_fb}", exc_info=True)
        return raw_ai_response

    # --- Metodos de Gestion del Ciclo de Vida de la Conversacion ---
    def get_current_conversation_id(self) -> Optional[int]: return self.current_conversation_id
    def start_new_conversation(self, summary: Optional[str] = None, user_id: str = "default_user") -> Optional[int]:
        log.info(f"CM: Solicitud para iniciar una nueva conversacion (Usuario: {user_id}, Resumen: {summary}).")
        self._start_new_db_conversation(summary=summary, user_id=user_id)
        return self.current_conversation_id
    def end_current_conversation(self):
        if self.current_conversation_id: log.info(f"CM: Finalizando la conversacion activa ID: {self.current_conversation_id}"); self.current_conversation_id = None
        else: log.info("CM: No hay una conversacion activa para finalizar.")

# --- Bloque de prueba ---
if __name__ == "__main__":
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
    log_main_test = logging.getLogger("CM_Standalone_Test")
    log_main_test.setLevel(logging.DEBUG)
    if not log_main_test.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s'))
        log_main_test.addHandler(handler)
        log_main_test.propagate = False
    log_main_test.info("--- Iniciando Prueba Standalone de ConversationManager ---")
    if not OPENAI_API_KEY_FROM_ENV:
        log_main_test.warning("OPENAI_API_KEY no configurada. Backend 'openai_gpt' podría no funcionar.")
    else:
        log_main_test.info(f"OPENAI_API_KEY detectada.")
    os.makedirs(os.path.dirname(DEFAULT_PERSONALITIES_PATH), exist_ok=True)
    if not os.path.exists(DEFAULT_PERSONALITIES_PATH):
        log_main_test.warning(f"Archivo personalidades '{DEFAULT_PERSONALITIES_PATH}' no encontrado. Creando demo.")
        demo_personalities_data = {
            "ume_asistente": {"name": "Umebot Asistente (Demo)", "robot_name": "UmeDemoAsistente", "system_prompt": "Eres UmeDemoAsistente, un robot servicial. Usa ^runTag(joy)."},
            "ume_profesor": {"name": "Umebot Profesor (Demo)", "robot_name": "ProfesorUmeDemo", "system_prompt": "Eres ProfesorUmeDemo. Responde formalmente. Usa ^runTag(exclamation)."}
        }
        try:
            with open(DEFAULT_PERSONALITIES_PATH, 'w', encoding='utf-8') as f: json.dump(demo_personalities_data, f, indent=2, ensure_ascii=False)
            log_main_test.info(f"Archivo personalidades DEMO creado: {DEFAULT_PERSONALITIES_PATH}")
        except Exception as e_json_write:
            log_main_test.error(f"No se pudo crear archivo personalidades demo: {e_json_write}")
    os.makedirs(os.path.dirname(DB_PATH_STANDALONE_DEMO), exist_ok=True)
    if os.path.exists(DB_PATH_STANDALONE_DEMO):
        log_main_test.info(f"Borrando DB de prueba anterior: {DB_PATH_STANDALONE_DEMO}")
        try: os.remove(DB_PATH_STANDALONE_DEMO)
        except OSError as e_rm: log_main_test.error(f"No se pudo borrar DB de prueba anterior: {e_rm}")
    log_main_test.info(f"Usando DB para prueba en: {DB_PATH_STANDALONE_DEMO}")
    try:
        test_db_manager = DBManager(db_path=DB_PATH_STANDALONE_DEMO)
    except Exception as e_db_init:
        log_main_test.critical(f"No se pudo inicializar DBManager para prueba: {e_db_init}", exc_info=True); sys.exit(1)
    cm_main_test_instance = None
    try:
        openai_cfg_test_main = {"model_name": OPENAI_MODEL_DEFAULT}
        gguf_model_path_for_main_test = os.path.join(UMEBOT_CORE_DIR, "..", "ai", "models", "Phi-3-mini-4k-instruct-q4.gguf") 
        gguf_model_path_for_main_test = os.path.abspath(gguf_model_path_for_main_test)
        if not os.path.exists(gguf_model_path_for_main_test):
            log_main_test.warning(f"Modelo GGUF de prueba NO encontrado: '{gguf_model_path_for_main_test}'. Backend 'local_gguf' NO funcionará.")
        gguf_cfg_test_main = {
            "model_gguf_path": gguf_model_path_for_main_test,
            "n_ctx": 1024, "chat_format": "phi-3",
            "n_gpu_layers": 0, "verbose": False
        }
        cm_main_test_instance = ConversationManager(
            db_manager_instance=test_db_manager,
            personalities_config_path=DEFAULT_PERSONALITIES_PATH,
            default_personality_key="ume_asistente",
            default_ai_backend_type="openai_gpt" if OPENAI_API_KEY_FROM_ENV else "none",
            initial_openai_config=openai_cfg_test_main if OPENAI_API_KEY_FROM_ENV else None,
            initial_gguf_config=gguf_cfg_test_main if os.path.exists(gguf_model_path_for_main_test) else None
        )
    except Exception as e_cm_init_main:
        log_main_test.critical(f"No se pudo inicializar CM para prueba: {e_cm_init_main}", exc_info=True); sys.exit(1)
    async def standalone_main_test_loop(cm: ConversationManager):
        if cm.current_conversation_id is None:
            log_main_test.error("CM (prueba) sin ID de conversación activo. Abortando."); return
        current_pers_cfg = cm.personalities_data.get(cm.current_personality_key, {})
        pers_name_display = current_pers_cfg.get("name", cm.current_personality_key)
        log_main_test.info(f"Prueba CM lista. ConvID: {cm.get_current_conversation_id()}, Pers: '{pers_name_display}', Backend: '{cm.current_ai_backend_type or 'NoAI'}'")
        print("\nEscribe 'salir'. Cmds: 'pers:clave_pers', 'model:gpt|local|none'")
        print("-" * 80)
        while True:
            try:
                user_text = input("Tú: ").strip()
                if user_text.lower() == 'salir': log_main_test.info("Saliendo..."); break
                if user_text.lower().startswith("pers:"):
                    key = user_text.split(":", 1)[1].strip()
                    if cm.set_active_personality(key):
                        new_pers_cfg = cm.personalities_data.get(cm.current_personality_key, {})
                        new_pers_name_display = new_pers_cfg.get("name", cm.current_personality_key)
                        log_main_test.info(f"Personalidad -> '{new_pers_name_display}'")
                    else: log_main_test.error(f"Fallo al cambiar personalidad a '{key}'.")
                    continue
                elif user_text.lower().startswith("model:"):
                    choice = user_text.split(":", 1)[1].strip().lower()
                    cfg, type_to_set = {}, None
                    if choice == "gpt": type_to_set, cfg = "openai_gpt", openai_cfg_test_main
                    elif choice == "local":
                        type_to_set, cfg = "local_gguf", gguf_cfg_test_main
                        if not os.path.exists(cfg.get("model_gguf_path","")):
                            log_main_test.error(f"Modelo GGUF no en '{cfg.get('model_gguf_path','N/A')}'. No se puede cambiar."); continue
                    elif choice == "none": type_to_set = "none"
                    else: log_main_test.warning(f"Modelo '{choice}' no reconocido."); continue
                    if type_to_set and cm.set_active_ai_model(type_to_set, cfg):
                        log_main_test.info(f"Backend IA -> '{cm.current_ai_backend_type}'")
                    else: log_main_test.error(f"Fallo al cambiar backend IA a '{type_to_set}'.")
                    continue
                if not user_text: continue
                response = await cm.get_ai_response(user_text, source="console_main_test")
                current_pers_cfg_resp = cm.personalities_data.get(cm.current_personality_key, {})
                robot_display_name = current_pers_cfg_resp.get("name", cm.current_personality_key or "Umebot")
                print(f"\n{robot_display_name}: {response if response else '...'}")
                print("-" * 80)
            except KeyboardInterrupt: log_main_test.info("Bucle de prueba CM interrumpido."); break
            except Exception as e_loop_main_test: log_main_test.error(f"Error en bucle de prueba CM: {e_loop_main_test}", exc_info=True)
        if hasattr(cm, 'end_current_conversation'): cm.end_current_conversation()
        log_main_test.info("--- Prueba Standalone de ConversationManager Finalizada ---")
    try:
        asyncio.run(standalone_main_test_loop(cm_main_test_instance))
    except KeyboardInterrupt:
        log_main_test.info("Programa de prueba CM interrumpido (Ctrl+C en asyncio.run).")
    except Exception as e_run_main_test:
        log_main_test.critical(f"Error fatal ejecutando prueba CM: {e_run_main_test}", exc_info=True)
    finally:
        logging.shutdown()
        print(f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) Script ConversationManager.py (standalone test) finalizado.")

