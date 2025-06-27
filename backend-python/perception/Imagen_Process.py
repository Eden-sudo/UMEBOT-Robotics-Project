#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------
# Titular: Nodo ROS2 Experimental para Deteccion de Personas con YOLO
# Funcion Principal: Este modulo define un nodo de ROS2 (PersonDetectorNode) que
#                    demuestra como procesar un flujo de imagenes de una camara
#                    de robot (publicado en un topic de ROS2, ej. desde driver_naoqi2)
#                    para detectar personas en tiempo real utilizando un modelo YOLO.
#                    El nodo se suscribe a un topic de imagenes, ejecuta la inferencia
#                    con YOLO en cada frame, y publica en otros topics si se ha
#                    detectado una persona.
#                    Este script es de naturaleza experimental y conceptual. Fue funcional
#                    durante las pruebas, pero su integracion completa en el sistema
#                    principal se dejo de lado para priorizar otros puntos del proyecto.
#                    Sirve como documentacion de la exploracion en el area de percepcion.
# ----------------------------------------------------------------------------------

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image # Tipo de mensaje estandar en ROS2 para imagenes
from std_msgs.msg import Bool   # Tipo de mensaje estandar para valores booleanos (True/False)
from cv_bridge import CvBridge, CvBridgeError # Herramienta para convertir entre imagenes de ROS2 y OpenCV
import cv2 # Libreria OpenCV para procesamiento de imagenes
import numpy as np
import threading
import time
import os
from ultralytics import YOLO # Libreria para utilizar modelos YOLO

# --- Dependencias Requeridas ---
# Asegurate de tener estas librerias en tu entorno de ROS2:
# pip install ultralytics opencv-python

