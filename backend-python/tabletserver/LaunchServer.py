# ----------------------------------------------------------------------------------
# Titular: Lanzador del Servidor Web Uvicorn/FastAPI
# Funcion Principal: Este script es el punto de entrada para iniciar el servidor
#                    web. Su funcion es configurar y ejecutar el servidor ASGI Uvicorn,
#                    que a su vez carga la aplicacion FastAPI definida en 'ServerWeb.py'.
#                    Lee la configuracion basica (host, puerto, nivel de log) desde
#                    variables de entorno para mayor flexibilidad.
#                    Aunque originalmente se concibio como una clase mas compleja para
#                    encapsular la configuracion, su implementacion actual es la de un
#                    lanzador directo y simple.
# ----------------------------------------------------------------------------------

import uvicorn  # El servidor ASGI (Asynchronous Server Gateway Interface) que ejecuta FastAPI
import os
import logging

# --- Seccion de Configuracion ---
# Lee la configuracion del servidor desde variables de entorno, con valores por defecto si no se definen.
# Es buena practica leer de un solo lugar (ej. un archivo .env), pero se leen aqui para simplicidad del lanzador.
LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "info").lower()
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
# Asegurarse que el puerto por defecto coincida con las expectativas de otros modulos.
SERVER_PORT = int(os.getenv("SERVER_PORT", 8080))

# --- Seccion de Logging ---
# Configura un logging basico para este script de lanzamiento, permitiendo ver los mensajes de inicio.
logging.basicConfig(level=LOG_LEVEL_NAME.upper(), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("LaunchServer")

# --- Bloque de Ejecucion Principal ---
# Este bloque se ejecuta solo si el script es llamado directamente (ej. `python tabletserver/LaunchServer.py`).
if __name__ == "__main__":
    log.info(f"Iniciando servidor Uvicorn para la aplicacion 'ServerWeb:app'...")
    log.info(f"Host de escucha: {SERVER_HOST}")
    log.info(f"Puerto de escucha: {SERVER_PORT}")
    log.info(f"Nivel de log configurado: {LOG_LEVEL_NAME}")

    # Llama a uvicorn.run para iniciar el servidor
    uvicorn.run(
        "ServerWeb:app",  # Apunta al objeto 'app' dentro del modulo 'ServerWeb' (archivo ServerWeb.py).
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level=LOG_LEVEL_NAME.lower(), # Nivel de log que usara Uvicorn para sus propios mensajes.
        reload=False, # Cambiar a True solo para desarrollo, recarga el servidor al detectar cambios en el codigo.
                      # Requiere que Uvicorn este instalado con soporte para 'watchfiles'.
        # loop="uvloop" # Descomentar si se instalo uvloop para usar su bucle de eventos de alto rendimiento.
    )
