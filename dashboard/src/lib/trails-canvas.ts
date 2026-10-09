import { readOverlayColors } from "./overlay";
import { drawTrailScene, type PaintGrid, type TrailCanvas, type TrailScene } from "./trails-draw";

/** The parts of the Trails picture that need a browser: a canvas to hold the heat grid, and saving as a PNG. */

/** The heat grid on a small canvas of its own, for the picture to scale up when it draws it. */
export const paintGrid: PaintGrid = (pixels, width, height) => {
  const small = document.createElement("canvas");
  small.width = width;
  small.height = height;
  small.getContext("2d")?.putImageData(new ImageData(pixels, width, height), 0, 0);
  return small;
};

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("the picture could not be loaded"));
    image.src = url;
  });
}

/** Lines and labels are designed for a picture about this wide; a bigger one is drawn scaled up. */
const DESIGN_WIDTH = 1000;
const MAX_WIDTH = 1920;
const FALLBACK_SIZE = { width: 1280, height: 720 };

/**
 * The picture as a PNG: the camera's picture (or a dark one if there is none), the paths or the heat
 * map and the lines on it, and a bar along the bottom that says what it shows, so that it still
 * makes sense when it is sent to someone.
 */
export async function renderPicturePng(options: { pictureUrl: string | null; scene: TrailScene; caption: string }): Promise<Blob> {
  let image: HTMLImageElement | null = null;
  if (options.pictureUrl) {
    try {
      image = await loadImage(options.pictureUrl);
    } catch {
      image = null; // the paths are still worth saving
    }
  }
  const natural = image && image.naturalWidth > 0 ? { width: image.naturalWidth, height: image.naturalHeight } : FALLBACK_SIZE;
  const width = Math.min(natural.width, MAX_WIDTH);
  const height = Math.round((width * natural.height) / natural.width);
  const k = width / DESIGN_WIDTH;

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  const layer = document.createElement("canvas");
  layer.width = width;
  layer.height = height;
  const layerCtx = layer.getContext("2d");
  if (!ctx || !layerCtx) throw new Error("this browser cannot draw the picture");

  ctx.fillStyle = "#0b0b0b";
  ctx.fillRect(0, 0, width, height);
  if (image) ctx.drawImage(image, 0, 0, width, height);

  // The paths go on a layer of their own: drawing a scene starts by clearing its canvas.
  layerCtx.scale(k, k);
  drawTrailScene(layerCtx as unknown as TrailCanvas, { width: DESIGN_WIDTH, height: height / k }, options.scene, readOverlayColors(), paintGrid);
  ctx.drawImage(layer, 0, 0);

  const bar = Math.round(30 * k);
  ctx.fillStyle = "rgba(11, 11, 11, 0.72)";
  ctx.fillRect(0, height - bar, width, bar);
  ctx.fillStyle = "#ffffff";
  ctx.font = `600 ${Math.round(14 * k)}px system-ui, -apple-system, 'Segoe UI', sans-serif`;
  ctx.textBaseline = "middle";
  ctx.fillText(options.caption, Math.round(12 * k), height - bar / 2, width - Math.round(24 * k));

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("could not make the picture"))), "image/png");
  });
}

/** Hand a file to the browser's downloads. */
export function download(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
