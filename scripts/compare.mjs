// Pone dos o más videos lado a lado, con su nombre encima, para comparar resultados.
//
// Uso:
//   node scripts/compare.mjs out/comparacion.mp4 "Original=antes.mp4" "Genjutsu=genjutsu.mp4" "Mutare=out/replace.mp4"
//
// Necesita ffmpeg instalado.
import { execFileSync } from "node:child_process";

const [output, ...pairs] = process.argv.slice(2);
if (!output || pairs.length < 2) throw new Error('Uso: compare.mjs salida.mp4 "Nombre=video.mp4" "Nombre=video.mp4" ...');

const items = pairs.map((p) => {
  const i = p.indexOf("=");
  return { label: p.slice(0, i), file: p.slice(i + 1) };
});
const font = process.platform === "win32" ? ":fontfile='C\\:/Windows/Fonts/arialbd.ttf'" : "";
const HEIGHT = 720;

const inputs = items.flatMap((it) => ["-i", it.file]);
const scaled = items
  .map(
    (it, i) =>
      `[${i}:v]fps=24,scale=-2:${HEIGHT},setsar=1,` +
      `drawtext=text='${it.label.replace(/'/g, "")}'${font}:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=10:x=16:y=16[v${i}]`,
  )
  .join(";");
const stack = `${items.map((_, i) => `[v${i}]`).join("")}hstack=inputs=${items.length}:shortest=1[out]`;

execFileSync(
  "ffmpeg",
  ["-v", "error", "-y", ...inputs, "-filter_complex", `${scaled};${stack}`, "-map", "[out]", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", output],
  { stdio: "inherit" },
);
console.log("Comparación guardada en", output);
