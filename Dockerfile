# Motor de Mutare: Wan 2.2 Animate sobre RunPod Serverless.
# Los pesos (decenas de GB) NO van en la imagen: se descargan una vez a un volumen de red.
FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    WAN_DIR=/opt/Wan2.2 \
    MODEL_DIR=/runpod-volume/models/Wan2.2-Animate-14B \
    HF_HOME=/runpod-volume/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Código oficial de Wan 2.2 (Apache 2.0). Fijar un commit concreto antes de producción.
ARG WAN_REF=main
RUN git clone https://github.com/Wan-Video/Wan2.2.git ${WAN_DIR} && cd ${WAN_DIR} && git checkout ${WAN_REF}

# Dependencias de Wan sin flash_attn (se instala aparte desde una rueda precompilada).
RUN grep -v '^flash_attn' ${WAN_DIR}/requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt

# FlashAttention 2 es obligatorio para Wan Animate. Rueda precompilada para torch 2.4 + CUDA 12 + Python 3.11.
# Si la URL no coincide con tu versión de Python/torch, busca la correcta en
# https://github.com/Dao-AILab/flash-attention/releases
ARG FLASH_ATTN_WHL=https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.4cxx11abiFALSE-cp311-cp311-linux_x86_64.whl
RUN pip install --no-cache-dir ${FLASH_ATTN_WHL}

# Extras del preprocesado de Animate (pose, máscara) y del worker.
RUN pip install --no-cache-dir peft decord onnxruntime-gpu hydra-core omegaconf loguru einops \
        huggingface_hub runpod requests \
    && pip install --no-cache-dir "git+https://github.com/facebookresearch/sam2.git"

COPY handler.py /opt/mutare/handler.py
WORKDIR /opt/mutare
CMD ["python", "-u", "handler.py"]
