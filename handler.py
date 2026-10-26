"""Motor de Mutare (Fase 1): Wan 2.2 Animate como worker serverless de RunPod.

Recibe una foto y un video, y devuelve el video con el movimiento transferido
(modo "animate") o con la persona del video reemplazada (modo "replace").
El modelo grande se carga una sola vez por worker y se reutiliza entre trabajos.
"""

import gc
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import requests
import runpod

WAN_DIR = os.environ.get("WAN_DIR", "/opt/Wan2.2")
MODEL_DIR = os.environ.get("MODEL_DIR", "/runpod-volume/models/Wan2.2-Animate-14B")
WORK_ROOT = os.environ.get("WORK_ROOT", "/tmp/mutare")
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_SECONDS = float(os.environ.get("MAX_SECONDS", "30"))

# Área de procesamiento por resolución; Wan respeta la proporción del video de entrada.
RESOLUTIONS = {"480p": (832, 480), "720p": (1280, 720)}

# Dónde se publica el resultado (Supabase Storage, bucket público).
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
BUCKET = os.environ.get("OUTPUT_BUCKET", "mutare-outputs")

sys.path.insert(0, WAN_DIR)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("mutare-engine")

_pipeline = None  # (relighting_lora: bool, WanAnimate)


def ensure_models() -> None:
    """Descarga los pesos al volumen de red la primera vez (~decenas de GB)."""
    if os.path.isdir(os.path.join(MODEL_DIR, "process_checkpoint")):
        return
    from huggingface_hub import snapshot_download

    log.info("Descargando Wan-AI/Wan2.2-Animate-14B en %s", MODEL_DIR)
    snapshot_download("Wan-AI/Wan2.2-Animate-14B", local_dir=MODEL_DIR)


def get_pipeline(relighting: bool):
    """Carga WanAnimate una vez. El modo reemplazo usa la LoRA de iluminación, así que si
    cambia el modo se recarga (no caben dos copias del modelo de 14B en una GPU)."""
    global _pipeline
    if _pipeline and _pipeline[0] == relighting:
        return _pipeline[1]

    import torch
    import wan
    from wan.configs import WAN_CONFIGS

    if _pipeline:
        _pipeline = None
        gc.collect()
        torch.cuda.empty_cache()

    log.info("Cargando WanAnimate (relighting_lora=%s)", relighting)
    pipe = wan.WanAnimate(
        config=WAN_CONFIGS["animate-14B"],
        checkpoint_dir=MODEL_DIR,
        device_id=0,
        rank=0,
        t5_fsdp=False,
        dit_fsdp=False,
        use_sp=False,
        t5_cpu=False,
        convert_model_dtype=True,
        use_relighting_lora=relighting,
    )
    _pipeline = (relighting, pipe)
    return pipe


def download(url: str, folder: str, stem: str) -> str:
    if urlparse(url).scheme != "https":
        raise ValueError("Las URLs de entrada deben ser https.")
    ext = os.path.splitext(urlparse(url).path)[1].lower() or ".bin"
    path = os.path.join(folder, stem + ext)
    size = 0
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_INPUT_BYTES:
                    raise ValueError("El archivo supera 200 MB.")
                f.write(chunk)
    return path


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets,r_frame_rate:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    num, den = data["streams"][0]["r_frame_rate"].split("/")
    return {
        "seconds": float(data["format"]["duration"]),
        "frames": int(data["streams"][0]["nb_read_packets"]),
        "fps": float(num) / float(den),
    }


def has_audio(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


def run(cmd: list, cwd: str) -> float:
    start = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-2000:]
        raise RuntimeError(f"Falló {os.path.basename(cmd[1])}: {tail}")
    return time.time() - start


def upload(path: str, key: str) -> str:
    if not (SUPABASE_URL and SUPABASE_KEY):
        raise RuntimeError("Faltan SUPABASE_URL / SUPABASE_SECRET_KEY para publicar el resultado.")
    with open(path, "rb") as f:
        r = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}",
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "apikey": SUPABASE_KEY,
                "Content-Type": "video/mp4",
                "x-upsert": "true",
            },
            data=f,
            timeout=300,
        )
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"


