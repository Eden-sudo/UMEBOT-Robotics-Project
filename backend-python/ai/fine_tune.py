# ----------------------------------------------------------------------------------
# Titular: Script Experimental para Ajuste Fino (Fine-Tuning) de LLM en CPU con LoRA
# Funcion Principal: Este script documenta un proceso conceptual y experimental para
#                    realizar un ajuste fino supervisado (SFT) de un modelo de lenguaje
#                    grande (especificamente Phi-3-mini-4k-instruct) utilizando datos
#                    personalizados (ej. informacion de la universidad UMECIT).
#                    Debido a las limitaciones significativas de hardware (entrenamiento
#                    forzado en CPU), este script es principalmente documental y sirve
#                    como guia de los pasos y configuraciones que se consideraron.
#                    Utiliza las librerias Hugging Face (transformers, datasets, peft, trl)
#                    y configura el entrenamiento con PEFT/LoRA para reducir la carga
#                    computacional, aunque el proceso en CPU sigue siendo extremadamente
#                    lento y demandante de memoria. El objetivo teorico era adaptar
#                    el modelo para mejorar su conocimiento especifico y reducir la
#                    dependencia de RAG basado en JSONL para cierta informacion.
# ----------------------------------------------------------------------------------

import os
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    logging as hf_logging, # Renombrado para evitar conflicto con el modulo logging estandar
    # BitsAndBytesConfig no se usa aqui (seria para QLoRA en GPU).
)
from peft import LoraConfig, PeftModel, get_peft_model # LoRA (sin Q) se puede usar en CPU para reducir parametros entrenables.
from trl import SFTTrainer

# --- ADVERTENCIA IMPORTANTE SOBRE EJECUCION EN CPU ---
# El siguiente bloque imprime una advertencia critica sobre los requisitos de este script.
print("*" * 80)
print("ADVERTENCIA: Intentando ajuste fino en CPU para Phi-3-mini (aprox. 3.8B parametros).")
print("Esto sera EXTREMADAMENTE LENTO y requerira MUCHA MEMORIA RAM (probablemente >32GB).")
print("Se recomienda encarecidamente usar una GPU con suficiente VRAM y CUDA, preferiblemente con QLoRA.")
print("Este script esta configurado para CPU como demostracion conceptual y para entornos sin GPU.")
print("*" * 80)
# --- FIN ADVERTENCIA ---


# --- Seccion 1: Configuracion General del Proceso de Ajuste Fino ---
base_model_id = "microsoft/Phi-3-mini-4k-instruct" # Identificador del modelo base en Hugging Face Hub.
dataset_path = "data_JSONL/umebot_tuning_data.jsonl" # Ruta al archivo JSONL con los datos para el ajuste fino.
output_dir = "tuned_models_cpu/phi3-mini-umebot-adapter-cpu" # Directorio donde se guardaran los adaptadores LoRA entrenados en CPU.

# Parametros de LoRA (Low-Rank Adaptation) para un ajuste fino eficiente en recursos.
lora_r = 8           # Rango (rank) de LoRA. Un valor bajo reduce parametros y memoria.
lora_alpha = 16      # Parametro Alpha de LoRA, a menudo el doble del rango.
lora_dropout = 0.05  # Tasa de dropout para las capas LoRA, para regularizacion.

