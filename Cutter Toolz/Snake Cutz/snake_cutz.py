#!/usr/bin/env python3
"""
Snake Cutz - Inkscape 1.x extension (SIMPLE version)

This reverts to circle/ellipse-only geometry (exact elliptical arc math,
like v0.1-v0.3) and drops the general arbitrary-path curve-splitting
machinery from v0.4. It also ports the feature set added to the
CorelDRAW VBA "SnakeChainSimple" macro:

  - Simple 2-way build direction: Horizontal (builds left to right) or
    Vertical (builds top to bottom).
  - Multiple parallel duplicates (chains), with an adjustable gap
    between them.
  - Optional straight-line connectors joining every chain into ONE
    continuous path (single pen-down/pen-up for the whole layout), or
    leaving each chain as its own separate closed path.
  - Fill-area sizing: you give a target width and height (in mm or
    inches, default mm) and the copy-per-chain and chain counts are
    worked out automatically to fill it as closely as possible without
    exceeding it. The mm/inches choice applies only to this Fill Area
    width/height - every other measurement (e.g. chain gap) stays in mm.
  - Demo/test mode: forward pass (red), return pass (blue), and - when
    multiple connected chains are used - the connectors (green) as
    separate open paths, for visually checking alignment before
    committing to a real cut.
  - Generated cutlines are grouped together ("SnakeCutz" group, paths
    named SnakeCutz_1, SnakeCutz_2, ...) and any duplicated artwork
    (including raster images) is grouped separately ("Images" group,
    named Image_1, Image_2, ...).
  - "Keep original object(s)" retains every originally selected object
    (the cut shape AND any artwork/images) untouched, alongside the
    newly generated groups, instead of consuming them into the result.

Geometry: any shape's bounding box gives its width/height/center, which
works for a true <circle>/<ellipse> exactly, and for anything else
(a <rect>, a <path>, a star, etc.) as a reasonable approximation - no
separate "convert to path" step is needed for that, since Inkscape's
bounding_box() already works generically across shape types. A warning
is shown if the shape isn't a native circle/ellipse, since the bounding
box only matches the true tangent geometry when the shape actually IS
elliptical.

Content duplication (artwork placed on the cut shape - images, text,
groups) works for ANY element type selected alongside the cut shape,
including raster <image> elements, since duplication is just clone +
reposition and doesn't care what kind of node it's duplicating. Every
selected non-cut-shape node - image or otherwise - is duplicated at
every position in the chain.

Each circle's bottom (for Vertical) or right (for Horizontal) pole
naturally touches the next circle's top (or left) pole, since the
chain always builds in the positive direction (left-to-right or
top-to-bottom).
"""

import base64
import math
import os
import tempfile

import inkex
from inkex import PathElement, Transform, Group, Image
from inkex.command import inkscape as inkscape_cmd


