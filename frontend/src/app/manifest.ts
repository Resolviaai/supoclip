import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "SupoClip",
    short_name: "SupoClip",
    description: "Turn long videos into viral vertical clips",
    start_url: "/",
    display: "standalone",
    background_color: "#070a0f",
    theme_color: "#070a0f",
    orientation: "portrait",
    icons: [
      {
        src: "/icon.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icon.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
