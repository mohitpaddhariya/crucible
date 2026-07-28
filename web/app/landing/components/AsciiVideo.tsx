"use client";

import { useEffect, useRef, useState } from "react";

export type AsciiVideoSource = {
  id: string;
  label: string;
  src: string;
  fallbackSrc?: string;
  poster?: string;
};

type AsciiVideoProps = {
  sources: AsciiVideoSource[];
  ariaLabel: string;
  cellSize?: number;
  characterSet?: string;
  frameRate?: number;
};

const DEFAULT_CHARACTERS = ".`,:;irsXA253hMHGS#9B&@";

export function AsciiVideo({
  sources,
  ariaLabel,
  cellSize = 10,
  characterSet = DEFAULT_CHARACTERS,
  frameRate = 18,
}: AsciiVideoProps) {
  const [activeId, setActiveId] = useState(sources[0]?.id ?? "");
  const hostRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sampleCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const activeSource = sources.find(({ id }) => id === activeId) ?? sources[0];

  useEffect(() => {
    const host = hostRef.current;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!host || !video || !canvas || !activeSource) return;

    const sampleCanvas = sampleCanvasRef.current ?? document.createElement("canvas");
    sampleCanvasRef.current = sampleCanvas;

    const context = canvas.getContext("2d", { alpha: false });
    const sampleContext = sampleCanvas.getContext("2d", {
      alpha: false,
      willReadFrequently: true,
    });
    if (!context || !sampleContext) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const hostStyles = window.getComputedStyle(host);
    const canvasBackground = hostStyles.getPropertyValue("--ascii-background").trim();
    const backgroundGlyph = hostStyles.getPropertyValue("--ascii-grid").trim();
    const frameInterval = 1000 / frameRate;
    let animationFrame = 0;
    let lastFrameAt = 0;
    let disposed = false;

    const resize = () => {
      const { width, height } = host.getBoundingClientRect();
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(width * pixelRatio));
      canvas.height = Math.max(1, Math.round(height * pixelRatio));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.fillStyle = canvasBackground;
      context.fillRect(0, 0, width, height);
    };

    const renderFrame = () => {
      if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth) return;

      const width = host.clientWidth;
      const height = host.clientHeight;
      const responsiveCellSize = width < 700 ? Math.max(7, cellSize - 2) : cellSize;
      const cellHeight = responsiveCellSize * 1.25;
      const columns = Math.max(1, Math.ceil(width / responsiveCellSize));
      const rows = Math.max(1, Math.ceil(height / cellHeight));

      if (sampleCanvas.width !== columns || sampleCanvas.height !== rows) {
        sampleCanvas.width = columns;
        sampleCanvas.height = rows;
      }

      const videoRatio = video.videoWidth / video.videoHeight;
      const hostRatio = width / height;
      let sourceX = 0;
      let sourceY = 0;
      let sourceWidth = video.videoWidth;
      let sourceHeight = video.videoHeight;

      if (videoRatio > hostRatio) {
        sourceWidth = video.videoHeight * hostRatio;
        const horizontalPosition = width < 700 ? 0.48 : 0.5;
        sourceX = (video.videoWidth - sourceWidth) * horizontalPosition;
      } else {
        sourceHeight = video.videoWidth / hostRatio;
        sourceY = (video.videoHeight - sourceHeight) / 2;
      }

      sampleContext.drawImage(
        video,
        sourceX,
        sourceY,
        sourceWidth,
        sourceHeight,
        0,
        0,
        columns,
        rows,
      );

      const pixels = sampleContext.getImageData(0, 0, columns, rows).data;
      context.fillStyle = canvasBackground;
      context.fillRect(0, 0, width, height);
      context.font = `${cellHeight}px "SFMono-Regular", Consolas, "Liberation Mono", monospace`;
      context.textBaseline = "top";

      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const offset = (row * columns + column) * 4;
          const red = pixels[offset];
          const green = pixels[offset + 1];
          const blue = pixels[offset + 2];
          const maximum = Math.max(red, green, blue);
          const minimum = Math.min(red, green, blue);
          const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
          const saturation = (maximum - minimum) / 255;
          const intensity = Math.min(1, (1 - luminance) * 0.95 + saturation * 1.15);
          const characterIndex = Math.min(
            characterSet.length - 1,
            Math.floor(intensity * (characterSet.length - 1)),
          );
          const character = characterSet[characterIndex];

          if (!character) continue;

          if (saturation < 0.035 && luminance > 0.92) {
            context.fillStyle = backgroundGlyph;
          } else {
            const average = (red + green + blue) / 3;
            const enhance = (channel: number) =>
              Math.max(
                0,
                Math.min(
                  255,
                  Math.round((average + (channel - average) * 1.45 - 128) * 1.1 + 128),
                ),
              );
            context.fillStyle = `rgb(${enhance(red)} ${enhance(green)} ${enhance(blue)})`;
          }
          context.fillText(character, column * responsiveCellSize, row * cellHeight);
        }
      }
    };

    const animate = (time: number) => {
      if (disposed) return;
      if (time - lastFrameAt >= frameInterval) {
        renderFrame();
        lastFrameAt = time;
      }
      animationFrame = window.requestAnimationFrame(animate);
    };

    const start = () => {
      resize();
      renderFrame();
      if (!reducedMotion) {
        void video.play().catch(() => undefined);
        animationFrame = window.requestAnimationFrame(animate);
      }
    };

    const handleVisibility = () => {
      if (document.hidden) {
        video.pause();
      } else if (!reducedMotion) {
        void video.play().catch(() => undefined);
      }
    };

    const observer = new ResizeObserver(() => {
      resize();
      renderFrame();
    });
    observer.observe(host);
    video.addEventListener("loadeddata", start);
    document.addEventListener("visibilitychange", handleVisibility);

    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) start();

    return () => {
      disposed = true;
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
      video.removeEventListener("loadeddata", start);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [activeSource, cellSize, characterSet, frameRate]);

  if (!activeSource) return null;

  return (
    <div className="ascii-video" ref={hostRef}>
      <video
        key={activeSource.id}
        ref={videoRef}
        className="ascii-video-source"
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        poster={activeSource.poster}
        aria-hidden="true"
      >
        <source src={activeSource.src} type="video/webm" />
        {activeSource.fallbackSrc && <source src={activeSource.fallbackSrc} type="video/mp4" />}
      </video>
      <canvas className="ascii-video-canvas" ref={canvasRef} role="img" aria-label={ariaLabel} />

      {sources.length > 1 && (
        <div className="ascii-video-selector" aria-label="Choose animation">
          {sources.map((source) => (
            <button
              className={source.id === activeSource.id ? "active" : ""}
              key={source.id}
              type="button"
              aria-pressed={source.id === activeSource.id}
              onClick={() => setActiveId(source.id)}
            >
              {source.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
