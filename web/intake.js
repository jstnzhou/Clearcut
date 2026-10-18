/* Property intake: GeoJSON / KML / GPX → selectable polygon features. */
(function (global) {
  function isPair(value) {
    return Array.isArray(value) && value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number";
  }

  function closeRing(ring) {
    if (!ring.length) return ring;
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) return ring.concat([first.slice()]);
    return ring;
  }

  function ringBBox(ring) {
    let minLon = Infinity;
    let minLat = Infinity;
    let maxLon = -Infinity;
    let maxLat = -Infinity;
    ring.forEach(function (pt) {
      if (!isPair(pt)) return;
      minLon = Math.min(minLon, pt[0]);
      minLat = Math.min(minLat, pt[1]);
      maxLon = Math.max(maxLon, pt[0]);
      maxLat = Math.max(maxLat, pt[1]);
    });
    if (!Number.isFinite(minLon)) return null;
    return [minLon, minLat, maxLon, maxLat];
  }

  function geometryToPolygons(geometry) {
    if (!geometry || !geometry.type) return [];
    if (geometry.type === "Polygon") {
      const ring = closeRing((geometry.coordinates && geometry.coordinates[0]) || []);
      return ring.length >= 4 ? [{ type: "Polygon", coordinates: [ring] }] : [];
    }
    if (geometry.type === "MultiPolygon") {
      const out = [];
      (geometry.coordinates || []).forEach(function (poly) {
        const ring = closeRing((poly && poly[0]) || []);
        if (ring.length >= 4) out.push({ type: "Polygon", coordinates: [ring] });
      });
      return out;
    }
    if (geometry.type === "LineString" || geometry.type === "MultiLineString") {
      // Boundary walk: treat closed lines as polygons when first≈last.
      const lines = geometry.type === "LineString" ? [geometry.coordinates] : geometry.coordinates;
      const out = [];
      (lines || []).forEach(function (line) {
        if (!Array.isArray(line) || line.length < 4) return;
        const ring = closeRing(line.map(function (pt) { return [pt[0], pt[1]]; }));
        const first = ring[0];
        const last = ring[ring.length - 1];
        if (first && last && first[0] === last[0] && first[1] === last[1]) {
          out.push({ type: "Polygon", coordinates: [ring] });
        }
      });
      return out;
    }
    return [];
  }

  function featureLabel(feature, index) {
    const props = (feature && feature.properties) || {};
    return props.name || props.Name || props.id || props.plot || ("Feature " + (index + 1));
  }

  function normalizeGeoJSON(raw) {
    let data = raw;
    if (typeof raw === "string") data = JSON.parse(raw);
    if (!data || typeof data !== "object") throw new Error("Not valid GeoJSON.");

    let features = [];
    if (data.type === "FeatureCollection") features = data.features || [];
    else if (data.type === "Feature") features = [data];
    else if (data.type && data.coordinates) features = [{ type: "Feature", properties: {}, geometry: data }];
    else throw new Error("GeoJSON must be a Feature, FeatureCollection, or geometry.");

    const items = [];
    features.forEach(function (feature, index) {
      if (!feature) return;
      const polygons = geometryToPolygons(feature.geometry);
      polygons.forEach(function (geometry, polyIndex) {
        const ring = geometry.coordinates[0];
        const bbox = ringBBox(ring);
        if (!bbox) return;
        const label = featureLabel(feature, index) + (polygons.length > 1 ? " (" + (polyIndex + 1) + ")" : "");
        items.push({
          id: items.length,
          label: label,
          properties: feature.properties || {},
          geometry: geometry,
          bbox: bbox,
          feature: {
            type: "Feature",
            properties: Object.assign({}, feature.properties || {}, { name: label }),
            geometry: geometry
          }
        });
      });
    });
    if (!items.length) throw new Error("No polygon (or closed boundary) found in that file.");
    return items;
  }

  let togeojsonPromise = null;
  function loadTogeojson() {
    if (!togeojsonPromise) {
      togeojsonPromise = import("https://esm.sh/@tmcw/togeojson@5.8.1");
    }
    return togeojsonPromise;
  }

  async function parseXmlFile(text, kind) {
    const mod = await loadTogeojson();
    const doc = new DOMParser().parseFromString(text, "text/xml");
    const parseError = doc.querySelector("parsererror");
    if (parseError) throw new Error("Could not parse the " + kind.toUpperCase() + " file.");
    const converted = kind === "gpx" ? mod.gpx(doc) : mod.kml(doc);
    return normalizeGeoJSON(converted);
  }

  async function parseUpload(file) {
    const name = (file && file.name) || "upload";
    const lower = name.toLowerCase();
    const text = await file.text();
    if (lower.endsWith(".geojson") || lower.endsWith(".json")) {
      return { method: "upload_geojson", items: normalizeGeoJSON(text), filename: name };
    }
    if (lower.endsWith(".kml")) {
      return { method: "upload_kml", items: await parseXmlFile(text, "kml"), filename: name };
    }
    if (lower.endsWith(".gpx")) {
      return { method: "upload_gpx", items: await parseXmlFile(text, "gpx"), filename: name };
    }
    // Sniff content
    const trimmed = text.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      return { method: "upload_geojson", items: normalizeGeoJSON(text), filename: name };
    }
    if (trimmed.indexOf("<kml") !== -1 || trimmed.indexOf("<KML") !== -1) {
      return { method: "upload_kml", items: await parseXmlFile(text, "kml"), filename: name };
    }
    if (trimmed.indexOf("<gpx") !== -1 || trimmed.indexOf("<GPX") !== -1) {
      return { method: "upload_gpx", items: await parseXmlFile(text, "gpx"), filename: name };
    }
    throw new Error("Use a .geojson, .json, .kml, or .gpx file.");
  }

  async function parsePaste(text) {
    return { method: "paste_geojson", items: normalizeGeoJSON(text), filename: "pasted.geojson" };
  }

  function validateBBox(bbox) {
    if (!bbox || bbox.length !== 4) return "The property has no bounds.";
    if (bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return "The property polygon is empty.";
    const width = bbox[2] - bbox[0];
    const height = bbox[3] - bbox[1];
    if (width > 1 || height > 1) return "That property is too large. Keep it under about 1 degree.";
    if (width < 0.0005 && height < 0.0005) return "That property is too small to analyze.";
    return null;
  }

  global.ClearcutIntake = {
    normalizeGeoJSON: normalizeGeoJSON,
    parseUpload: parseUpload,
    parsePaste: parsePaste,
    validateBBox: validateBBox,
    ringBBox: ringBBox
  };
})(window);