# Nodo de ROS2 que se suscribe a un stream de imagenes, detecta personas
# utilizando un modelo YOLO, y publica el estado de la deteccion en otros topics.
class PersonDetectorNode(Node):
    # Inicializa el nodo ROS2, declara parametros, carga el modelo YOLO,
    # y configura los publishers y el suscriptor.
    def __init__(self):
        super().__init__('person_detector_node') # Nombre del nodo en el grafo de ROS2
        self.get_logger().info('Nodo Detector de Personas con YOLO iniciado.')

        # --- Seccion de Parametros del Nodo ---
        # Declarar parametros permite configurar el nodo desde la linea de comandos o archivos de lanzamiento.
        self.declare_parameter('camera_topic', '/camera/front/image_raw') # Topic de la camara a la que suscribirse
        self.declare_parameter('yolo_model_name', 'yolov8n.pt') # Modelo YOLOv8 nano, ligero y rapido.
        self.declare_parameter('confidence_threshold', 0.45) # Umbral de confianza para las detecciones

        # Obtener los valores de los parametros
        self.camera_topic = self.get_parameter('camera_topic').get_parameter_value().string_value
        yolo_model_name = self.get_parameter('yolo_model_name').get_parameter_value().string_value
        self.confidence_threshold = self.get_parameter('confidence_threshold').get_parameter_value().double_value

        self.get_logger().info(f"Suscribiendose al topic de camara: '{self.camera_topic}'")
        self.get_logger().info(f"Usando modelo YOLO: '{yolo_model_name}' (Umbral de confianza: {self.confidence_threshold})")
        # --- Fin de Parametros ---

        self.bridge = CvBridge() # Instancia para la conversion de imagenes

        # --- Seccion de Carga del Modelo YOLO ---
        self.yolo_model = None
        self.person_class_id = None # Almacenara el ID numerico para la clase 'person' del modelo.
        self.yolo_model_loaded = False
        try:
            self.get_logger().info(f"Cargando modelo YOLO '{yolo_model_name}'... (Puede tardar la primera vez)")
            self.yolo_model = YOLO(yolo_model_name)
            # Encuentra el ID de la clase 'person' en el modelo cargado
            if hasattr(self.yolo_model, 'names'):
                # YOLOv8 guarda los nombres en model.names (un diccionario id -> nombre)
                names_dict = self.yolo_model.names
                for class_id, name in names_dict.items():
                    if name.lower() == 'person':
                        self.person_class_id = int(class_id)
                        break
                if self.person_class_id is not None:
                    self.get_logger().info(f"ID para la clase 'person' encontrado en el modelo: {self.person_class_id}")
                else:
                    self.get_logger().warn("ADVERTENCIA: No se encontro la clase 'person' en los nombres del modelo YOLO. La deteccion de personas no funcionara.")
            else:
                self.get_logger().error("Error: El modelo YOLO cargado no tiene el atributo 'names' esperado.")

            self.yolo_model_loaded = True
            self.get_logger().info("Modelo YOLO cargado exitosamente.")
        except Exception as e:
            self.get_logger().error(f"Error CRITICO al cargar el modelo YOLO: {e}")
            # Es critico no continuar si el modelo no se puede cargar.
            raise e # Lanza la excepcion para detener la inicializacion del nodo.
        # --- Fin Carga Modelo YOLO ---

        # --- Seccion de Publishers ---
        # Publisher para el estado de deteccion: publica True/False si se detecta una persona.
        self.status_publisher_ = self.create_publisher(Bool, 'person_detected_status', 10)
        # Publisher para la imagen: publica la imagen COMPLETA solo cuando se detecta una persona.
        # Esto es eficiente para no sobrecargar la red con imagenes innecesarias.
        self.image_publisher_ = self.create_publisher(Image, 'person_detected_image', 5) # QoS 5 es suficiente para esto.
        # --- Fin Publishers ---

        # --- Seccion de Suscriptor ---
        # Suscriptor al topic de la camara. Cada vez que llega una imagen, se llama a image_callback_and_process.
        self.image_subscription = self.create_subscription(
            Image,
            self.camera_topic,
            self.image_callback_and_process, # Funcion a ejecutar por cada mensaje
            10) # Quality of Service (QoS) depth

        self.get_logger().info("Nodo listo para recibir y procesar imagenes.")

    # Funcion callback que se ejecuta por cada mensaje de imagen recibido.
    # Convierte el mensaje de imagen ROS a un formato de OpenCV, ejecuta la
    # inferencia del modelo YOLO para buscar personas y publica los resultados
    # en los topics correspondientes.
    #
    # Args:
    #   msg (sensor_msgs.msg.Image): El mensaje de imagen recibido del topic de la camara.
    def image_callback_and_process(self, msg: Image):
        # No procesar si el modelo no esta cargado o no se encontro la clase 'person'.
        if not self.yolo_model_loaded or self.person_class_id is None:
            # Publica False si no podemos detectar.
            status_msg = Bool(); status_msg.data = False
            self.status_publisher_.publish(status_msg)
            return

        try:
            # Convierte el mensaje de imagen de ROS2 a una imagen de OpenCV (formato BGR8)
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except CvBridgeError as e:
            self.get_logger().error(f'Error de CvBridge al convertir la imagen: {e}')
            return

        person_detected_flag = False # Asumir que no hay persona por defecto en este frame

        # --- Bloque de Deteccion de Personas ---
        try:
            # Ejecuta la inferencia del modelo YOLO sobre la imagen
            results = self.yolo_model(cv_image, verbose=False, conf=self.confidence_threshold)

            # Itera sobre las detecciones encontradas en el frame
            for detection in results[0].boxes.data.tolist():
                x1, y1, x2, y2, score, class_id = detection

                # Comprueba si la clase detectada es la de 'person'
                if int(class_id) == self.person_class_id:
                    self.get_logger().info(f"¡Persona detectada! (Confianza: {score:.2f})", throttle_duration_sec=2) # Loguea con throttle para no inundar la consola
                    person_detected_flag = True # Activa la bandera
                    break # Sale del bucle de detecciones, ya que con una persona es suficiente.
        except Exception as e:
            self.get_logger().error(f"Error durante la inferencia con YOLO: {e}", throttle_duration_sec=10)
            person_detected_flag = False # Considera como no detectado si hay un error
        # --- Fin Deteccion ---

        # --- Bloque de Publicacion de Resultados ---
        # 1. Publica siempre el estado de deteccion (True/False) en el topic de estado.
        status_msg = Bool(); status_msg.data = person_detected_flag
        self.status_publisher_.publish(status_msg)

        # 2. Publica la imagen original COMPLETA solo si se detecto una persona.
        if person_detected_flag:
            try:
                # Reutilizar el mensaje original de la camara ('msg') es eficiente si no se necesita dibujar sobre la imagen.
                # Si se dibujaran los bounding boxes, se usaria cv2_to_imgmsg para crear un nuevo mensaje.
                self.image_publisher_.publish(msg)
            except Exception as e:
                self.get_logger().error(f"Error al publicar la imagen con deteccion: {e}")
        # --- Fin Publicar Resultados ---

    # Metodo para una limpieza ordenada al destruir el nodo.
    def destroy_node(self):
        self.get_logger().info("Cerrando el nodo detector de personas...")
        # Liberacion de recursos (opcional, Python y rclpy suelen manejarlo)
        try:
            del self.yolo_model
        except Exception:
            pass
        super().destroy_node()

# Funcion principal que inicializa rclpy, crea y ejecuta el nodo
# PersonDetectorNode, manteniendolo activo hasta que se solicite su cierre.
def main(args=None):
    rclpy.init(args=args)
    person_detector_node = None
    try:
        person_detector_node = PersonDetectorNode()
        rclpy.spin(person_detector_node) # Mantiene el nodo activo, procesando callbacks en bucle.
    except KeyboardInterrupt:
        print('Cierre del nodo solicitado por el usuario (Ctrl+C).')
    except Exception as e:
        # Captura errores durante la inicializacion (ej. fallo al cargar modelo) u otros.
        if person_detector_node:
            person_detector_node.get_logger().fatal(f"Error fatal durante la ejecucion: {e}")
        else:
            print(f"Error fatal durante la inicializacion del nodo: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # El bloque de limpieza siempre se ejecuta para un apagado ordenado.
        print("Realizando limpieza final y apagando rclpy...")
        if person_detector_node:
            person_detector_node.destroy_node() # Llama a la destruccion explicita del nodo
        if rclpy.ok():
            rclpy.shutdown() # Cierra la comunicacion de ROS2
        print("Programa de percepcion terminado.")

# Punto de entrada del script
if __name__ == '__main__':
    main()
