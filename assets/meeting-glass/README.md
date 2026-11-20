# Neutral meeting glass skin

`toolbar.png` and `panel.png` come from the user's externally generated neutral
1536 x 1024 glass sheet, replacing the previous scenery-based crops entirely.
There are no source labels, controls or scenery in these assets. Live Sentinel
controls remain separate and interactive.

The supplied file was RGB/JPEG with a white matte, not a transparent PNG.
The extraction utility neutralizes chroma and approximates translucency with a
rounded alpha mask, a light coating and stronger rim highlights. This cannot
recover the original unknown alpha. Native window composition still provides
desktop blur; the image itself provides only a static neutral surface texture.
The source rectangles are (68,98)-(1468,210) and (68,258)-(1468,932).

`sentinel_meeting_material.py` caches QImage data and uses 48px source corner
regions for nine-slice drawing. Asset alpha remains in effect at every tint
setting. Missing images fall back to vector glass. The existing `sentinel.spec`
bundles the assets directory; Pillow/numpy are only extraction dependencies.

Recreate from the supplied sheet:

```powershell
.venv/Scripts/python.exe packaging/extract_meeting_skin.py <neutral-sheet.jpg>
```

An actual RGBA sheet preserves its supplied alpha instead of reconstructing it.
