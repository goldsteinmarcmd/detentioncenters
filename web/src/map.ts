/**
 * MapLibre setup.
 *
 * Clusters are sized by summed average daily population rather than facility count, so
 * a cluster's visual weight tracks the number of people held rather than the number of
 * dots. A county jail holding three people and a 2,200-bed private prison should not
 * look alike.
 */

import maplibregl, { type GeoJSONSource, type Map as MLMap } from 'maplibre-gl';
import type { FacilityProps } from './types';

const SOURCE = 'facilities';

/** CARTO's Positron raster tiles — no API key, deliberately low-contrast so the
 *  facility markers carry the visual weight. Swapped for self-hosted Protomaps
 *  pmtiles when the Devvit port needs a single allowlisted domain. */
const BASEMAP_TILES = [
  'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
  'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
  'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
];

const COLOR_PASS = '#2f7d5d';
const COLOR_FAIL = '#c0392b';
const COLOR_NONE = '#8a8f98';

/** Paint colour by inspection result — the accountability signal people come for. */
const RATING_COLOR: maplibregl.ExpressionSpecification = [
  'case',
  ['==', ['get', 'rating'], null],
  COLOR_NONE,
  ['in', 'Fail', ['coalesce', ['get', 'rating'], '']],
  COLOR_FAIL,
  ['in', 'Pass', ['coalesce', ['get', 'rating'], '']],
  COLOR_PASS,
  COLOR_NONE,
];

export function createMap(container: HTMLElement): MLMap {
  return new maplibregl.Map({
    container,
    style: {
      version: 8,
      sources: {
        basemap: {
          type: 'raster',
          tiles: BASEMAP_TILES,
          tileSize: 256,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
            'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        },
      },
      layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
    },
    center: [-97, 38.5],
    zoom: 3.4,
    maxZoom: 16,
    attributionControl: { compact: true },
  });
}

export function addFacilityLayers(map: MLMap, data: GeoJSON.FeatureCollection): void {
  map.addSource(SOURCE, {
    type: 'geojson',
    data,
    cluster: true,
    clusterRadius: 46,
    clusterMaxZoom: 11,
    // Summed ADP travels with the cluster so its size can reflect people, not points.
    clusterProperties: {
      adp_sum: ['+', ['coalesce', ['get', 'adp'], 0]],
      fail_count: ['+', ['case', ['in', 'Fail', ['coalesce', ['get', 'rating'], '']], 1, 0]],
    },
  });

  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: SOURCE,
    filter: ['has', 'point_count'],
    paint: {
      // Any cluster containing a failed facility carries the warning colour up.
      'circle-color': ['case', ['>', ['get', 'fail_count'], 0], '#b8563f', '#3d6a8f'],
      'circle-opacity': 0.82,
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#ffffff',
      'circle-radius': [
        'interpolate',
        ['linear'],
        ['sqrt', ['max', ['get', 'adp_sum'], 1]],
        1,
        13,
        20,
        22,
        60,
        34,
        120,
        46,
      ],
    },
  });

  map.addLayer({
    id: 'cluster-count',
    type: 'symbol',
    source: SOURCE,
    filter: ['has', 'point_count'],
    layout: {
      'text-field': ['number-format', ['get', 'point_count'], {}],
      'text-font': ['Open Sans Semibold'],
      'text-size': 12,
      'text-allow-overlap': true,
    },
    paint: { 'text-color': '#ffffff' },
  });

  map.addLayer({
    id: 'facility',
    type: 'circle',
    source: SOURCE,
    filter: ['!', ['has', 'point_count']],
    paint: {
      // A derived coordinate is drawn hollow: the fill is what claims "here", so a pin
      // that only knows the city gets an outline and a translucent centre instead.
      'circle-color': RATING_COLOR,
      'circle-opacity': ['case', ['==', ['get', 'approx'], 1], 0.18, 0.9],
      'circle-stroke-width': ['case', ['==', ['get', 'approx'], 1], 1.6, 1.2],
      'circle-stroke-color': ['case', ['==', ['get', 'approx'], 1], RATING_COLOR, '#ffffff'],
      'circle-radius': [
        'interpolate',
        ['linear'],
        ['sqrt', ['max', ['coalesce', ['get', 'adp'], 0], 1]],
        1,
        4.5,
        10,
        9,
        30,
        16,
        50,
        22,
      ],
    },
  });

  map.addLayer({
    id: 'facility-selected',
    type: 'circle',
    source: SOURCE,
    filter: ['==', ['get', 'code'], '__none__'],
    paint: {
      'circle-radius': 20,
      'circle-color': 'transparent',
      'circle-stroke-width': 3,
      'circle-stroke-color': '#1b1b1f',
    },
  });
}

export function setSelected(map: MLMap, code: string | null): void {
  if (!map.getLayer('facility-selected')) return;
  map.setFilter('facility-selected', ['==', ['get', 'code'], code ?? '__none__']);
}

export function setVisible(map: MLMap, codes: Set<string> | null): void {
  const source = map.getSource(SOURCE) as GeoJSONSource | undefined;
  if (!source) return;
  const filter: maplibregl.FilterSpecification | null = codes
    ? ['in', ['get', 'code'], ['literal', [...codes]]]
    : null;
  for (const layer of ['facility']) {
    map.setFilter(layer, filter ? ['all', ['!', ['has', 'point_count']], filter] : ['!', ['has', 'point_count']]);
  }
}

export function zoomToFacility(map: MLMap, coords: [number, number]): void {
  map.easeTo({ center: coords, zoom: Math.max(map.getZoom(), 11), duration: 700 });
}

export function onClusterClick(map: MLMap): void {
  map.on('click', 'clusters', async (e) => {
    const feature = map.queryRenderedFeatures(e.point, { layers: ['clusters'] })[0];
    if (!feature) return;
    const source = map.getSource(SOURCE) as GeoJSONSource;
    const zoom = await source.getClusterExpansionZoom(feature.properties.cluster_id);
    map.easeTo({
      center: (feature.geometry as GeoJSON.Point).coordinates as [number, number],
      zoom,
    });
  });
}

export function facilityAt(
  map: MLMap,
  point: maplibregl.Point,
): FacilityProps | null {
  const hit = map.queryRenderedFeatures(point, { layers: ['facility'] })[0];
  return hit ? (hit.properties as unknown as FacilityProps) : null;
}
