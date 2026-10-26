// Envía un trabajo al motor en RunPod, espera el resultado y calcula cuánto costó.
//
// Uso:
//   node scripts/test-engine.mjs --mode replace --image URL --video URL [--resolution 480p] [--steps 20] [--gpu-price 0.00053]
//
// Variables: RUNPOD_API_KEY y RUNPOD_ENDPOINT_ID (en .env o en la terminal).
import { mkdirSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";

if (existsSync(".env")) {
  for (const line of readFileSync(".env", "utf8").split(/\r?\n/)) {
    const m = line.match(/^([A-Z_]+)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
}

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, all) => (cur.startsWith("--") ? [...acc, [cur.slice(2), all[i + 1]]] : acc), []),
);
const { RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID } = process.env;
if (!RUNPOD_API_KEY || !RUNPOD_ENDPOINT_ID) throw new Error("Faltan RUNPOD_API_KEY y RUNPOD_ENDPOINT_ID.");
if (!args.image || !args.video) throw new Error("Faltan --image y --video (URLs https).");

const api = `https://api.runpod.ai/v2/${RUNPOD_ENDPOINT_ID}`;
const headers = { Authorization: `Bearer ${RUNPOD_API_KEY}`, "Content-Type": "application/json" };
// Precio por segundo de la GPU del endpoint (H100 serverless ≈ 0,00053–0,0013 USD/s según el plan).
const gpuPrice = Number(args["gpu-price"] ?? 0.00053);

const input = {
  mode: args.mode ?? "animate",
  image_url: args.image,
  video_url: args.video,
  resolution: args.resolution ?? "720p",
  steps: Number(args.steps ?? 20),
  fps: Number(args.fps ?? 30),
  retarget: args.retarget === "true",
};

console.log("Enviando trabajo:", input);
const t0 = Date.now();
const { id } = await (await fetch(`${api}/run`, { method: "POST", headers, body: JSON.stringify({ input }) })).json();
if (!id) throw new Error("RunPod no devolvió id de trabajo.");

let job;
for (;;) {
  await new Promise((r) => setTimeout(r, 5000));
  job = await (await fetch(`${api}/status/${id}`, { headers })).json();
  process.stdout.write(`\r${job.status} · ${Math.round((Date.now() - t0) / 1000)} s   `);
  if (["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"].includes(job.status)) break;
}
console.log();

if (job.status !== "COMPLETED" || job.output?.error) {
  console.error("Falló:", job.error ?? job.output?.error ?? job.status);
  process.exit(1);
}

const execS = (job.executionTime ?? 0) / 1000;
const report = {
  ...job.output.metrics,
  queue_s: Math.round((job.delayTime ?? 0) / 1000),
  billed_s: Math.round(execS),
  cost_usd: +(execS * gpuPrice).toFixed(3),
  wall_s: Math.round((Date.now() - t0) / 1000),
  video_url: job.output.video_url,
};
console.table(report);

mkdirSync("out", { recursive: true });
const file = join("out", `${input.mode}-${input.resolution}-${Date.now()}.mp4`);
writeFileSync(file, Buffer.from(await (await fetch(job.output.video_url)).arrayBuffer()));
writeFileSync(file.replace(/\.mp4$/, ".json"), JSON.stringify({ input, report }, null, 2));
console.log("Guardado en", file);