def handler(job):
    started = time.time()
    inp = job.get("input") or {}
    mode = inp.get("mode", "animate")
    if mode not in ("animate", "replace"):
        return {"error": "mode debe ser 'animate' o 'replace'."}
    if not inp.get("image_url") or not inp.get("video_url"):
        return {"error": "Faltan image_url y video_url."}
    resolution = inp.get("resolution", "720p")
    if resolution not in RESOLUTIONS:
        return {"error": "resolution debe ser '480p' o '720p'."}
    fps = max(8, min(30, int(inp.get("fps", 30))))
    steps = max(4, min(40, int(inp.get("steps", 20))))
    # El retarget básico (sin FLUX) solo ayuda si la foto está de frente y con los brazos abiertos.
    retarget = bool(inp.get("retarget", False)) and mode == "animate"
    seed = int(inp.get("seed", -1))

    work = os.path.join(WORK_ROOT, job.get("id") or uuid.uuid4().hex)
    proc_dir = os.path.join(work, "process")
    os.makedirs(proc_dir, exist_ok=True)
    try:
        ensure_models()
        video = download(inp["video_url"], work, "driving")
        image = download(inp["image_url"], work, "reference")
        src = probe(video)
        if src["seconds"] > MAX_SECONDS + 0.5:
            return {"error": f"El video dura {src['seconds']:.1f} s; el máximo es {MAX_SECONDS:.0f} s."}

        width, height = RESOLUTIONS[resolution]
        pre_cmd = [
            sys.executable, "preprocess_data.py",
            "--ckpt_path", os.path.join(MODEL_DIR, "process_checkpoint"),
            "--video_path", video,
            "--refer_path", image,
            "--save_path", proc_dir,
            "--resolution_area", str(width), str(height),
            "--fps", str(fps),
        ]
        if mode == "replace":
            pre_cmd += ["--replace_flag", "--iterations", "3", "--k", "7", "--w_len", "1", "--h_len", "1"]
        elif retarget:
            # Sin --use_flux: FLUX.1-Kontext-dev no permite uso comercial.
            pre_cmd += ["--retarget_flag"]
        preprocess_s = run(pre_cmd, cwd=os.path.join(WAN_DIR, "wan", "modules", "animate", "preprocess"))

        import torch
        from wan.configs import WAN_CONFIGS
        from wan.utils.utils import save_video

        cfg = WAN_CONFIGS["animate-14B"]
        load_start = time.time()
        pipe = get_pipeline(relighting=(mode == "replace"))
        load_s = time.time() - load_start

        gen_start = time.time()
        frames = pipe.generate(
            src_root_path=proc_dir,
            replace_flag=(mode == "replace"),
            refert_num=1,
            clip_len=cfg.frame_num,
            shift=cfg.sample_shift,
            sample_solver="unipc",
            sampling_steps=steps,
            guide_scale=cfg.sample_guide_scale,
            seed=seed,
            offload_model=False,
        )
        generate_s = time.time() - gen_start

        raw = os.path.join(work, "raw.mp4")
        # Se guarda al mismo fps con el que se muestreó el video de entrada para no acelerarlo.
        save_video(tensor=frames[None], save_file=raw, fps=fps, nrow=1, normalize=True, value_range=(-1, 1))

        final = os.path.join(work, "final.mp4")
        audio = ["-i", video, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"] if has_audio(video) else ["-an"]
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", raw, *audio, "-c:v", "libx264", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", final],
            check=True,
        )
        out = probe(final)
        url = upload(final, f"{time.strftime('%Y/%m/%d')}/{uuid.uuid4().hex}.mp4")

        return {
            "video_url": url,
            "metrics": {
                "mode": mode,
                "resolution": resolution,
                "steps": steps,
                "input_seconds": round(src["seconds"], 2),
                "output_seconds": round(out["seconds"], 2),
                "output_frames": out["frames"],
                "preprocess_s": round(preprocess_s, 1),
                "model_load_s": round(load_s, 1),
                "generate_s": round(generate_s, 1),
                "total_s": round(time.time() - started, 1),
                "gpu": torch.cuda.get_device_name(0),
            },
        }
    except Exception as e:  # noqa: BLE001 — el error vuelve al cliente en vez de matar el worker
        log.exception("Trabajo fallido")
        return {"error": str(e)[-2000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
