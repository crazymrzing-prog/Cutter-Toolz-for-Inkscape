#!/usr/bin/env python3
"""
Quik Cutz - Inkscape 1.x extension

Starts from a single sticker (a native <rect> cut line, usually with a
raster image / artwork placed on top and selected alongside it) and
tiles it into a grid sized to fill a target width x height (mm or
inches, default mm) - the column/row counts are worked out
automatically from the cut shape's own size, rather than being typed
in directly. The mm/inches choice applies only to this Fill Area
width/height - every other measurement (e.g. stub/extension length)
stays in mm. It draws every horizontal
and vertical boundary line of that grid - including the sheet's own
outer top/bottom/left/right edges, not just the internal dividers
between cells - as two alternating snake paths: one set of horizontal
cuts, one set of vertical cuts. For N copies along an axis there are
N+1 lines on that axis (N-1 internal dividers plus the 2 outer edges).

Direction / snake logic:
  - Each internal horizontal divider alternates direction from the one
    above it: the first (topmost) runs left->right, the next
    right->left, and so on.
  - Each internal vertical divider alternates from the one to its
    left: the first (leftmost) runs bottom->top ("up"), the next
    top->bottom, and so on.
  - This is a fixed convention (not user-configurable) confirmed
    against reference images before shipping.

Stub / Join Options (single mutually-exclusive radio choice,
layout_option, shown as a left-hand option stack next to the stub
length setting on the right):
  * opt1 - No stub extensions. Plain grid lines; path one and path
           two stay separate and unjoined; no border.
  * opt2 - Add stub extensions only: a short straight stub is added
           past each line's true start/end (length set by
           extension_length, default 5mm), so the cut slightly
           overshoots the grid edge - useful for reliable pierce/
           tie-off points. Stub ends stay unjoined.
  * opt3 - Stub extensions + join with connectors: straight segments
           join the stub (or, with extensions effectively zero, the
           bare true endpoint) at the end of one line to the matching
           end of the next line in that direction's sequence, so
           every horizontal divider becomes ONE path and every
           vertical divider becomes ONE path (two separate path
           objects - path one and path two are NOT bridged into a
           single path). Every corner a connector introduces is
           automatically rounded with a circular fillet (radius 0.9x
           the extension length, clamped so it never exceeds the
           extension length or half the connector's own length).
  * opt4 - Everything opt3 does, plus path one's end is joined
           directly to path two's start with a straight segment,
           combining them into ONE single path with a square (sharp,
           never filleted) right-angle corner at that one joint - no
           curve. This forces connectors on regardless (both paths
           need to already be single continuous subpaths before
           they're joined this way). Unlike the internal connector
           corners elsewhere (which DO get filleted), this one joint
           is always kept sharp, since a filleted version needs two
           separate arcs on a short connecting segment and tends to
           meet at an ugly kink rather than one smooth curve.
  * opt5 - Stub extensions + a border frame: a rectangle is traced
           around the array, sitting at the stub-tip extent (offset
           outward by the extension length on all four sides), as a
           third path object alongside path one and path two.
  - Keep original object(s): keeps the original cut rectangle AND the
    originally selected artwork completely untouched, instead of
    consuming them into the result (default is to consume them - the
    cut shape is removed, and the original artwork becomes part of the
    Images group as its first copy). See "Grouped output" below.

Build direction is fixed: the grid always builds to the right and down
from the ORIGINAL selection, which stays in place as the top-left cell
of the resulting grid. The whole combined path always STARTS at that
same top-left corner - the one corner that never moves as copies are
added.

Rounded connector corners: whenever connectors are enabled AND
extensions are on, every corner introduced by a connector is
automatically replaced with a circular fillet (no separate toggle).
The radius is 0.9x the extension length, further clamped per corner
so it never exceeds the extension length itself (stays within the
stub, never eats into the true cut line) and never exceeds half the
length of that specific connector segment (so the two fillets on
either end of one connector can't overlap).

Grouped output: generated cutlines are grouped together ("QuikCutz"
group, paths named QuikCutz_1, QuikCutz_2, ...) and duplicated artwork
(including raster images) is grouped separately ("Images" group, named
Image_1, Image_2, ...).

Content duplication (artwork placed on the cut shape - images, text,
groups) works for ANY element type, exactly as in the circle-chain
extension: duplication is just clone + reposition and doesn't care
what kind of node it's duplicating. Every position in the grid -
including the original's own cell - is duplicated at every position
when "Keep original object(s)" is on; otherwise the original artwork
itself serves as that first copy and is folded into the Images group.
"""

