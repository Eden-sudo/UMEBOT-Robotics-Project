# ----------------------------------------------------------------------------------
# Titular: Aplicacion de Interfaz Grafica Experimental (Kivy) para Lanzar Procesos
# Funcion Principal: Este modulo define una aplicacion de escritorio basica usando
#                    la libreria Kivy. Su objetivo era servir como una prueba de
#                    concepto para una interfaz grafica (UI) que pudiera controlar
#                    y monitorear el estado del sistema del robot, como por ejemplo,
#                    lanzar los nodos de ROS2, ver el estado de los servicios y
#                    mostrar logs en tiempo real.
#                    La implementacion actual es un prototipo simple que interactua
#                    con un modulo conceptual 'Launch_ROS2' para iniciar y detener
#                    un proceso externo y mostrar su estado. Debido a la falta de
#                    tiempo y la priorizacion de otras tareas, el desarrollo de
#                    una UI mas completa se dejo en esta fase conceptual.
# ----------------------------------------------------------------------------------

import kivy
kivy.require('2.0.0') # Requisito de version de Kivy (ajustar si es necesario).

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.uix.scrollview import ScrollView
from kivy.properties import StringProperty
import os # Para obtener el PID en la logica de control (no usado directamente aqui)

# Importa el modulo que gestiona el proceso externo (ej. el lanzador de ROS2).
# Se asume que este modulo existe y tiene las funciones necesarias.
import Launch_ROS2

# Define la disposicion y los widgets principales de la interfaz grafica.
# Contiene los botones de control, la etiqueta de estado y un area para logs.
class LauncherLayout(BoxLayout):
    # Propiedad de Kivy para enlazar el texto de estado con el Label de la UI.
    # Cuando esta propiedad cambia, el texto del Label se actualiza automaticamente.
    status_text = StringProperty("Status: Idle")

    # Constructor del layout. Organiza los botones, etiquetas y el area de scroll.
    # Tambien programa la actualizacion periodica de la UI usando Clock.schedule_interval.
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=10, spacing=10, **kwargs)

        # --- Creacion de los Botones de Control ---
        button_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        launch_button = Button(text="Iniciar Setup ROS2")
        launch_button.bind(on_press=self.press_launch) # Asocia el evento de presion con el metodo
        button_layout.add_widget(launch_button)

        stop_button = Button(text="Detener Setup")
        stop_button.bind(on_press=self.press_stop) # Asocia el evento de presion con el metodo
        button_layout.add_widget(stop_button)
        self.add_widget(button_layout)

        # --- Creacion de la Etiqueta de Estado ---
        # Usamos la propiedad 'status_text' para que Kivy actualice el texto del Label automaticamente.
        self.status_label = Label(text=self.status_text, size_hint_y=None, height=40)
        self.bind(status_text=self.status_label.setter('text')) # Enlaza la propiedad 'status_text' de esta clase con la propiedad 'text' del Label.
        self.add_widget(self.status_label)

        # --- Creacion del Area de Texto para Logs ---
        self.log_label = Label(text="Logs apareceran aqui...\n", size_hint_y=None, halign='left', valign='top')
        # Enlazar texture_size al tamano del widget es necesario para que el ScrollView calcule correctamente el scroll.
        self.log_label.bind(texture_size=self.log_label.setter('size'))
        log_scroll = ScrollView(size_hint=(1, 1)) # Permite hacer scroll si el texto es muy largo
        log_scroll.add_widget(self.log_label)
        self.add_widget(log_scroll)

        # --- Programacion de la Actualizacion de la UI ---
        # Llama al metodo update_ui cada 1.0 segundo para refrescar el estado del proceso.
        Clock.schedule_interval(self.update_ui, 1.0)
        self.update_ui(0) # Llamada inicial para establecer el estado correcto de la UI al arrancar.

    # Metodo que se ejecuta al presionar el boton "Iniciar".
    # Llama a la funcion correspondiente en el modulo Launch_ROS2 para
    # iniciar el proceso externo y actualiza la UI con el resultado.
    def press_launch(self, instance):
        self.log_label.text += "INFO: Boton 'Iniciar' presionado.\n"
        self.status_text = "Status: Iniciando proceso..."
        success, handle = Launch_ROS2.launch_driver_setup()
        if not success:
            self.status_text = "Status: ¡Error al iniciar! Revisa la consola."
            self.log_label.text += "ERROR: Fallo el inicio del script externo.\n"
        # Si tuvo exito, el estado se actualizara en el proximo ciclo de update_ui.

    # Metodo que se ejecuta al presionar el boton "Detener".
    # Llama a la funcion correspondiente en el modulo Launch_ROS2 para
    # detener el proceso externo.
    def press_stop(self, instance):
        self.log_label.text += "INFO: Boton 'Detener' presionado.\n"
        stopped = Launch_ROS2.stop_process()
        if stopped:
            self.log_label.text += "INFO: Solicitud de detencion enviada al proceso.\n"
        else:
            self.log_label.text += "WARN: No se pudo detener el proceso (quizas ya estaba detenido).\n"
        # Actualiza la UI inmediatamente para reflejar el cambio de estado.
        self.update_ui(0)

    # Metodo llamado periodicamente por el Clock de Kivy para refrescar la UI.
    # Consulta el estado actual del proceso a traves de Launch_ROS2
    # y actualiza la etiqueta de estado correspondiente.
    #
    # Args:
    #   dt (float): Delta time (tiempo transcurrido desde la ultima llamada),
    #             proporcionado por Kivy Clock.
    def update_ui(self, dt):
        current_status = Launch_ROS2.check_process_status()
        self.status_text = f"Status: {current_status}"

        # Seccion conceptual para leer la salida del proceso sin bloquear la UI.
        # En una aplicacion real, esto requeriria una implementacion mas compleja
        # con hilos o comunicacion entre procesos.
        proc = Launch_ROS2.process_handle # Accede al handle global del proceso
        if proc and proc.poll() is None: # Si el proceso aun esta corriendo
            # Para leer output continuamente sin bloquear la UI se necesitarian hilos o una logica asincrona.
            # Por ahora, nos enfocamos solo en mostrar el estado del proceso.
            pass

    # Metodo auxiliar para anadir mensajes al area de logs de la UI.
    def add_log(self, message):
        self.log_label.text += message + "\n"

# Clase principal de la aplicacion Kivy.
# Su metodo build() es el punto de entrada que construye y devuelve
# el widget raiz de la aplicacion (en este caso, LauncherLayout).
class KivyRosLauncherApp(App):
    def build(self):
        return LauncherLayout()

# Punto de entrada del script si se ejecuta directamente.
if __name__ == '__main__':
    # Se necesita tener Kivy instalado en el entorno: pip install kivy
    KivyRosLauncherApp().run()