class SnakeCutz(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--direction", default="vertical")  # horizontal/vertical
        pars.add_argument("--chain_gap", type=float, default=0.0)
        pars.add_argument("--fill_width", type=float, default=100.0)
        pars.add_argument("--fill_height", type=float, default=100.0)
        pars.add_argument("--units", default="mmetres")  # mmetres/inches - Fill Area only
        pars.add_argument("--add_connectors", type=inkex.Boolean, default=True)
        pars.add_argument("--rasterize_images", type=inkex.Boolean, default=False)
        pars.add_argument("--keep_originals", type=inkex.Boolean, default=False)
        pars.add_argument("--test_mode", type=inkex.Boolean, default=False)
        pars.add_argument("--active_tab", default="")

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def get_shape_geometry(self, node):
        """Return (cx, cy, rx, ry) for the shape, read directly from the
        native <circle>/<ellipse> element's own cx/cy/r/rx/ry attributes
        (not derived from its bounding box). Strictly requires a native
        <circle> or <ellipse> element - aborts otherwise."""
        tag = node.tag.split('}')[-1]

        if tag == "path":
            raise inkex.AbortExtension(
                "This object is a PATH.\n\n"
                "Snake Cutz requires a native Circle or Ellipse.\n\n"
                "If you previously used "
                "'Path > Object to Path', undo that step or redraw the shape "
                "with the Circle tool."
            )
        if tag not in ("circle", "ellipse"):
            raise inkex.AbortExtension(
                f"Unsupported object type '{tag}'. "
                "Please select a native Circle or Ellipse."
            )

        def read_len(attr, default=0.0):
            val = node.get(attr)
            if val is None:
                return default
            try:
                if isinstance(val, str) and any(c.isalpha() for c in val):
                    return float(inkex.units.convert_unit(val, 'px'))
                return float(val)
            except (TypeError, ValueError):
                return default

        cx = read_len('cx', 0.0)
        cy = read_len('cy', 0.0)
        if tag == 'circle':
            rx = ry = read_len('r', 0.0)
        else:
            rx = read_len('rx', 0.0)
            ry = read_len('ry', 0.0)

        if rx <= 0 or ry <= 0:
            raise inkex.AbortExtension("Selected cut shape has zero width or height.")

        # Account for any transform (translate/scale/rotate) applied to the
        # shape itself or inherited from its ancestors, so cx/cy/rx/ry are
        # expressed in the same document coordinate space used elsewhere.
        transform = node.composed_transform()
        cx, cy = transform.apply_to_point((cx, cy))
        scale_x = math.hypot(transform.a, transform.b)
        scale_y = math.hypot(transform.c, transform.d)
        rx *= scale_x
        ry *= scale_y

        return cx, cy, rx, ry

    @staticmethod
    def resolve_direction(dir_code):
        """Returns vertical (True stacks top-to-bottom along Y, False
        stacks left-to-right along X). The chain always builds in the
        positive direction."""
        return {
            'vertical': True,
            'horizontal': False,
        }[dir_code]

    @staticmethod
    def pole_funcs(rx, ry, vertical):
        """Returns (start_pole, end_pole) functions for the chain,
        which always builds in the positive direction (top-to-bottom
        for Vertical, left-to-right for Horizontal)."""
        def start_pole(c):
            cx, cy = c
            return (cx, cy - ry) if vertical else (cx - rx, cy)

        def end_pole(c):
            cx, cy = c
            return (cx, cy + ry) if vertical else (cx + rx, cy)

        return start_pole, end_pole

    # ------------------------------------------------------------------
    # Arc path building (per chain)
    # ------------------------------------------------------------------

    def build_forward_commands(self, centers, rx, ry, start_pole, end_pole):
        n = len(centers)
        parts = ["M {:.6f},{:.6f}".format(*start_pole(centers[0]))]
        for i in range(n):
            sweep = 1 if (i % 2 == 0) else 0
            ep = end_pole(centers[i])
            parts.append(
                "A {rx:.6f},{ry:.6f} 0 0,{s} {x:.6f},{y:.6f}".format(
                    rx=rx, ry=ry, s=sweep, x=ep[0], y=ep[1]
                )
            )
        return parts

    def build_return_commands(self, centers, rx, ry, start_pole, end_pole):
        n = len(centers)
        parts = ["M {:.6f},{:.6f}".format(*end_pole(centers[n - 1]))]
        for i in range(n - 1, -1, -1):
            sweep = 1 if (i % 2 == 0) else 0
            sp = start_pole(centers[i])
            parts.append(
                "A {rx:.6f},{ry:.6f} 0 0,{s} {x:.6f},{y:.6f}".format(
                    rx=rx, ry=ry, s=sweep, x=sp[0], y=sp[1]
                )
            )
        return parts

    def build_centers(self, cx, cy, n, vertical, spacing, perp_dx, perp_dy):
        centers = []
        for i in range(n):
            if vertical:
                centers.append((cx + perp_dx, cy + perp_dy + i * spacing))
            else:
                centers.append((cx + perp_dx + i * spacing, cy + perp_dy))
        return centers

    # ------------------------------------------------------------------
    # Content (artwork) duplication - works for any element type,
    # including raster <image> elements.
    # ------------------------------------------------------------------

    def duplicate_content(self, node, dx, dy, target_parent):
        clone = node.duplicate()
        # Compute the clone's full WORLD transform first (as if it were
        # being dropped in place next to the original, offset by dx/dy),
        # then re-express that as a LOCAL transform relative to
        # target_parent's own coordinate frame. This lets target_parent be
        # any group - including one that sits somewhere other than
        # document root and carries its own ancestor transforms - without
        # the clone's position/scale silently drifting.
        # VERIFY: `-transform` as shorthand for Transform.inverse() is the
        # pattern used in current inkex extensions, but I couldn't test-run
        # it in this environment - if it errors, use
        # target_parent.composed_transform().inverse() instead.
        world_transform = Transform("translate({},{})".format(dx, dy)) @ node.composed_transform()
        parent_world_transform = target_parent.composed_transform()
        local_transform = (-parent_world_transform) @ world_transform
        clone.set('transform', str(local_transform))
        target_parent.append(clone)
        return clone

    def emit_path(self, parent, d_path, style=None, label=None, stroke_color=None, stroke_width=None):
        new_path = PathElement()
        new_path.set('d', d_path)
        if stroke_color is not None:
            new_path.set('style', "fill:none;stroke:{0};stroke-width:{1:.4f}".format(
                stroke_color, stroke_width))
        else:
            new_path.set('style', style)
        if label:
            new_path.set('inkscape:label', label)
            new_path.set('id', self.svg.get_unique_id(label.lower()))
        parent.append(new_path)
        return new_path

    def make_group(self, parent, label):
        group = Group()
        group.set('inkscape:label', label)
        group.set('id', self.svg.get_unique_id(label.lower()))
        parent.append(group)
        return group

    def rasterize_group(self, group_node):
        """Flatten every duplicated artwork copy inside group_node into a
        single embedded PNG <image>, replacing the group in place.

        This shells out to the Inkscape binary (same approach used by
        other inkex extensions via inkex.command.inkscape) because
        rendering arbitrary content - including raster images, text, and
        filters - to a bitmap isn't something the Python side can do on
        its own; only Inkscape's own renderer can. The current in-memory
        document (including the newly generated "Images" group) is
        written to a temp file first, so what gets rasterized is exactly
        what was just built, not the original input file.

        VERIFY: this depends on inkex.command.inkscape() and the
        "export-id / export-id-only / export-type:png / export-filename /
        export-do" action names, which are correct for the Inkscape 1.x
        command-line/actions API but weren't test-run in this
        environment - if Inkscape's CLI syntax differs on the target
        version, adjust the actions string accordingly.
        """
        bbox = group_node.bounding_box()
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            return group_node

        group_id = group_node.get('id')
        parent = group_node.getparent()

        tmp_dir = tempfile.mkdtemp(prefix="snake_cutz_")
        svg_path = os.path.join(tmp_dir, "doc.svg")
        png_path = os.path.join(tmp_dir, "out.png")

        try:
            self.document.write(svg_path)
            inkscape_cmd(
                svg_path,
                actions=(
                    "export-id:{0};export-id-only;export-type:png;"
                    "export-filename:{1};export-do"
                ).format(group_id, png_path)
            )
            if not os.path.exists(png_path):
                raise RuntimeError("no PNG file was produced")
            with open(png_path, 'rb') as f:
                png_bytes = f.read()
        except Exception as exc:
            raise inkex.AbortExtension(
                "Rasterizing the duplicated artwork failed ({0}). Turn off "
                "'Rasterize duplicated artwork into a single image' and try "
                "again, or check that Inkscape's command-line export is "
                "working on this system.".format(exc)
            )
        finally:
            for fname in (svg_path, png_path):
                try:
                    os.remove(fname)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

        b64 = base64.b64encode(png_bytes).decode('ascii')

        image_node = Image()
        image_node.set('x', "{:.6f}".format(bbox.left))
        image_node.set('y', "{:.6f}".format(bbox.top))
        image_node.set('width', "{:.6f}".format(bbox.width))
        image_node.set('height', "{:.6f}".format(bbox.height))
        image_node.set('xlink:href', "data:image/png;base64,{0}".format(b64))
        image_node.set('inkscape:label', 'Images')
        image_node.set('id', self.svg.get_unique_id('images_raster'))

        index = list(parent).index(group_node)
        parent.remove(group_node)
        parent.insert(index, image_node)
        return image_node

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def effect(self):
        selected = list(self.svg.selection)
        if not selected:
            raise inkex.AbortExtension(
                "Select the cut circle/ellipse (and, optionally, any artwork "
                "or raster images you want duplicated with it)."
            )

        shape_nodes = [
            n for n in selected if n.tag.split('}')[-1] in ('circle', 'ellipse')
        ]
        if len(shape_nodes) == 0:
            raise inkex.AbortExtension(
                "No circle or ellipse found in the selection. The cut shape "
                "must be a native circle or ellipse - select one (plus any "
                "artwork/text to go with it) and try again."
            )
        if len(shape_nodes) > 1:
            raise inkex.AbortExtension(
                "Select only ONE circle/ellipse as the cut line (plus any "
                "artwork/text to go with it)."
            )
        cut_node = shape_nodes[0]
        # Everything else selected - images, text, groups, whatever - is
        # treated as artwork to duplicate at every chain position.
        content_nodes = [n for n in selected if n is not cut_node]

        chain_gap_mm = self.options.chain_gap
        # chain_gap / fill dimensions are always specified in mm regardless
        # of the document's display unit - convert to the document's
        # internal user units (accounts for viewBox scaling too) right
        # here, once.
        # VERIFY: self.svg.unittouu() is a long-standing inkex API for this
        # exact purpose, but I couldn't test-run it in this environment -
        # if this errors, check whether your inkex version instead wants
        # inkex.units.convert_unit(chain_gap_mm, "px", "mm") or similar.
        chain_gap = self.svg.unittouu("{}mm".format(chain_gap_mm))
        add_connectors = self.options.add_connectors
        rasterize_images = self.options.rasterize_images
        keep_originals = self.options.keep_originals
        test_mode = self.options.test_mode

        cx, cy, rx, ry = self.get_shape_geometry(cut_node)
        vertical = self.resolve_direction(self.options.direction)
        start_pole, end_pole = self.pole_funcs(rx, ry, vertical)

        spacing = 2 * ry if vertical else 2 * rx
        perp_diameter = 2 * rx if vertical else 2 * ry

        # Copies-per-chain and chain count are always derived from the
        # target fill area - there's no separate "type the counts in
        # directly" mode anymore. The Fill Area width/height are the ONLY
        # values affected by the "units" option (mm or inches, default
        # mm); everything else in the extension (chain_gap, etc.) stays
        # in mm regardless of this setting.
        fill_unit = {"mmetres": "mm", "inches": "in"}.get(self.options.units, "mm")
        fill_width = self.svg.unittouu("{}{}".format(self.options.fill_width, fill_unit))
        fill_height = self.svg.unittouu("{}{}".format(self.options.fill_height, fill_unit))
        if fill_width <= 0 or fill_height <= 0:
            raise inkex.AbortExtension("Fill Area width/height must be greater than 0.")

        primary_extent = fill_height if vertical else fill_width
        secondary_extent = fill_width if vertical else fill_height

        if spacing <= 0:
            raise inkex.AbortExtension("Cut shape has zero size along the build direction.")
        copies = int(primary_extent // spacing)
        if copies < 2:
            raise inkex.AbortExtension(
                "Fill Area is too small to fit at least 2 copies along the "
                "build direction. Increase the fill size or shrink the cut shape."
            )

        denom = perp_diameter + chain_gap
        if denom <= 0:
            num_chains = 1
        else:
            num_chains = max(1, int((secondary_extent + chain_gap) // denom))

        if copies < 2:
            raise inkex.AbortExtension("Copies per chain must be 2 or more.")
        if num_chains < 1:
            raise inkex.AbortExtension("Number of chains must be 1 or more.")

        parent = cut_node.getparent()

        # Groups for the generated output. Cutlines are grouped as
        # "SnakeCutz" (paths SnakeCutz_1, SnakeCutz_2, ...); duplicated
        # artwork/images are grouped as "Images" (paths Image_1, Image_2,
        # ...). Groups are created lazily, only if something is actually
        # emitted into them.
        cut_group = None
        images_group = None
        cut_counter = 0
        image_counter = 0

        def get_cut_group():
            nonlocal cut_group
            if cut_group is None:
                cut_group = self.make_group(parent, "SnakeCutz")
            return cut_group

        def get_images_group():
            nonlocal images_group
            if images_group is None:
                # Same layer/parent as the SnakeCutz group - duplicate_content()
                # re-expresses each clone's transform relative to this
                # group's own coordinate frame, so it's safe to place it
                # anywhere without positions drifting.
                images_group = self.make_group(parent, "Images")
            return images_group

        def next_cut_label():
            nonlocal cut_counter
            cut_counter += 1
            return "SnakeCutz_{0}".format(cut_counter)

        def next_image_label():
            nonlocal image_counter
            image_counter += 1
            return "Image_{0}".format(image_counter)

        combined_parts = []
        prev_start = None
        first_chain_start = None

        for k in range(num_chains):
            perp_offset = k * (perp_diameter + chain_gap)
            perp_dx, perp_dy = (perp_offset, 0) if vertical else (0, perp_offset)

            centers = self.build_centers(cx, cy, copies, vertical, spacing, perp_dx, perp_dy)
            this_start = start_pole(centers[0])
            if k == 0:
                first_chain_start = this_start

            # Duplicate artwork (including any images) at every circle
            # position in this chain. When keeping the originals, every
            # position - including chain 0, copy 0 - gets a fresh
            # duplicate, so the original selected artwork is left
            # completely untouched. Otherwise, the original artwork
            # itself is reused as chain 0 / copy 0 (and later moved into
            # the Images group), so it isn't duplicated a second time.
            if content_nodes:
                for i in range(copies):
                    if not keep_originals and k == 0 and i == 0:
                        continue
                    if vertical:
                        adx, ady = perp_dx, perp_dy + i * spacing
                    else:
                        adx, ady = perp_dx + i * spacing, perp_dy
                    images_target = get_images_group()
                    for content in content_nodes:
                        clone = self.duplicate_content(content, adx, ady, images_target)
                        label = next_image_label()
                        clone.set('inkscape:label', label)
                        clone.set('id', self.svg.get_unique_id(label.lower()))

            forward = self.build_forward_commands(centers, rx, ry, start_pole, end_pole)
            back = self.build_return_commands(centers, rx, ry, start_pole, end_pole)

            if test_mode:
                sw = max((rx + ry) * 0.02, 0.2)
                test_target = get_cut_group()
                self.emit_path(test_target, " ".join(forward), stroke_color="#ff0000",
                               stroke_width=sw, label="Snake Cutz TEST - forward (red)")
                self.emit_path(test_target, " ".join(back), stroke_color="#0000ff",
                               stroke_width=sw, label="Snake Cutz TEST - return (blue)")
                if k > 0 and add_connectors:
                    conn_d = "M {:.6f},{:.6f} L {:.6f},{:.6f}".format(
                        prev_start[0], prev_start[1], this_start[0], this_start[1]
                    )
                    self.emit_path(test_target, conn_d, stroke_color="#00c800",
                                   stroke_width=sw, label="Snake Cutz TEST - connector (green)")
            else:
                if num_chains == 1 or add_connectors:
                    if k > 0:
                        combined_parts.append(
                            "L {:.6f},{:.6f}".format(this_start[0], this_start[1])
                        )
                        combined_parts.extend(forward[1:])  # skip forward's own M - the L already got us here
                    else:
                        combined_parts.extend(forward)
                    combined_parts.extend(back[1:])
                else:
                    # Multiple chains, no connectors: each chain is its
                    # own independent closed path.
                    d_path = " ".join(forward + back[1:] + ["Z"])
                    style = cut_node.get('style') or "fill:none;stroke:#000000;stroke-width:1"
                    self.emit_path(get_cut_group(), d_path, style=style, label=next_cut_label())

            prev_start = this_start

        if not test_mode and (num_chains == 1 or add_connectors):
            # A single chain always returns to its own start (close it).
            # Multiple connector-joined chains end at the LAST chain's
            # start, not the overall first point, so don't close - that
            # would add an unwanted extra line back to the very beginning.
            if num_chains == 1:
                combined_parts.append("Z")
            d_path = " ".join(combined_parts)
            style = cut_node.get('style') or "fill:none;stroke:#000000;stroke-width:1"
            self.emit_path(get_cut_group(), d_path, style=style, label=next_cut_label())

        if not keep_originals:
            # Consume the original cut shape into the result...
            parent.remove(cut_node)
            # ...and fold the original artwork (which served as chain 0 /
            # copy 0 above, and was never duplicated for that slot) into
            # the Images group alongside its duplicates, so the group
            # contains every copy including the first.
            if content_nodes:
                images_target = get_images_group()
                parent_world_transform = images_target.composed_transform()
                for content in content_nodes:
                    # Preserve on-canvas position: compute the node's full
                    # world transform BEFORE reparenting, then re-express
                    # it relative to the Images group's own coordinate
                    # frame (moving it under a different-ancestor group
                    # would otherwise silently shift/rescale it if the old
                    # and new ancestors carry different transforms).
                    world_transform = content.composed_transform()
                    local_transform = (-parent_world_transform) @ world_transform
                    content.getparent().remove(content)
                    content.set('transform', str(local_transform))
                    images_target.append(content)
                    label = next_image_label()
                    content.set('inkscape:label', label)
                    content.set('id', self.svg.get_unique_id(label.lower()))
        # If keep_originals is True, the cut shape and every originally
        # selected artwork node are left exactly where they were - the
        # newly generated SnakeCutz / Images groups are entirely separate.

        if rasterize_images and images_group is not None:
            # Flatten every duplicated artwork copy (the whole "Images"
            # group, now holding its final content) into one embedded PNG.
            self.rasterize_group(images_group)


if __name__ == '__main__':
    SnakeCutz().run()
