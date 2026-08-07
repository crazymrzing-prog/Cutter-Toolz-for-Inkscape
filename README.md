I'm not a coder — much of this was created using AI.

# Cutter Toolz — Inkscape Extensions

  <a href="https://github.com/crazymrzing-prog/Cutter-Toolz-for-Inkscape/archive/refs/heads/main.zip">
    <img src="https://img.shields.io/badge/DOWNLOAD-blue?style=for-the-badge&logo=download&logoColor=white" alt="Download">
  </a>

Two Inkscape 1.x extensions for laying out repeating cut lines (circles/ellipses or rectangles) to fill a target area, for laser cutters, vinyl cutters, sticker sheets, and similar. Both show up in Inkscape under **Extensions > Cutter Toolz**.


## What's in this repo

| File | Extension | Purpose |
|---|---|---|
| `snake_cutz.inx` / `snake_cutz.py` | **Snake Cutz** | Chains a circle or ellipse into one or more tangent "snake" rows/columns to fill an area. |
| `quik_cutz_grid.inx` / `quik_cutz_grid.py` | **Quik Cutz** | Tiles a rectangle into a grid of cells to fill an area, drawing all the row/column dividers as two continuous snake-style paths. |

Each `.py` file is the extension logic; each matching `.inx` file defines the Inkscape UI (menus, options, help tabs) for it. They must stay paired and both be installed together.

## Installation

1. Copy all four files into your Inkscape user extensions folder:
   - **Windows:** `%APPDATA%\inkscape\extensions\`
   - **macOS:** `~/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/`
   - **Linux:** `~/.config/inkscape/extensions/`
2. Restart Inkscape.
3. Find them under **Extensions > Cutter Toolz > Snake Cutz** / **Quik Cutz**.

## Snake Cutz

<img width="528" height="508.5" alt="202608073161" src="https://github.com/user-attachments/assets/781ce6fe-0ac4-4dc3-b465-f563d5af953d" />         <img width="306" height="332" alt="202608073144" src="https://github.com/user-attachments/assets/1382e1ab-4ec0-4d07-aa06-f9ab5a080139" />




Select **one native circle or ellipse** as the cut shape (converted paths, rectangles, and stars are rejected — redraw with the Circle/Ellipse tool if needed). Optionally also select any artwork (image, text, group) to repeat at every position, e.g. a sticker design.

- **Build direction:** Horizontal (left to right) or Vertical (top to bottom).
- **Fill Area:** give a target width/height (mm or inches) and the number of copies per chain, and the number of chains, are worked out automatically.
- **Multiple chains:** set a gap between parallel chains; optionally join every chain into one continuous cut path with straight connectors, or leave each chain as its own closed path.

   <img width="313" height="330" alt="202608073146" src="https://github.com/user-attachments/assets/25bea1cb-15f0-46d1-8c6e-657b38ae0f35" />      <img width="309" height="334" alt="202608073145" src="https://github.com/user-attachments/assets/88644528-344f-4062-a239-5e36ba4c05c0" />
   
- **Demo mode:** previews the forward pass (red), return pass (blue), and connectors (green) as separate open paths, so you can check alignment before cutting.
- **Rasterize duplicated artwork:** flattens all the duplicated artwork into a single embedded PNG (requires the Inkscape command-line renderer).
- **Keep originals:** leaves your original shape/artwork untouched instead of consuming it into the result.

Output is grouped as **"SnakeCutz"** (cut paths) and **"Images"** (duplicated artwork).

## Quik Cutz

   <img width="582" height="504" alt="202608073160" src="https://github.com/user-attachments/assets/f9c5b503-d13f-4563-a021-fde4be0aac8a" />    <img width="334" height="336" alt="202608073150" src="https://github.com/user-attachments/assets/8fc01bda-3804-4f1d-9a3b-2010b9412280" />

Select **one native rectangle or square** as the cut shape (converted paths, circles, and stars are rejected), plus optionally any artwork to repeat in every cell. The grid always builds right and down from your original selection, which becomes the top-left cell.

- **Fill Area:** target width/height (mm or inches); row/column counts are calculated automatically.
- **Stub / Join options** (pick one):
  1. No stub extensions — plain grid lines.
  
       <img width="307" height="306" alt="202608073152" src="https://github.com/user-attachments/assets/31bab4ca-95a3-417e-b612-863627e0572d" />
      
  2. Add stub extensions only — each line overshoots its true end slightly, useful for reliable pierce/tie-off points.

       <img width="332" height="331" alt="202608073153" src="https://github.com/user-attachments/assets/9b486434-63b8-4c3c-88f4-b613dd73ae8d" />

  3. Add stubs + join with connectors — every horizontal divider becomes one path, every vertical divider becomes one path, with filleted corners at the joins.
 
       <img width="332" height="333" alt="202608073154" src="https://github.com/user-attachments/assets/2b3de9ef-bd63-47bf-a297-1053079ae040" />

  4. Add stubs + connectors + join path one to path two — as above, plus the horizontal and vertical paths are joined into one, with a sharp right-angle corner.

       <img width="331" height="336" alt="202608073159" src="https://github.com/user-attachments/assets/7a3e17f0-0f09-4268-ae58-3ba61f82453b" />
  
  5. Add stubs + border frame — traces a border rectangle around the whole grid in addition to the two paths.
  
       <img width="330" height="338" alt="202608073158" src="https://github.com/user-attachments/assets/356f2a77-458e-4411-bd33-1bb30e63841f" />

- **Demo mode:** previews path one/horizontal (red), path two/vertical (blue), and the border or join (green).
- **Rasterize duplicated artwork** and **Keep originals** work the same as in Snake Cutz.

Output is grouped as **"QuikCutz"** (cut paths) and **"Images"** (duplicated artwork).

## Requirements

- Inkscape 1.x (uses the `inkex` Python API and `inkscape.command` module).
- The "Rasterize duplicated artwork" option in either extension needs Inkscape's command-line renderer available on your system.

## Notes

- Both extensions strictly require a **native** shape (circle/ellipse for Snake Cutz, rectangle for Quik Cutz) as the cut line — if you've used "Path > Object to Path" on it, undo that or redraw the shape.
- Full in-app help is available on the Help tabs of each extension's dialog.
