"use client";

import { useRef, useState } from "react";
import { Film } from "lucide-react";
import { cn } from "@/lib/utils";

/** Silent video poster that previews on hover — used for generation and clip thumbnails. */
export function ClipCover({ src, className, children }: {
  src: string | null;
  className?: string;
  children?: React.ReactNode;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  const play = (event: React.PointerEvent) => {
    if (event.pointerType !== "mouse") return;
    void videoRef.current?.play().catch(() => {});
  };
  const stop = (event: React.PointerEvent) => {
    if (event.pointerType !== "mouse") return;
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = 0.5;
  };

  return (
    <div
      className={cn("relative overflow-hidden bg-stone-900", className)}
      onPointerEnter={play}
      onPointerLeave={stop}
    >
      {(!src || failed || !ready) && (
        <div className="absolute inset-0 flex items-center justify-center bg-[radial-gradient(circle_at_30%_20%,oklch(0.32_0.015_50),oklch(0.16_0.005_50))]">
          <Film className="size-6 text-white/25" aria-hidden />
        </div>
      )}
      {src && !failed && (
        <video
          ref={videoRef}
          src={`${src}#t=0.5`}
          muted
          loop
          playsInline
          preload="metadata"
          onLoadedMetadata={(e) => {
            const video = e.currentTarget;
            if (video.currentTime < 0.1 && video.duration > 0.6) {
              try { video.currentTime = 0.5; } catch { /* ignore seek errors */ }
            }
          }}
          onLoadedData={() => setReady(true)}
          onSeeked={() => setReady(true)}
          onError={() => setFailed(true)}
          className={cn(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-300",
            ready ? "opacity-100" : "opacity-0",
          )}
          aria-hidden
          tabIndex={-1}
        />
      )}
      {children}
    </div>
  );
}

export function ScoreRing({ score, size = 36, className }: { score: number; size?: number; className?: string }) {
  const stroke = 3;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <span
      className={cn("relative flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Virality score ${clamped} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} className="stroke-current opacity-20" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped / 100)}
          className={cn("transition-[stroke-dashoffset] duration-700", clamped >= 80 ? "stroke-brand" : clamped >= 60 ? "stroke-amber-400" : "stroke-current")}
        />
      </svg>
      <span className="absolute text-[11px] font-semibold tabular-nums">{clamped}</span>
    </span>
  );
}
