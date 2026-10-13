# `GET /fishdex`: species reference image

Every species in `caughtSpecies[]` and `regionSpeciesPool[]` carries an `image` object: the
one reference illustration every viewer sees for that species, the same for everyone. It is
separate from `representativePhotoUrl`, which is the user's own catch photo.

| Field | Type | Meaning |
|---|---|---|
| `url` | string | Carousel image, 960 x 660 transparent WebP. A PNG sits beside it (same name, `.png`). |
| `thumbUrl` | string | Tile image, 500 x 344 transparent WebP (PNG beside it). |
| `status` | `"approved"` or `"placeholder"` | `approved`: a licensed illustration of this species. `placeholder`: the shared generic silhouette. |
| `creator` | string or null | Who made the illustration, as the source page states it. `null` for placeholders. |
| `licence` | string or null | The licence sentence as the source page states it. `null` for placeholders. |

Notes for the UI:

- URLs are relative to the API origin and served by the existing `/static` mount. Resolve them
  the way `resolvePhotoUrl` resolves photo URLs.
- Every approved image is a transparent cut-out on the same 960 x 660 canvas (500 x 344 for
  tiles), every fish faces left, and the whole fish sits inside the central 80% x 60%. Show it
  with `contain`, never a crop.
- A placeholder is one shared SVG (`/static/species/silhouette.svg`): `url` and `thumbUrl` are
  identical. Check `status` to tell it from an illustration, not the URL.
- The field is always present. A caught species the species list does not know gets the
  placeholder, so there is one shape to read.
- Attribution for every approved image is at `/static/species-credits.html`.

## Example

Real output (Smallmouth Bass has an illustration, Creek Chub and Splake do not). Trimmed to
two species per list.

```json
{
  "caughtSpecies": [
    {
      "id": "Micropterus dolomieu",
      "commonName": "Smallmouth Bass",
      "scientificName": "Micropterus dolomieu",
      "family": "Centrarchidae",
      "familyCommonName": "Bass & Sunfish",
      "count": 1,
      "maxLengthInches": null,
      "isPersonalBest": false,
      "personalBestLength": null,
      "isNewFind": true,
      "representativePhotoUrl": null,
      "image": {
        "url": "/static/species/micropterus-dolomieu-carousel.webp",
        "thumbUrl": "/static/species/micropterus-dolomieu-tile.webp",
        "status": "approved",
        "creator": "Duane Raver, U.S. Fish and Wildlife Service",
        "licence": "Public domain: This image or recording is the work of a U.S. Fish and Wildlife Service employee, taken or made as part of that person's official duties. As a work of the U.S. federal government, the image is in the public domain. [Commons tag PD-USGov-FWS]"
      }
    },
    {
      "id": "Semotilus atromaculatus",
      "commonName": "Creek Chub",
      "scientificName": "Semotilus atromaculatus",
      "family": "Cyprinidae",
      "familyCommonName": "Minnows & Chubs",
      "count": 1,
      "maxLengthInches": null,
      "isPersonalBest": false,
      "personalBestLength": null,
      "isNewFind": true,
      "representativePhotoUrl": null,
      "image": {
        "url": "/static/species/silhouette.svg",
        "thumbUrl": "/static/species/silhouette.svg",
        "status": "placeholder",
        "creator": null,
        "licence": null
      }
    }
  ],
  "regionSpeciesPool": [
    {
      "id": "Micropterus dolomieu",
      "commonName": "Smallmouth Bass",
      "scientificName": "Micropterus dolomieu",
      "family": "Centrarchidae",
      "familyCommonName": "Bass & Sunfish",
      "image": {
        "url": "/static/species/micropterus-dolomieu-carousel.webp",
        "thumbUrl": "/static/species/micropterus-dolomieu-tile.webp",
        "status": "approved",
        "creator": "Duane Raver, U.S. Fish and Wildlife Service",
        "licence": "Public domain: This image or recording is the work of a U.S. Fish and Wildlife Service employee, taken or made as part of that person's official duties. As a work of the U.S. federal government, the image is in the public domain. [Commons tag PD-USGov-FWS]"
      }
    },
    {
      "id": "Salvelinus fontinalis × Salvelinus namaycush",
      "commonName": "Splake",
      "scientificName": "Salvelinus fontinalis × Salvelinus namaycush",
      "family": "Salmonidae",
      "familyCommonName": "Trout & Salmon",
      "image": {
        "url": "/static/species/silhouette.svg",
        "thumbUrl": "/static/species/silhouette.svg",
        "status": "placeholder",
        "creator": null,
        "licence": null
      }
    }
  ]
}
```

## Where the data comes from

The `species_image` table (keyed by scientific name, the same `id` the payload uses) holds the
provenance. It is rebuilt from `data/species_images/selection.csv`,
`data/species_images/originals_manifest.csv` and the species list, and is filled automatically
the first time `/fishdex` finds it empty. After adding or approving an image, run
`uv run python -m src.cli.main sync-species-images` to refresh the table and the credits page.
