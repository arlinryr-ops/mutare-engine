# Motor de Mutare — Fase 1

El motor propio de Mutare: **Wan 2.2 Animate** (pesos abiertos, licencia Apache 2.0) corriendo en
GPUs alquiladas por segundo en **RunPod Serverless**. Es lo que en el futuro estará detrás de la API de Mutare,
en lugar de fal.ai.

| Modo | Qué hace | Entrada |
|---|---|---|
| `animate` | Tu personaje (foto) hace el movimiento del video. Fondo de la foto. | `image_url` + `video_url` |
| `replace` | La persona del video pasa a ser la de la foto. Mismo escenario, cámara y luz. | `image_url` + `video_url` |

El cambio de **objetos** (lata → perfume) no está en esta fase: usa otro modelo (Wan VACE) y va en la siguiente.

Decisiones ya tomadas:
- **Sin FLUX Kontext** en el preprocesado: su licencia (FLUX.1-dev) no permite uso comercial.
- El modelo de 14B se **carga una vez por worker** y se reutiliza entre trabajos.
- Los resultados se publican en tu Supabase, en el bucket público `mutare-outputs` (ya creado).

## Qué se necesita

| Cosa | Para qué | Costo aproximado |
|---|---|---|
| Cuenta de GitHub | RunPod construye la imagen desde tu repositorio | Gratis |
| Cuenta de RunPod (acepta cripto, pide verificación) | GPUs por segundo | Recarga desde 10 USD |
| Volumen de red de 120 GB en RunPod | Guardar los pesos del modelo (~60–80 GB) | ≈ 8–9 USD/mes |
| GPU de 80 GB (H100 o A100) | Correr el modelo de 14B | ≈ 0,0005–0,0013 USD/s, solo mientras trabaja |

## Pasos

1. **Sube esta carpeta a un repositorio de GitHub** (privado), por ejemplo `mutare-engine`.
2. **RunPod → Storage → New Network Volume**: 120 GB, en un centro de datos que tenga H100 o A100 80 GB.
3. **RunPod → Serverless → New Endpoint → GitHub Repo**: elige tu repositorio y la rama `main`.
   - GPU: **80 GB** (H100 o A100). Container disk: 30 GB.
   - Network volume: el del paso 2 (se monta en `/runpod-volume`).
   - Max workers: 1 · Idle timeout: 5 s · Execution timeout: 3600 s (la primera vez descarga el modelo).
   - Environment variables: `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (la misma de la app), `OUTPUT_BUCKET=mutare-outputs`.
4. Copia el **Endpoint ID** y crea una **API Key** en RunPod → Settings. Ponlas en `.env` (copia de `.env.example`).
5. **Primera prueba** (tarda más: descarga ~70 GB al volumen):
   ```
   node scripts/test-engine.mjs --mode replace --resolution 480p --image URL_FOTO --video URL_VIDEO
   ```
6. **Comparación con Genjutsu** (mismos ingredientes que la portada):
   ```
   node scripts/compare.mjs out/vs-genjutsu.mp4 "Original=antes.mp4" "Genjutsu=genjutsu.mp4" "Mutare=out/replace-480p-XXXX.mp4"
   ```

`test-engine.mjs` imprime una tabla con tiempos (preprocesado, carga, generación), segundos facturados y costo en USD.
Pasa `--gpu-price` con el precio por segundo real de tu GPU en RunPod.

## Qué medir (y cómo decidir)

| Métrica | fal.ai (referencia) | Genjutsu | Mutare (motor propio) |
|---|---|---|---|
| Costo por video de 4–5 s, 720p | 0,49–0,75 USD | ≈ 1,2–1,7 USD al cliente | _medir_ |
| Tiempo hasta el resultado | 2–6 min | 2–5 min | _medir_ |
| Fidelidad de cara y ropa | — | Muy buena | _comparar_ |
| Movimiento, manos, parpadeo | — | Muy bueno | _comparar_ |

Si la calidad es comparable y el costo por video baja de lo que cobra fal.ai, el motor está listo para la Fase 2 (API de Mutare).

## Siguientes optimizaciones

- Fijar el commit de Wan (`WAN_REF`) para que una actualización no rompa el motor.
- Bajar costo: menos pasos (`--steps 12`), 480p, `fps` 24, versión FP8 del modelo.
- Guardar los pesos en la imagen o usar caché de modelos de RunPod para arranques en frío más rápidos.
- Añadir el modo `object` con Wan VACE.
