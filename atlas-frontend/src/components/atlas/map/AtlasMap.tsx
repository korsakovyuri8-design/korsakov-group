// MapAdapter boundary. Screens depend only on <AtlasMap />.
// The active adapter is an illustrative sketch (design placeholder). To ship a real map,
// implement a MapLibre adapter that reads `place.coords` and assign it to `activeAdapter` —
// no screen needs to change.
import type { ComponentType } from "react";
import type { MapPlace } from "@/lib/atlas/domain";
import { SketchRenderer } from "./SketchRenderer";

export interface MapRendererProps {
  places: MapPlace[];
  /** Ordered place ids to connect with a route line. */
  path?: string[] | undefined;
  selectedId?: string | undefined;
  className?: string;
}

export type MapAdapter = ComponentType<MapRendererProps>;

const activeAdapter: MapAdapter = SketchRenderer;

export function AtlasMap(props: MapRendererProps) {
  const Adapter = activeAdapter;
  return <Adapter {...props} />;
}
