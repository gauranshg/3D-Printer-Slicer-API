# Pookie Slicer API — Usage

Cloud-hosted 3D slicing + price-quoting API. Upload a model, get back print time,
material usage, and a price. Configured for a **Bambu Lab A1** (FDM, 256×256×256 mm).

## Base URL

```
https://pookie-slicer-api.blueforest-ccb9c346.centralindia.azurecontainerapps.io
```

> **Cold start:** the service scales to zero when idle to save cost. The first
> request after a few minutes of inactivity takes **~25–40 s** to wake up; after
> that, quotes return in ~1–2 s. Retry once if the first call times out.

## Quick start — get a price quote

```bash
BASE="https://pookie-slicer-api.blueforest-ccb9c346.centralindia.azurecontainerapps.io"

curl -X POST "$BASE/prusa/slice" \
  -F "choosenFile=@model.stl" \
  -F "layerHeight=0.2" \
  -F "material=PLA" \
  -F "infill=20"
```

Response (abridged):

```json
{
  "success": true,
  "technology": "FDM",
  "material": "PLA",
  "hourly_rate": 800,
  "stats": {
    "print_time_seconds": 420,
    "print_time_readable": "0h 7m",
    "material_used_m": 0.69,
    "object_height_mm": 14.2,
    "estimated_price_huf": 200
  }
}
```

`estimated_price_huf` = price in HUF. It is derived from the print time and the
per-material hourly rate (see Pricing below).

## Public endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | Liveness check (`{status:"OK"}`) |
| GET  | `/pricing` | Current material hourly-rate matrix |
| POST | `/prusa/slice` | Slice + price a model (FDM/SLA) |
| GET  | `/docs` | Swagger UI (interactive) |
| GET  | `/openapi.json` | OpenAPI spec |

> `/orca/slice` exists in the code but is **disabled** in this deployment
> (OrcaSlicer was removed to slim the image). Use `/prusa/slice`.

## Slice request fields (`multipart/form-data`)

| Field | Required | Notes |
|---|---|---|
| `choosenFile` | ✅ | The model file. Max **100 MB**. |
| `layerHeight` | – | `0.1`, `0.2`, `0.3` (FDM) or `0.025`, `0.05` (SLA resin). Default `0.2`. |
| `material` | – | e.g. `PLA`, `ABS`, `PETG`, `TPU`. Default `PLA`. |
| `infill` | – | `0`–`100` (percent). Default `20`. |
| `sizeUnit` | – | `mm` (default) or `inch`. |
| `keepProportions` | – | `true` (default) / `false`. |
| `targetSizeX/Y/Z` | – | Resize to target dimensions (in `sizeUnit`). |
| `scalePercent` | – | Uniform scale; can't combine with `targetSize*`. |
| `rotationX/Y/Z` | – | Rotation in degrees. |
| `printerProfile` | – | Override profile file from `configs/prusa` (e.g. `FDM_0.2mm.ini`). |

Supported input formats: `.stl`, `.obj`, `.3mf` (direct); `.stp`, `.step`,
`.igs`, `.iges`, `.ply` (CAD/NURBS, auto-converted); `.zip` (must contain exactly
one supported model).

## Printer limits (Bambu A1)

- **Build volume:** 256 × 256 × 256 mm. Models larger than this (after any
  scaling) are rejected with `MODEL_OUT_OF_PRINTER_BOUNDS`.
- **Layer heights:** FDM `0.1 / 0.2 / 0.3` mm. (`0.025 / 0.05` route to Prusa SLA
  resin mode — the A1 can't print those, but the endpoint will still quote them.)
- **Nozzle:** 0.4 mm. Supports are auto-generated for overhangs (~45°).
- **Upload cap:** 100 MB per file.
- Time/price estimates use A1 machine limits (10 000 mm/s² accel, 500 mm/s max).

## Pricing

`GET /pricing` returns the current matrix, e.g.:

```json
{ "FDM": { "PLA": 800, "ABS": 800, "PETG": 900, "TPU": 900 },
  "SLA": { "Standard": 1800, "ABS-Like": 1800, "Flexible": 2400 } }
```

Numbers are **HUF per hour**. Quote price ≈ `print_time_hours × hourly_rate`.

Editing prices requires the admin key (see below) and persists across restarts:

```bash
# Update one material
curl -X PATCH "$BASE/pricing/FDM/PLA" \
  -H "x-api-key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"price":950}'

# Add a material
curl -X POST "$BASE/pricing/FDM" \
  -H "x-api-key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"material":"ASA","price":1200}'

# Delete a material
curl -X DELETE "$BASE/pricing/FDM/TPU" -H "x-api-key: $ADMIN_KEY"
```

## Admin endpoints (require `x-api-key`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/detailed` | Subsystem diagnostics (slicer/python/storage/queue) |
| POST/PATCH/DELETE | `/pricing/...` | Manage the pricing matrix |
| GET | `/admin/output-files` | List generated G-code artifacts |
| GET | `/admin/download/:fileName` | Download an artifact (`ALL` = ZIP) |

> Generated G-code is **not persisted** in this deployment (only pricing is), so
> `/admin/output-files` reflects the current live replica only.

Retrieve the admin key (Azure CLI, owner access):

```bash
az containerapp secret show -g PookiePrintsSLicer -n pookie-slicer-api \
  --secret-name admin-api-key --query value -o tsv
```

## Rate limits & queue

- Slicing: **3 requests / 60 s per IP** (burst 5) → `429` with `Retry-After`.
- Admin: 30 requests / 60 s per IP.
- One slice runs at a time (FIFO queue); overflow → `503 SLICE_QUEUE_FULL`.

## Common error codes

`INVALID_LAYER_HEIGHT`, `INVALID_MATERIAL_FOR_TECHNOLOGY`,
`MODEL_OUT_OF_PRINTER_BOUNDS`, `UNSUPPORTED_FILE_FORMAT`,
`INVALID_SOURCE_GEOMETRY`, `RATE_LIMIT_EXCEEDED`, `SLICE_QUEUE_FULL`,
`FILE_PROCESSING_TIMEOUT`.

---

## Operating notes (for maintainers)

**Where settings live:**
1. **Env vars** (rate limits, queue sizes, upload cap, admin key, proxy trust) —
   change with `az containerapp update -g PookiePrintsSLicer -n pookie-slicer-api
   --set-env-vars KEY=VALUE` (rolls a new revision).
2. **Files on the Azure Files share** `configs` (mounted at `/app/configs`) —
   `pricing.json` (edit via the pricing API) and slicer profiles
   `configs/prusa/*.ini`. Upload a changed profile with
   `az storage file upload --account-name pookieslicerst87118 --share-name configs
   --source X.ini --path prusa/X.ini`. Read per request; no restart needed.
3. **Code** (layer-height allowlist, forced supports, bed center, default build
   volume) — edit `app/`, rebuild + push the image, then
   `az containerapp update ... --image ghcr.io/gauranshg/3d-psa:vN`.

**Deployment facts:**
- Image: `ghcr.io/gauranshg/3d-psa:v2` (public, Prusa-only, ~1.4 GB).
- Resource group `PookiePrintsSLicer` (Central India): Container App
  `pookie-slicer-api` (scale 0→1, 2 vCPU / 4 GiB), storage
  `pookieslicerst87118` (share `configs`), Log Analytics `pookie-slicer-logs`.
- Estimated cost: ~$0–1/month (mostly idle).