# Parametros de TrainingArguments ajustados drasticamente para ejecucion en CPU.
# Estos valores son conservadores para intentar funcionar con memoria limitada.
num_train_epochs = 1      # Numero de epocas de entrenamiento (1 ya es mucho para CPU con este modelo).
per_device_train_batch_size = 1 # Tamano de lote muy pequeno, crucial para la memoria RAM en CPU.
gradient_accumulation_steps = 1 # Acumulacion minima. Aumentar solo con mucha RAM para simular lotes mas grandes.
gradient_checkpointing = True # MUY RECOMENDADO para ahorrar memoria RAM, especialmente en CPU, a costa de un poco de velocidad.
max_grad_norm = 0.3           # Limite para el gradiente (evita exploding gradients).
learning_rate = 2e-5          # Tasa de aprendizaje tipica para ajuste fino.
weight_decay = 0.001          # Decaimiento de peso para regularizacion.
optim = "adamw_torch"         # Optimizador AdamW (version de PyTorch), adecuado para CPU (no version 'paged').
lr_scheduler_type = "cosine"  # Tipo de planificador de tasa de aprendizaje.
max_steps = -1                # Limita el numero total de pasos de entrenamiento. -1 para usar num_train_epochs.
                              # Opcional: limitar pasos para una prueba rapida (ej. max_steps=50).
warmup_ratio = 0.03           # Proporcion de pasos de calentamiento para la tasa de aprendizaje.
group_by_length = True        # Agrupar por longitud puede optimizar ligeramente el padding y el uso de memoria.
save_steps = 100              # Guardar checkpoints con menos frecuencia para reducir sobrecarga en CPU.
logging_steps = 10            # Frecuencia de registro de metricas.

# Parametros para SFTTrainer (Supervised Fine-tuning Trainer).
max_seq_length = 512          # Longitud maxima de secuencia. REDUCIR para ahorrar RAM en CPU. Ajustar segun la RAM disponible.
packing = False               # Si se deben empaquetar multiples secuencias cortas en una mas larga (False para simplificar en CPU).
# Forzar el uso de CPU para todos los calculos.
device_map = {"": "cpu"}      # ¡¡¡FORZAR CPU!!!

# --- Seccion 2: Carga del Conjunto de Datos para Ajuste Fino ---
print("Cargando dataset para ajuste fino...")
# Carga el dataset desde un archivo JSONL local. Se asume que la columna con los mensajes/conversaciones se llama "messages".
dataset = load_dataset("json", data_files=dataset_path, split="train")
print(f"Dataset cargado con {len(dataset)} ejemplos.")


# --- Seccion 3: Carga del Tokenizador y Modelo Base Pre-entrenado (en CPU) ---
print(f"Cargando modelo base en CPU: {base_model_id}")
print("Este proceso puede tardar varios minutos y consumir una cantidad significativa de RAM...")

# Carga el modelo en precision float32, especificamente para CPU.
# No se usa cuantizacion (BitsAndBytesConfig) ya que se ejecuta en CPU y QLoRA no esta optimizado para ello.
model = AutoModelForCausalLM.from_pretrained(
    base_model_id,
    device_map=device_map,      # Forzar carga y ejecucion en CPU.
    trust_remote_code=True,   # Necesario para algunos modelos de Hugging Face.
    torch_dtype=torch.float32 # Usar precision float32 para compatibilidad y operacion en CPU.
)
# Deshabilitar el uso de cache de KV (Key-Value) durante el entrenamiento para ahorrar memoria.
model.config.use_cache = False
# Configuracion relacionada con tensor parallelism, no relevante para CPU single-device.
model.config.pretraining_tp = 1

# Carga el tokenizador correspondiente al modelo base.
print(f"Cargando tokenizador para el modelo: {base_model_id}")
tokenizer = AutoTokenizer.from_pretrained(base_model_id, trust_remote_code=True)
# Configura el token de padding si no esta definido; se usa el token de fin de secuencia (eos_token).
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right" # El padding a la derecha es comun para modelos causales.

# --- Seccion 4: Configuracion de PEFT/LoRA (Parameter-Efficient Fine-Tuning) ---
print("Configurando PEFT/LoRA (para ajuste fino eficiente en CPU)...")
# No se usa 'prepare_model_for_kbit_training' ya que no se aplica cuantizacion de k-bits (QLoRA) para CPU.

# Modulos del modelo objetivo para aplicar las matrices LoRA (varia segun la arquitectura del modelo).
# Estos son nombres comunes para capas de atencion en modelos basados en Transformer.
target_modules = ["qkv_proj", "o_proj", "gate_up_proj", "down_proj"]