import base64
import os
import tempfile
import inkex
from inkex import PathElement, Transform, Group


class QuikCutzGrid(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--tab")  # notebook page selector, unused - just needs to be accepted
        pars.add_argument("--fill_width", type=float, default=100.0)
        pars.add_argument("--fill_height", type=float, default=100.0)
        pars.add_argument("--units", default="mmetres")  # mmetres/inches - Fill Area only
        pars.add_argument("--layout_option", default="opt1")  # opt1..opt5, see effect()
        pars.add_argument("--extension_length", type=float, default=5.0)
        pars.add_argument("--rasterize_images", type=inkex.Boolean, default=False)
        pars.add_argument("--keep_originals", type=inkex.Boolean, default=False)
        pars.add_argument("--test_mode", type=inkex.Boolean, default=False)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def get_rect_geometry(self, node):
        """Return (left, top, width, height) for the shape. Requires a
        native <rect> - aborts otherwise, since the grid math assumes a
        true rectangle/square cut line."""
        tag = node.tag.split('}')[-1]
        if tag != 'rect':
            raise inkex.AbortExtension(
                "The cut shape must be a rectangle or square. Selected "
                "shape is a '{0}'. Please select a native <rect> (not a "
                "converted path, circle, star, etc.) and try again.".format(tag)
            )
        bbox = node.bounding_box()
        if bbox is None:
            raise inkex.AbortExtension(
                "Couldn't compute a bounding box for the selected cut shape."
            )
        if bbox.width <= 0 or bbox.height <= 0:
            raise inkex.AbortExtension("Selected cut shape has zero width or height.")
        return bbox.left, bbox.top, bbox.width, bbox.height

    @staticmethod
    def extend_line(p1, p2, ext):
        """Push both endpoints of an axis-aligned segment outward by
        `ext`, past whichever end they already point toward."""
        if ext <= 0:
            return p1, p2
        x1, y1 = p1
        x2, y2 = p2
        if abs(x2 - x1) >= abs(y2 - y1):
            d = 1 if x2 > x1 else -1
            return (x1 - d * ext, y1), (x2 + d * ext, y2)
        else:
            d = 1 if y2 > y1 else -1
            return (x1, y1 - d * ext), (x2, y2 + d * ext)

    @staticmethod
    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    @staticmethod
    def normalize(v):
        d = (v[0] ** 2 + v[1] ** 2) ** 0.5
        if d == 0:
            return (0.0, 0.0)
        return (v[0] / d, v[1] / d)

    def build_subpaths(self, lines, ext_len, use_extensions, use_connectors):
        """Turn a sequence of (p1,p2) lines into a list of subpaths. Each
        subpath is a dict with 'points' (flat list of points) and
        'is_connector' (a flags list, one entry per segment: True if
        that segment - points[k] to points[k+1] - is an implicit
        connector between two lines rather than a true cut line). With
        connectors, consecutive lines share one subpath; without
        connectors, every line is its own 2-point subpath."""
        subpaths = []
        points = []
        flags = []
        for i, (p1, p2) in enumerate(lines):
            e1, e2 = self.extend_line(p1, p2, ext_len) if use_extensions else (p1, p2)
            if i == 0 or not use_connectors:
                if points:
                    subpaths.append({'points': points, 'is_connector': flags})
                points = [e1, e2]
                flags = [False]  # this line segment is a true cut line
            else:
                flags.append(True)   # connector segment (previous line's end -> this line's start)
                points.append(e1)
                flags.append(False)  # this line segment is a true cut line
                points.append(e2)
        if points:
            subpaths.append({'points': points, 'is_connector': flags})
        return subpaths

    def points_to_dpath(self, points, is_connector, radius, ext_len, square_indices=None):
        """Render one subpath's points as an SVG `d` fragment. is_connector
        is a flags list (one per segment) from build_subpaths (or a
        manually merged combination of them) telling which segments are
        implicit connectors vs true cut lines - this replaces the old
        index-parity assumption so subpaths can be safely spliced
        together. With radius <= 0, every vertex is a sharp corner. With
        radius > 0, every interior vertex that's an actual turn (not a
        straight continuation) is replaced with a circular fillet,
        clamped two ways: it can't exceed the extension length on a true
        cut line (so it stays within the stub and never eats into the
        true cut), and it can't exceed half the CONNECTOR segment's
        length on a connector side (so the two fillets on either end of
        one connector never overlap). square_indices is an optional set
        of vertex indices that stay sharp regardless of radius (used for
        the H-to-V join, so it's a clean square corner even when other
        connector corners elsewhere are filleted)."""
        square_indices = square_indices or set()
        parts = ["M {:.6f},{:.6f}".format(*points[0])]
        n = len(points)
        if radius <= 0 or n < 3:
            for p in points[1:]:
                parts.append("L {:.6f},{:.6f}".format(*p))
            return " ".join(parts)

        for i in range(1, n - 1):
            prev_pt, corner, next_pt = points[i - 1], points[i], points[i + 1]
            if i in square_indices:
                parts.append("L {:.6f},{:.6f}".format(*corner))
                continue
            d_in = self.normalize((corner[0] - prev_pt[0], corner[1] - prev_pt[1]))
            d_out = self.normalize((next_pt[0] - corner[0], next_pt[1] - corner[1]))
            cross_z = d_in[0] * d_out[1] - d_in[1] * d_out[0]
            if abs(cross_z) < 1e-9:
                # collinear (or a straight reversal) - no clean fillet, keep it sharp
                parts.append("L {:.6f},{:.6f}".format(*corner))
                continue
            seg_before_is_connector = is_connector[i - 1]
            seg_after_is_connector = is_connector[i]
            limit_before = self.dist(prev_pt, corner) / 2 if seg_before_is_connector else ext_len
            limit_after = self.dist(corner, next_pt) / 2 if seg_after_is_connector else ext_len
            r = min(radius, limit_before, limit_after)
            if r <= 0:
                parts.append("L {:.6f},{:.6f}".format(*corner))
                continue
            p1 = (corner[0] - r * d_in[0], corner[1] - r * d_in[1])
            p2 = (corner[0] + r * d_out[0], corner[1] + r * d_out[1])
            sweep = 1 if cross_z > 0 else 0
            parts.append("L {:.6f},{:.6f}".format(*p1))
            parts.append("A {r:.6f},{r:.6f} 0 0,{sweep} {x:.6f},{y:.6f}".format(
                r=r, sweep=sweep, x=p2[0], y=p2[1]))
        parts.append("L {:.6f},{:.6f}".format(*points[-1]))
        return " ".join(parts)

    @staticmethod
    def join_h_v_bracket(subpath_h, subpath_v):
        """Join path one's subpath to path two's subpath into one
        combined subpath. path one's last true point and path two's
        first true point sit at the SAME grid corner (guaranteed by the
        row-count-parity matching in effect()), but with extensions on,
        each gets its OWN stub pushed out along its OWN line's axis (H's
        stub moves in x, V's stub moves in y) - so their stub TIPS are
        two different points, offset diagonally from each other. A
        straight segment between them would cut on the diagonal, so
        instead this inserts ONE extra point - (h_last.x, v_first.y) -
        splitting that gap into two orthogonal segments (a vertical
        step then a horizontal step, forming a small right-angle
        bracket entirely outside the grid's true corner) instead of a
        diagonal shortcut. If the two points already coincide (e.g.
        extensions off), no extra point is needed - they're just merged
        directly. Returns (combined_subpath, square_indices) where
        square_indices are the vertex indices that must stay sharp (no
        fillet) regardless of the radius setting, since a filleted
        bracket corner would need extra room this small connector
        doesn't reliably have."""
        h_points, h_flags = subpath_h['points'], subpath_h['is_connector']
        v_points, v_flags = subpath_v['points'], subpath_v['is_connector']
        h_last = h_points[-1]
        v_first = v_points[0]
        if h_last == v_first:
            merged_points = h_points + v_points[1:]
            merged_flags = h_flags + v_flags
            square_indices = {len(h_points) - 1}
        else:
            bend = (h_last[0], v_first[1])
            merged_points = h_points + [bend] + v_points
            merged_flags = h_flags + [True, True] + v_flags
            hi = len(h_points) - 1
            square_indices = {hi, hi + 1, hi + 2}
        return {'points': merged_points, 'is_connector': merged_flags}, square_indices

    def build_horizontal_lines(self, grid_left, grid_top, total_w, total_h, rows, start_top, start_left):
        """One entry per row boundary, INCLUDING the sheet's own top and
        bottom edges - rows+1 of them - alternating direction each step.
        The FIRST line in the sequence is the row at the build-direction
        anchor (start_top picks top vs bottom edge first), and its
        initial direction is start_left (L->R) or not (R->L) - this is
        what the whole combined path starts from."""
        lines = []
        row_order = range(0, rows + 1) if start_top else range(rows, -1, -1)
        for i, row in enumerate(row_order):
            y = grid_top + row * (total_h / rows)
            go_left_to_right = start_left if i % 2 == 0 else not start_left
            if go_left_to_right:
                lines.append(((grid_left, y), (grid_left + total_w, y)))
            else:
                lines.append(((grid_left + total_w, y), (grid_left, y)))
        return lines

    def build_vertical_lines(self, grid_left, grid_top, total_w, total_h, cols, start_left, start_bottom):
        """One entry per column boundary, INCLUDING the sheet's own left
        and right edges - cols+1 of them - alternating direction each
        step. The FIRST line is the column at the build-direction
        anchor (start_left picks left vs right edge first), and its
        initial direction is start_bottom (bottom->top / "up") or not
        (top->bottom / "down")."""
        lines = []
        col_order = range(0, cols + 1) if start_left else range(cols, -1, -1)
        for i, col in enumerate(col_order):
            x = grid_left + col * (total_w / cols)
            go_up = start_bottom if i % 2 == 0 else not start_bottom
            if go_up:
                lines.append(((x, grid_top + total_h), (x, grid_top)))
            else:
                lines.append(((x, grid_top), (x, grid_top + total_h)))
        return lines

    # ------------------------------------------------------------------
    # Content duplication - works for any element type, including
    # raster <image> elements.
    # ------------------------------------------------------------------

    def duplicate_content(self, node, dx, dy, target_parent):
        clone = node.duplicate()
        # Compute the clone's full WORLD transform first (as if it were
        # being dropped in place next to the original, offset by dx/dy),
        # then re-express that as a LOCAL transform relative to
        # target_parent's own coordinate frame, so target_parent can be
        # any group - not just document root - without the clone's
        # position drifting.
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

    def rasterize_group(self, group):
        """Flatten every child of `group` down into a single embedded PNG
        <image>, replacing the individual duplicated artwork elements.
        Shells out to the Inkscape binary itself to do the actual
        rendering, since that's the only way to get pixel-accurate raster
        output (filters, gradients, raster sources, etc.) without
        reimplementing an SVG renderer.
        VERIFY: I couldn't run Inkscape in this environment to test this
        end-to-end. `inkex.command.inkscape()` is the documented way
        other extensions shell out to the same Inkscape binary that
        launched this script, but if it errors, the fallback is to build
        the same command manually with subprocess.run() using
        `shutil.which("inkscape")` (or the path Inkscape's own
        preferences report) for the executable.
        """
        bbox = group.bounding_box()
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            # Nothing visible to rasterize - leave the group as-is.
            return

        from inkex.command import inkscape

        group_id = group.get('id')
        tmp_dir = tempfile.mkdtemp(prefix="quikcutz_")
        tmp_svg = os.path.join(tmp_dir, "src.svg")
        tmp_png = os.path.join(tmp_dir, "out.png")
        try:
            self.document.write(tmp_svg)
            try:
                inkscape(
                    tmp_svg,
                    export_id=group_id,
                    export_id_only=True,
                    export_type="png",
                    export_dpi=300,
                    export_filename=tmp_png,
                )
            except Exception as exc:
                raise inkex.AbortExtension(
                    "Rasterizing the duplicated artwork failed - the Inkscape "
                    "command-line export didn't run successfully ({0}). Turn "
                    "off 'Rasterize duplicated artwork' and try again, or "
                    "check that Inkscape's own executable is reachable from "
                    "extensions.".format(exc)
                )

            if not os.path.exists(tmp_png):
                raise inkex.AbortExtension(
                    "Rasterizing the duplicated artwork failed - no PNG was "
                    "produced. Turn off 'Rasterize duplicated artwork' and "
                    "try again."
                )

            with open(tmp_png, "rb") as f:
                png_b64 = base64.b64encode(f.read()).decode("ascii")
        finally:
            for fname in (tmp_svg, tmp_png):
                try:
                    os.remove(fname)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

        for child in list(group):
            group.remove(child)

        image = inkex.Image()
        image.set('x', str(bbox.left))
        image.set('y', str(bbox.top))
        image.set('width', str(bbox.width))
        image.set('height', str(bbox.height))
        image.set('preserveAspectRatio', 'none')
        image.set('xlink:href', "data:image/png;base64,{0}".format(png_b64))
        image.set('inkscape:label', "Image_1")
        image.set('id', self.svg.get_unique_id("image_1"))
        group.append(image)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def effect(self):
        selected = list(self.svg.selection)
        if not selected:
            raise inkex.AbortExtension(
                "Select the cut rectangle/square (and, optionally, any "
                "artwork or raster image you want repeated with it)."
            )

        shape_nodes = [n for n in selected if n.tag.split('}')[-1] == 'rect']
        if len(shape_nodes) == 0:
            raise inkex.AbortExtension(
                "No rectangle found in the selection. The cut shape must "
                "be a native <rect> - select one (plus any artwork/text "
                "to go with it) and try again."
            )
        if len(shape_nodes) > 1:
            raise inkex.AbortExtension(
                "Select only ONE rectangle as the cut line (plus any "
                "artwork/text to go with it)."
            )
        cut_node = shape_nodes[0]
        content_nodes = [n for n in selected if n is not cut_node]

        # A single 5-way radio choice drives everything that used to be
        # three separate controls (add_extensions / stub_option /
        # join_h_v):
        #   opt1 - no stub extensions, no join, no border
        #   opt2 - stub extensions only, ends left unjoined
        #   opt3 - stub extensions + join with connectors
        #   opt4 - stub extensions + connectors + join path one to path
        #          two with a square right-angle corner (join_h_v needs
        #          both paths already single continuous subpaths, so it
        #          implies connectors are on)
        #   opt5 - stub extensions + border frame around the array
        layout_option = self.options.layout_option
        add_extensions = layout_option in ("opt2", "opt3", "opt4", "opt5")
        join_h_v = layout_option == "opt4"
        add_connectors = layout_option in ("opt3", "opt4") or join_h_v
        add_outer_box = layout_option == "opt5"
        keep_originals = self.options.keep_originals
        test_mode = self.options.test_mode
        ext_len = self.svg.unittouu("{}mm".format(self.options.extension_length))

        orig_left, orig_top, width, height = self.get_rect_geometry(cut_node)

        # Columns/rows are always derived from the target fill area - the
        # Fill Area width/height are the ONLY values affected by the
        # "units" option (mm or inches, default mm); everything else in
        # the extension (extension_length/stub length, etc.) stays in mm
        # regardless of this setting.
        fill_unit = {"mmetres": "mm", "inches": "in"}.get(self.options.units, "mm")
        fill_width = self.svg.unittouu("{}{}".format(self.options.fill_width, fill_unit))
        fill_height = self.svg.unittouu("{}{}".format(self.options.fill_height, fill_unit))
        if fill_width <= 0 or fill_height <= 0:
            raise inkex.AbortExtension("Fill Area width/height must be greater than 0.")

        W = int(fill_width // width)
        H = int(fill_height // height)
        if W < 1 or H < 1:
            raise inkex.AbortExtension(
                "Fill Area is too small to fit even one copy of the cut "
                "shape. Increase the fill size or shrink the cut shape."
            )

        # The grid always builds right and down from the original
        # sticker, which sits at the top-left cell - the one corner
        # that never moves as copies are added. The combined path
        # starts there too.
        col_of_orig = 0
        row_of_orig = 0

        grid_left = orig_left - col_of_orig * width
        grid_top = orig_top - row_of_orig * height
        total_w = W * width
        total_h = H * height

        parent = cut_node.getparent()

        # Groups for the generated output. Cutlines are grouped as
        # "QuikCutz" (paths QuikCutz_1, QuikCutz_2, ...); duplicated
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
                cut_group = self.make_group(parent, "QuikCutz")
            return cut_group

        def get_images_group():
            nonlocal images_group
            if images_group is None:
                # Same layer/parent as the QuikCutz group - duplicate_content()
                # re-expresses each clone's transform relative to this
                # group's own coordinate frame, so it's safe to place it
                # anywhere without positions drifting.
                images_group = self.make_group(parent, "Images")
            return images_group

        def next_cut_label():
            nonlocal cut_counter
            cut_counter += 1
            return "QuikCutz_{0}".format(cut_counter)

        def next_image_label():
            nonlocal image_counter
            image_counter += 1
            return "Image_{0}".format(image_counter)

        # ---- duplicate artwork into every cell ----
        # When keeping the originals, every cell - including the
        # original's own - gets a fresh duplicate, so the original
        # selected artwork is left completely untouched. Otherwise, the
        # original artwork itself is reused as the original's cell (and
        # later moved into the Images group), so it isn't duplicated a
        # second time.
        if content_nodes:
            for col in range(W):
                for row in range(H):
                    if not keep_originals and col == col_of_orig and row == row_of_orig:
                        continue
                    dx = (grid_left + col * width) - orig_left
                    dy = (grid_top + row * height) - orig_top
                    images_target = get_images_group()
                    for content in content_nodes:
                        clone = self.duplicate_content(content, dx, dy, images_target)
                        label = next_image_label()
                        clone.set('inkscape:label', label)
                        clone.set('id', self.svg.get_unique_id(label.lower()))

        # The whole path starts at the grid's true top-left corner.
        anchor_x_left = True
        anchor_y_top = True

        lines_h = self.build_horizontal_lines(grid_left, grid_top, total_w, total_h, H,
                                               start_top=anchor_y_top, start_left=anchor_x_left)

        if add_connectors:
            # Path two must START at the SAME grid corner path one ENDS
            # at (not the build-direction anchor) - which depends on the
            # row count's parity, since direction alternates every row.
            last_row_is_left_to_right = anchor_x_left if H % 2 == 0 else not anchor_x_left
            h_end_at_left = not last_row_is_left_to_right
            h_end_at_bottom = anchor_y_top
            lines_v = self.build_vertical_lines(grid_left, grid_top, total_w, total_h, W,
                                                 start_left=h_end_at_left, start_bottom=h_end_at_bottom)
        else:
            lines_v = self.build_vertical_lines(grid_left, grid_top, total_w, total_h, W,
                                                 start_left=anchor_x_left, start_bottom=not anchor_y_top)

        style = cut_node.get('style') or "fill:none;stroke:#000000;stroke-width:1"
        sw = max(min(width, height) * 0.01, 0.2)
        radius = 0.9 * ext_len if (add_connectors and add_extensions) else 0

        if test_mode:
            subpaths_h = self.build_subpaths(lines_h, ext_len, add_extensions, add_connectors)
            subpaths_v = self.build_subpaths(lines_v, ext_len, add_extensions, add_connectors)
            test_target = get_cut_group()
            for sp in subpaths_h:
                self.emit_path(test_target, self.points_to_dpath(sp['points'], sp['is_connector'], radius, ext_len),
                                stroke_color="#ff0000", stroke_width=sw, label="Quik Cutz TEST - path one (red)")
            for sp in subpaths_v:
                self.emit_path(test_target, self.points_to_dpath(sp['points'], sp['is_connector'], radius, ext_len),
                                stroke_color="#0000ff", stroke_width=sw, label="Quik Cutz TEST - path two (blue)")
            if join_h_v and subpaths_h and subpaths_v:
                _, square_indices = self.join_h_v_bracket(subpaths_h[0], subpaths_v[0])
                h_last = subpaths_h[0]['points'][-1]
                v_first = subpaths_v[0]['points'][0]
                if h_last != v_first:
                    bend = (h_last[0], v_first[1])
                    bracket_d = "M {:.6f},{:.6f} L {:.6f},{:.6f} L {:.6f},{:.6f}".format(
                        h_last[0], h_last[1], bend[0], bend[1], v_first[0], v_first[1])
                    self.emit_path(test_target, bracket_d, stroke_color="#00c800", stroke_width=sw,
                                    label="Quik Cutz TEST - H-to-V bracket join (green)")
            if add_outer_box:
                o = ext_len if add_extensions else 0
                box_d = ("M {:.6f},{:.6f} L {:.6f},{:.6f} L {:.6f},{:.6f} L {:.6f},{:.6f} Z".format(
                    grid_left - o, grid_top - o, grid_left + total_w + o, grid_top - o,
                    grid_left + total_w + o, grid_top + total_h + o, grid_left - o, grid_top + total_h + o))
                self.emit_path(test_target, box_d, stroke_color="#00c800", stroke_width=sw,
                                label="Quik Cutz TEST - outer box (green)")
        else:
            subpaths_h = self.build_subpaths(lines_h, ext_len, add_extensions, add_connectors)
            subpaths_v = self.build_subpaths(lines_v, ext_len, add_extensions, add_connectors)

            if join_h_v and subpaths_h and subpaths_v:
                # Bridges path one's subpath to path two's subpath with
                # a right-angle bracket (never a diagonal) - see
                # join_h_v_bracket's docstring. square_indices keeps
                # every vertex the bracket introduces sharp, regardless
                # of the radius setting, even while other internal
                # connector corners elsewhere still get filleted
                # normally.
                combined, square_indices = self.join_h_v_bracket(subpaths_h[0], subpaths_v[0])
                d_combined = self.points_to_dpath(combined['points'], combined['is_connector'], radius, ext_len,
                                                   square_indices=square_indices)
                self.emit_path(get_cut_group(), d_combined, style=style, label=next_cut_label())
            else:
                if lines_h:
                    d_h = " ".join(self.points_to_dpath(sp['points'], sp['is_connector'], radius, ext_len)
                                   for sp in subpaths_h)
                    self.emit_path(get_cut_group(), d_h, style=style, label=next_cut_label())
                if lines_v:
                    d_v = " ".join(self.points_to_dpath(sp['points'], sp['is_connector'], radius, ext_len)
                                   for sp in subpaths_v)
                    self.emit_path(get_cut_group(), d_v, style=style, label=next_cut_label())
            if add_outer_box:
                o = ext_len if add_extensions else 0
                box_d = ("M {:.6f},{:.6f} L {:.6f},{:.6f} L {:.6f},{:.6f} L {:.6f},{:.6f} Z".format(
                    grid_left - o, grid_top - o, grid_left + total_w + o, grid_top - o,
                    grid_left + total_w + o, grid_top + total_h + o, grid_left - o, grid_top + total_h + o))
                self.emit_path(get_cut_group(), box_d, style=style, label=next_cut_label())

        if not keep_originals:
            # Consume the original cut shape into the result...
            parent.remove(cut_node)
            # ...and fold the original artwork (which served as the
            # original's cell above, and was never duplicated for that
            # slot) into the Images group alongside its duplicates, so
            # the group contains every copy including the first.
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
        # newly generated QuikCutz / Images groups are entirely separate.

        if self.options.rasterize_images and content_nodes and images_group is not None:
            self.rasterize_group(images_group)


if __name__ == '__main__':
    QuikCutzGrid().run()
