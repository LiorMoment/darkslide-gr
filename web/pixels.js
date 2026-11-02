"use strict";
// The pixels that go into the camera (the 38×36 icon, the 720×480 shutdown picture),
// made the same in every browser. A canvas resizes with the browser's own filter, which
// differs between Chromium and WebKit and even between WebKit on Intel and Apple chips,
// so the same picture made different firmware. Browsers decode JPEG differently too
// (libjpeg-turbo in Chromium, ImageIO on macOS), so sRGB JPEGs (and ones without a
// colour profile) are decoded by jpegdec.js. Other pictures, and JPEGs in another colour
// space (Display P3 from an iPhone, Adobe RGB...), are decoded by the browser, which turns
// them into sRGB as the page shows them; their colours stay right, their bytes may differ
// per browser as before. The resize and the PNG are plain arithmetic.
(function () {
  // The picture's pixels at full size, RGBA, turned as its EXIF says, like the <img> the
  // page shows (`img`, whose size it must have: crop rectangles are in its pixels).
  async function decode(blob, img) {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (bytes[0] === 0xFF && bytes[1] === 0xD8 && window.DarkslideJpegDecode && srgbJpeg(bytes)) {
      let px = null;
      try {
        const out = window.DarkslideJpegDecode.decode(bytes);
        px = orient(out, out.orientation);
      } catch (e) { /* a JPEG it does not read (CMYK, arithmetic coding...): the browser's */ }
      if (px && (!img || (px.w === img.naturalWidth && px.h === img.naturalHeight))) return px;
    }
    // the browser's own decode error is English text ("The source image could not be decoded."): say it our way
    const bmp = await createImageBitmap(blob, { colorSpaceConversion: "default", premultiplyAlpha: "none", imageOrientation: "from-image" })
      .catch(() => { throw new Error("图片打不开"); });
    if (img && (bmp.width !== img.naturalWidth || bmp.height !== img.naturalHeight)) {
      bmp.close();
      throw new Error("图片打不开");
    }
    const c = document.createElement("canvas");
    c.width = bmp.width; c.height = bmp.height;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(bmp, 0, 0);
    bmp.close();
    return { w: c.width, h: c.height, data: g.getImageData(0, 0, c.width, c.height).data };
  }

  // True when the JPEG has no colour profile, or an RGB profile with sRGB's primaries, or
  // a grey one: then its numbers are already the sRGB the page shows. Any other profile
  // (Display P3, Adobe RGB, a profile without primaries) needs the browser's conversion.
  function srgbJpeg(b) {
    const parts = [];
    for (let i = 2; i + 4 <= b.length && b[i] === 0xFF;) {
      const m = b[i + 1];
      if (m === 0xD9 || m === 0xDA) break;                  // end of image / start of scan
      if (m === 0x01 || (m >= 0xD0 && m <= 0xD7)) { i += 2; continue; }
      const len = (b[i + 2] << 8) | b[i + 3];
      if (len < 2) break;
      if (m === 0xE2 && len >= 16 && String.fromCharCode(...b.subarray(i + 4, i + 16)) === "ICC_PROFILE\0") {
        parts.push([b[i + 16], b.subarray(i + 18, i + 2 + len)]);
      }
      i += 2 + len;
    }
    if (!parts.length) return true;
    parts.sort((x, y) => x[0] - y[0]);
    const icc = new Uint8Array(parts.reduce((n, p) => n + p[1].length, 0));
    let o = 0;
    for (const p of parts) { icc.set(p[1], o); o += p[1].length; }
    if (icc.length < 132) return false;
    const u32 = (k) => ((icc[k] << 24) | (icc[k + 1] << 16) | (icc[k + 2] << 8) | icc[k + 3]) >>> 0;
    const tag = (k) => String.fromCharCode(icc[k], icc[k + 1], icc[k + 2], icc[k + 3]);
    const space = tag(16);
    if (space === "GRAY") return true;
    if (space !== "RGB ") return false;
    const xyz = {};
    for (let t = 0, n = u32(128); t < n && 132 + t * 12 + 12 <= icc.length; t++) {
      const e = 132 + t * 12, sig = tag(e), at = u32(e + 4);
      if ((sig === "rXYZ" || sig === "gXYZ" || sig === "bXYZ") && at + 20 <= icc.length && tag(at) === "XYZ ") {
        xyz[sig] = [0, 1, 2].map((j) => (u32(at + 8 + j * 4) | 0) / 65536);
      }
    }
    // sRGB's primaries adapted to D50, as every sRGB matrix profile writes them
    const want = { rXYZ: [0.4361, 0.2225, 0.0139], gXYZ: [0.3851, 0.7169, 0.0971], bXYZ: [0.1431, 0.0606, 0.7141] };
    return Object.keys(want).every((k) => xyz[k] && want[k].every((v, j) => Math.abs(xyz[k][j] - v) < 0.003));
  }

  // px turned upright for EXIF orientation o (1–8), as browsers show the picture.
  function orient(px, o) {
    if (!(o >= 2 && o <= 8)) return { w: px.w, h: px.h, data: px.data };
    const { w, h } = px, swap = o >= 5, W = swap ? h : w, H = swap ? w : h;
    const out = new Uint8ClampedArray(W * H * 4), d = px.data;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        let sx, sy;
        switch (o) {
          case 2: sx = w - 1 - x; sy = y; break;          // mirrored
          case 3: sx = w - 1 - x; sy = h - 1 - y; break;  // upside down
          case 4: sx = x; sy = h - 1 - y; break;          // mirrored upside down
          case 5: sx = y; sy = x; break;                  // transposed
          case 6: sx = y; sy = h - 1 - x; break;          // turned 90° clockwise
          case 7: sx = w - 1 - y; sy = h - 1 - x; break;  // transversed
          default: sx = w - 1 - y; sy = x;                // 8: turned 90° counter-clockwise
        }
        const s = (sy * w + sx) * 4, t = (y * W + x) * 4;
        out[t] = d[s]; out[t + 1] = d[s + 1]; out[t + 2] = d[s + 2]; out[t + 3] = d[s + 3];
      }
    }
    return { w: W, h: H, data: out };
  }

  // Source pixels and weights for each output pixel along one axis: the share of each
  // source pixel the output pixel covers when shrinking, the two nearest when enlarging.
  function taps(start, span, size, out) {
    const k = span / out, rows = [];
    for (let i = 0; i < out; i++) {
      const row = [];
      if (k >= 1) {
        const a = start + i * k, b = a + k;
        for (let s = Math.max(0, Math.floor(a)); s < Math.min(size, Math.ceil(b)); s++) {
          const w = Math.min(b, s + 1) - Math.max(a, s);
          if (w > 0) row.push(s, w);
        }
      } else {
        const c = Math.min(size - 1, Math.max(0, start + (i + 0.5) * k - 0.5)), s = Math.floor(c), t = c - s;
        row.push(s, 1 - t);
        if (t > 0 && s + 1 < size) row.push(s + 1, t);
      }
      rows.push(row);
    }
    return rows;
  }

  // The rectangle r ({sx, sy, sw, sh} in source pixels) of src resized to w×h, laid over
  // black (transparent parts turn black, as on the camera) and fully opaque.
  function resample(src, r, w, h) {
    const xs = taps(r.sx, r.sw, src.w, w), ys = taps(r.sy, r.sh, src.h, h);
    const out = new Uint8ClampedArray(w * h * 4), d = src.data;
    for (let y = 0; y < h; y++) {
      const ty = ys[y];
      for (let x = 0; x < w; x++) {
        const tx = xs[x];
        let R = 0, G = 0, B = 0, W = 0;
        for (let j = 0; j < ty.length; j += 2) {
          const row = ty[j] * src.w, wy = ty[j + 1];
          for (let i = 0; i < tx.length; i += 2) {
            const p = (row + tx[i]) * 4, wa = wy * tx[i + 1] * d[p + 3] / 255;
            R += d[p] * wa; G += d[p + 1] * wa; B += d[p + 2] * wa;
            W += wy * tx[i + 1];
          }
        }
        const o = (y * w + x) * 4;
        out[o] = Math.round(R / W); out[o + 1] = Math.round(G / W); out[o + 2] = Math.round(B / W); out[o + 3] = 255;
      }
    }
    return out;
  }

  // A PNG of RGBA pixels, byte for byte the same everywhere: stored (uncompressed) deflate
  // blocks, so no compressor's choices are involved. For small images such as icons.
  function png(w, h, rgba) {
    const raw = new Uint8Array(h * (w * 4 + 1));
    for (let y = 0; y < h; y++) raw.set(rgba.subarray(y * w * 4, (y + 1) * w * 4), y * (w * 4 + 1) + 1);
    const blocks = [];
    for (let i = 0; i < raw.length || i === 0; i += 65535) {
      const part = raw.subarray(i, Math.min(raw.length, i + 65535)), last = i + 65535 >= raw.length;
      blocks.push(Uint8Array.of(last ? 1 : 0, part.length & 255, part.length >> 8, ~part.length & 255, (~part.length >> 8) & 255), part);
    }
    let a = 1, b = 0;
    for (const v of raw) { a = (a + v) % 65521; b = (b + a) % 65521; }
    const zlib = concat([Uint8Array.of(0x78, 0x01), ...blocks, u32((b << 16 | a) >>> 0)]);
    const ihdr = concat([u32(w), u32(h), Uint8Array.of(8, 6, 0, 0, 0)]);
    return concat([Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10), chunk("IHDR", ihdr), chunk("IDAT", zlib), chunk("IEND", new Uint8Array(0))]);
  }

  const CRC = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  function crc32(bytes) { let c = 0xFFFFFFFF; for (const v of bytes) c = CRC[(c ^ v) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
  function u32(v) { return Uint8Array.of(v >>> 24, (v >>> 16) & 255, (v >>> 8) & 255, v & 255); }
  function chunk(type, data) {
    const t = Uint8Array.from(type, (ch) => ch.charCodeAt(0));
    return concat([u32(data.length), t, data, u32(crc32(concat([t, data])))]);
  }
  function concat(parts) {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }

  window.DarkslidePixels = { decode, orient, resample, png, srgbJpeg };
})();