peft_config = LoraConfig(
    lora_alpha=lora_alpha,
    lora_dropout=lora_dropout,
    r=lora_r,
    bias="none", # No entrenar los bias con LoRA.
    task_type="CAUSAL_LM", # Tarea de modelado de lenguaje causal.
    target_modules=target_modules
)

# Aplica la configuracion PEFT/LoRA al modelo base.
# Esto modifica el modelo para insertar los adaptadores LoRA en los modulos objetivo.
model = get_peft_model(model, peft_config)

# Muestra el numero de parametros entrenables (deberian ser significativamente menores que los totales del modelo).
model.print_trainable_parameters()


# --- Seccion 5: Configuracion de los Argumentos de Entrenamiento para CPU ---
print("Configurando argumentos de entrenamiento especificos para CPU...")
# Se pasan los parametros previamente definidos y ajustados para CPU.
training_arguments = TrainingArguments(
    output_dir=output_dir,
    num_train_epochs=num_train_epochs,
    per_device_train_batch_size=per_device_train_batch_size,
    gradient_accumulation_steps=gradient_accumulation_steps,
    optim=optim,
    save_steps=save_steps,
    logging_steps=logging_steps,
    learning_rate=learning_rate,
    weight_decay=weight_decay,
    # fp16=False, # fp16 no es optimo ni siempre soportado en CPU.
    # bf16=False, # bf16 generalmente requiere hardware mas especifico (GPUs recientes).
    max_grad_norm=max_grad_norm,
    max_steps=max_steps,
    warmup_ratio=warmup_ratio,
    group_by_length=group_by_length,
    lr_scheduler_type=lr_scheduler_type,
    report_to="tensorboard", # Formato para los logs de metricas.
    gradient_checkpointing=gradient_checkpointing,
    no_cuda=True, # Crucial para asegurar que no intente usar GPU y falle si no esta disponible/configurada.
)

# --- Seccion 6: Inicializacion del SFTTrainer ---
print("Inicializando SFTTrainer (para ajuste fino supervisado en CPU)...")
# SFTTrainer de la libreria TRL simplifica el bucle de entrenamiento para SFT.
trainer = SFTTrainer(
    model=model,                            # El modelo PEFT/LoRA.
    train_dataset=dataset,                  # El conjunto de datos de entrenamiento.
    peft_config=peft_config,                # La configuracion PEFT (redundante si el modelo ya es PeftModel, pero no dana).
    dataset_kwargs={"messages_column": "messages"}, # Especifica la columna del dataset que contiene los mensajes.
    tokenizer=tokenizer,                    # El tokenizador.
    args=training_arguments,                # Los argumentos de entrenamiento.
    max_seq_length=max_seq_length,          # Longitud maxima de secuencia.
    packing=packing,                        # Empaquetado de secuencias (desactivado).
)

# --- Seccion 7: Inicio del Proceso de Entrenamiento en CPU ---
print("Iniciando entrenamiento en CPU... ¡Esto tomara MUCHO tiempo y consumira mucha RAM!")
# Inicia el bucle de entrenamiento.
train_result = trainer.train()

# --- Seccion 8: Guardado de los Resultados del Entrenamiento ---
print("Entrenamiento en CPU completado. Guardando modelo final (adaptador LoRA)...")
# Guarda las metricas del entrenamiento.
metrics = train_result.metrics
trainer.log_metrics("train", metrics)
trainer.save_metrics("train", metrics)

# Guarda el adaptador LoRA entrenado (no el modelo completo, solo los pesos del adaptador).
trainer.save_model(output_dir)

# Guarda el tokenizador para consistencia futura al cargar el adaptador.
tokenizer.save_pretrained(output_dir)
print(f"Adaptador LoRA y tokenizador guardados en el directorio: {output_dir}")

print("¡Proceso de ajuste fino (experimental en CPU) completado!")
